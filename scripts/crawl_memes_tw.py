#!/usr/bin/env python
"""從 memes.tw 批次爬梗圖進庫（解耦匯入：先入庫、標註交背景 worker 慢慢跑）。

memes.tw robots.txt 允許全站爬取；本腳本用其公開 JSON API（/wtf/api），禮貌節流。
去重（sha256 + phash）、濾非梗圖 / NSFW、向量化 都沿用既有 pipeline 與背景標註 worker
——所以爬進來的圖要等標註完才會出現在探索/推薦。

用法（在 repo 根目錄；要寫進正式庫需設好 DATABASE_URL + R2 憑證於環境變數/.env）：
    python scripts/crawl_memes_tw.py --count 2000              # 全站最新 2000 張
    python scripts/crawl_memes_tw.py --count 500 --contests 11,8,53   # 加指定主題
    python scripts/crawl_memes_tw.py --count 20 --dry-run      # 只抓+對映、不下載不入庫

水位（各來源獨立）記在 crawl_state，重跑只抓更新的（增量）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memeradar.ingestion.memes_tw import MemesTwAdapter  # noqa: E402


def _download(url: str, *, timeout: float = 20.0) -> bytes:
    import requests

    resp = requests.get(
        url, headers={"User-Agent": "MemeRadar/1.0 (ingestion)"}, timeout=timeout
    )
    resp.raise_for_status()
    return resp.content


def _import_one(conn, cand, content: bytes, data_dir) -> str:
    """單張解耦匯入：去重 → import_image_bytes（帶 attribution）→ 落 R2/DB → 登記 phash。"""
    from memeradar.api.app import _persist_image
    from memeradar.ingestion.dedup import Deduplicator
    from memeradar.ingestion.seed_import import import_image_bytes

    if Deduplicator(conn).check(content).layer in ("duplicate", "review"):
        return "duplicate"
    meme, status = import_image_bytes(
        conn, content, data_dir=data_dir,
        source_title=cand.post_title, platform="memes_tw",
        post_url=cand.post_url, top_comments=cand.top_comments,
        upvotes=cand.upvotes, posted_at=cand.posted_at,
    )
    if status != "imported":
        return status
    _persist_image(conn, meme.meme_id, meme.image_uri, content)
    Deduplicator(conn).register(meme, content)
    return "imported"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="從 memes.tw 批次爬梗圖進庫（解耦匯入）")
    parser.add_argument("--count", type=int, default=2000, help="每個來源最多抓幾張")
    parser.add_argument("--contests", default="", help="逗號分隔的 contest id（除全站最新外）")
    parser.add_argument("--delay", type=float, default=1.0, help="每頁 API 間隔秒（禮貌節流）")
    parser.add_argument("--img-delay", type=float, default=0.15, help="每張圖下載間隔秒")
    parser.add_argument("--dry-run", action="store_true", help="只抓+對映、不下載不入庫")
    args = parser.parse_args(argv)

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    contests = [int(c) for c in args.contests.split(",") if c.strip()]
    adapters = [MemesTwAdapter(max_items=args.count, request_delay=args.delay)]
    adapters += [
        MemesTwAdapter(max_items=args.count, contest=c, request_delay=args.delay)
        for c in contests
    ]

    if args.dry_run:
        cands, wm = adapters[0].fetch(None)
        print(f"[dry-run] 抓到 {len(cands)} 張（新水位 {wm}）。前 5 筆：")
        for c in cands[:5]:
            print(f"  {c.post_id} | {c.post_title[:26]} | 讚 {c.upvotes} | {c.images[0]['url']}")
        return 0

    from memeradar.shared import repository as repo
    from memeradar.shared.config import get_settings
    from memeradar.shared.db import connect, migrate

    settings = get_settings()
    if not settings.r2_upload_enabled():
        print("⚠️ 未設定 R2 憑證；圖片會存進 DB image_data（確認這是你要的）。")
    conn = connect()
    migrate(conn)
    totals = {"imported": 0, "duplicate": 0, "failed": 0}
    try:
        for adapter in adapters:
            before = repo.get_watermark(conn, adapter.name)
            cands, after = adapter.fetch(before)
            print(f"\n[{adapter.name}] 抓到 {len(cands)} 張（水位 {before} → {after}），匯入中…")
            imp = dup = fail = 0
            for i, cand in enumerate(cands, 1):
                try:
                    content = _download(cand.images[0]["url"])
                    result = _import_one(conn, cand, content, settings.memeradar_data_dir)
                except Exception as exc:  # noqa: BLE001 單張失敗不中斷整批
                    fail += 1
                    if fail <= 5:
                        print(f"  ✗ {cand.post_id}：{exc!r}")
                else:
                    imp += result == "imported"
                    dup += result == "duplicate"
                    fail += result not in ("imported", "duplicate")
                if i % 50 == 0:
                    print(f"  …{i}/{len(cands)}（入庫 {imp}/重複 {dup}/失敗 {fail}）", flush=True)
                time.sleep(args.img_delay)
            if after:
                repo.set_watermark(conn, adapter.name, after)
            print(f"[{adapter.name}] 完成：入庫 {imp} / 重複 {dup} / 失敗 {fail}")
            for k, v in (("imported", imp), ("duplicate", dup), ("failed", fail)):
                totals[k] += v
    finally:
        conn.close()

    print(f"\n✅ 全部完成：入庫 {totals['imported']} / 重複 {totals['duplicate']} / "
          f"失敗 {totals['failed']}")
    print("標註由正式站背景 worker 慢慢處理（免費 VLM 限流）；標註完才會出現在探索/推薦。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
