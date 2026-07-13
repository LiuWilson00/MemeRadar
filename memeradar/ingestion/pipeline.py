"""P3-5 全自動資料管線：抓取 → 過濾 → 去重 → 標註 → 佇列裁決 → 向量化。

一次執行完成 docs/01 §2 的整條離線批次流程；由系統排程（cron / Windows
工作排程器）定期觸發 CLI：``python -m memeradar.ingestion.pipeline``。

設計要點：
- 單來源失敗不阻塞其他來源；連續 3 次失敗發告警（crawl_health，docs/02 §6）。
- 下載失敗重試 2 次後跳過並記錄（docs/02 §7）。
- 批次報表數字對帳（P3-5 驗收）：處理過的圖片數 = 入庫 + 重複吸收 +
  圖片層淘汰 + 失敗。
- 標註目前走同步標註器；Batch API 半價版（P1-2）為後續成本優化。
"""

from __future__ import annotations

import io
import sqlite3
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from memeradar.ingestion.base import SourceAdapter
from memeradar.ingestion.dedup import (
    Deduplicator,
    absorb_duplicate,
    maybe_upgrade_image,
    resolve_pending_reviews,
)
from memeradar.ingestion.rules import RuleConfig, check_candidate, check_image, check_image_url
from memeradar.ingestion.seed_import import import_image_bytes
from memeradar.shared import repository as repo
from memeradar.shared.models import MemeSource, new_id
from memeradar.understanding.annotator import annotate_meme
from memeradar.understanding.embedding import Embedder, embed_pending_memes

ALERT_FAILURE_THRESHOLD = 3
DOWNLOAD_RETRIES = 2
DOWNLOAD_DELAY_SECONDS = 2.0


@dataclass
class PipelineReport:
    fetched: int = 0  # 通過水位的候選貼文數
    images_seen: int = 0  # 進入圖片處理的張數（候選層通過者）
    imported: int = 0
    duplicates: int = 0  # SHA256 吸收
    queued_review: int = 0  # 已入庫且進裁決佇列（包含於 imported）
    rejected: dict[str, int] = field(default_factory=dict)  # 各原因（含候選層）
    rejected_images: int = 0  # 僅圖片層淘汰（對帳用）
    failures: int = 0  # 下載失敗 / 壞圖
    annotated: int = 0
    annotation_refused: int = 0
    review_resolution: dict[str, int] = field(default_factory=dict)
    embedded: int = 0
    adapter_errors: dict[str, str] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def _reject(self, reason: str, *, image_level: bool) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1
        if image_level:
            self.rejected_images += 1

    def reconciles(self) -> bool:
        """對帳（P3-5 驗收）：圖片去向必須全數可解釋。"""
        return self.images_seen == (
            self.imported + self.duplicates + self.rejected_images + self.failures
        )


def default_image_fetcher() -> Callable[[str], bytes]:
    import httpx

    http = httpx.Client(
        headers={"User-Agent": "MemeRadar/0.1 (meme research tool)"},
        timeout=30,
        follow_redirects=True,
    )

    def fetch(url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(DOWNLOAD_RETRIES + 1):
            if attempt:
                time.sleep(DOWNLOAD_DELAY_SECONDS)
            try:
                response = http.get(url)
                response.raise_for_status()
                return response.content
            except Exception as exc:  # noqa: BLE001 — 重試後由呼叫端記錄
                last_error = exc
        raise last_error  # type: ignore[misc]

    return fetch


def run_pipeline(
    conn: sqlite3.Connection,
    adapters: Sequence[SourceAdapter],
    *,
    data_dir: Path,
    vlm,
    embedder: Embedder,
    image_embedder=None,
    rules: RuleConfig | None = None,
    image_fetcher: Callable[[str], bytes] | None = None,
) -> PipelineReport:
    rules = rules or RuleConfig()
    fetch_image = image_fetcher or default_image_fetcher()
    dedup = Deduplicator(conn, image_embedder=image_embedder)
    report = PipelineReport()

    # ── 抓取 → 過濾 → 去重 → 入庫 ─────────────────────────────────
    for adapter in adapters:
        old_watermark = repo.get_watermark(conn, adapter.name)
        try:
            candidates, new_watermark = adapter.fetch(old_watermark)
        except Exception as exc:  # noqa: BLE001 — 單來源失敗不阻塞（docs/02 §6）
            report.adapter_errors[adapter.name] = str(exc)
            count = repo.record_crawl_failure(conn, adapter.name, str(exc))
            if count >= ALERT_FAILURE_THRESHOLD:
                report.alerts.append(
                    f"來源 {adapter.name} 連續失敗 {count} 次（可能改版或被擋）：{exc}"
                )
            continue
        repo.reset_crawl_failures(conn, adapter.name)
        report.fetched += len(candidates)

        for candidate in candidates:
            reason = check_candidate(candidate, rules)
            if reason is not None:
                report._reject(reason, image_level=False)
                continue

            for image in candidate.images:
                url = image["url"]
                report.images_seen += 1

                reason = check_image_url(url, rules)
                if reason is not None:
                    report._reject(reason, image_level=True)
                    continue

                try:
                    content = fetch_image(url)
                    with Image.open(io.BytesIO(content)) as img:
                        width, height = img.size
                except Exception as exc:  # noqa: BLE001 — 已含重試（docs/02 §7）
                    report.failures += 1
                    report.warnings.append(f"下載或讀取失敗，已跳過：{url}（{exc}）")
                    continue

                reason = check_image(width, height, rules)
                if reason is not None:
                    report._reject(reason, image_level=True)
                    continue

                result = dedup.check(content)
                if result.decision == "duplicate":
                    absorb_duplicate(
                        conn,
                        result.matched_meme_id,
                        MemeSource(
                            source_id=new_id("s"),
                            meme_id=result.matched_meme_id,
                            platform=candidate.platform,
                            post_url=candidate.post_url,
                            post_title=candidate.post_title,
                            top_comments=candidate.top_comments,
                            upvotes=candidate.upvotes,
                            posted_at=candidate.posted_at,
                        ),
                    )
                    maybe_upgrade_image(
                        conn, result.matched_meme_id, content, data_dir=data_dir
                    )
                    report.duplicates += 1
                    continue

                meme, status = import_image_bytes(
                    conn,
                    content,
                    data_dir=data_dir,
                    platform=candidate.platform,
                    source_title=candidate.post_title,
                    post_url=candidate.post_url,
                    top_comments=candidate.top_comments,
                    upvotes=candidate.upvotes,
                    posted_at=candidate.posted_at,
                )
                if status != "imported":
                    report.failures += 1
                    report.warnings.append(f"入庫失敗（{status}）：{url}")
                    continue
                dedup.register(meme, content)
                report.imported += 1

                if result.decision == "review":
                    repo.add_dedup_review(
                        conn,
                        meme_id=meme.meme_id,
                        matched_meme_id=result.matched_meme_id,
                        layer=result.layer,
                        score=result.score,
                    )
                    report.queued_review += 1

        if new_watermark and new_watermark != old_watermark:
            repo.set_watermark(conn, adapter.name, new_watermark)

    # ── 標註（同步版；Batch API 見 P1-2）─────────────────────────────
    for meme in repo.list_memes_missing_annotation(conn):
        try:
            annotation = annotate_meme(conn, vlm, meme, data_dir=data_dir)
        except Exception as exc:  # noqa: BLE001 — 批次不因單張中斷
            report.warnings.append(f"標註失敗：{meme.meme_id}（{exc}）")
            continue
        if annotation is None:
            report.annotation_refused += 1
        else:
            report.annotated += 1

    # ── 去重佇列裁決（需標註完成，docs/02 §4 修訂）──────────────────
    report.review_resolution = resolve_pending_reviews(conn)

    # ── 向量化 ───────────────────────────────────────────────────────
    report.embedded = embed_pending_memes(conn, embedder)

    return report


def format_report(report: PipelineReport) -> str:
    lines = [
        f"候選貼文 {report.fetched}；處理圖片 {report.images_seen}",
        f"  入庫 {report.imported}（其中進裁決佇列 {report.queued_review}）",
        f"  重複吸收 {report.duplicates}；下載/讀取失敗 {report.failures}",
    ]
    for reason, count in sorted(report.rejected.items()):
        lines.append(f"  規則淘汰［{reason}］{count}")
    lines.append(
        f"標註 {report.annotated}（拒答 {report.annotation_refused}）；"
        f"佇列裁決 {report.review_resolution}；向量化 {report.embedded}"
    )
    lines.append(f"對帳：{'✓ 一致' if report.reconciles() else '✗ 不一致（需檢查）'}")
    for name, error in report.adapter_errors.items():
        lines.append(f"[來源錯誤] {name}: {error}")
    for alert in report.alerts:
        lines.append(f"[告警] {alert}")
    for warning in report.warnings[:10]:
        lines.append(f"[警告] {warning}")
    if len(report.warnings) > 10:
        lines.append(f"[警告] …另有 {len(report.warnings) - 10} 則")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse

    from memeradar.ingestion.reddit import RedditAdapter, build_client
    from memeradar.shared.config import get_settings
    from memeradar.shared.db import connect, migrate
    from memeradar.understanding.embedding import DEFAULT_BACKEND, get_embedder

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="全自動資料管線（供 cron / 工作排程器定期觸發）"
    )
    parser.add_argument("--client", choices=["public", "praw"], default="praw")
    parser.add_argument("--subreddit", action="append", default=[])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--no-clip", action="store_true", help="跳過 CLIP 去重層（僅 L1/L2）")
    args = parser.parse_args(argv)

    from memeradar.understanding.annotator import build_default_vlm

    settings = get_settings()
    vlm = build_default_vlm()
    adapters = [
        RedditAdapter(
            build_client(args.client),
            subreddits=args.subreddit or None,
            listing_limit=args.limit,
        )
    ]
    image_embedder = None
    if not args.no_clip:
        from memeradar.ingestion.dedup import ClipImageEmbedder

        image_embedder = ClipImageEmbedder()

    conn = connect()
    try:
        migrate(conn)
        report = run_pipeline(
            conn,
            adapters,
            data_dir=settings.memeradar_data_dir,
            vlm=vlm,
            embedder=get_embedder(DEFAULT_BACKEND),
            image_embedder=image_embedder,
        )
    finally:
        conn.close()
    print(format_report(report))


if __name__ == "__main__":
    main()
