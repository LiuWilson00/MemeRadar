#!/usr/bin/env python
"""從 memes.tw 批次爬梗圖進庫（解耦匯入：先入庫、標註交背景 worker 慢慢跑）。

memes.tw robots.txt 允許全站爬取；本腳本用其公開 JSON API（/wtf/api），禮貌節流。
去重（sha256 + phash）、濾非梗圖 / NSFW、向量化 都沿用既有 pipeline 與背景標註 worker
——所以爬進來的圖要等標註完才會出現在探索/推薦。

用法（在 repo 根目錄；要寫進正式庫需設好 DATABASE_URL + R2 憑證於環境變數/.env）：
    python scripts/crawl_memes_tw.py --count 2000              # 全站最新 2000 張
    python scripts/crawl_memes_tw.py --count 500 --contests 11,8,53   # 加指定主題
    python scripts/crawl_memes_tw.py --count 20 --dry-run      # 只抓+對映、不下載不入庫
    python scripts/crawl_memes_tw.py --count 2000 --ignore-watermark --local-annotate  # 回填舊圖

水位（各來源獨立）記在 crawl_state，重跑只抓更新的（增量）；
回填舊圖用 --ignore-watermark（從最新往回爬，去重擋掉已入庫的）。
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


def _import_one(conn, cand, content: bytes, data_dir, *, vlm=None, embedder=None) -> str:
    """單張匯入：去重 → import_image_bytes（帶 attribution）→ 落 R2/DB → 登記 phash。

    給了 vlm+embedder（--local-annotate）就順便本地標註 + 向量，濾掉非梗圖/NSFW、自動上架
    → 進庫直接可用；否則解耦匯入（標註交正式站背景 worker）。
    """
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

    if vlm is not None:  # 本地完整處理：標註 + 濾 + 向量 + 上架（同上傳端邏輯）
        from memeradar.shared import repository as repo
        from memeradar.understanding.annotator import annotate_meme
        from memeradar.understanding.embedding import embed_pending_memes

        # 若 annotate_meme 拋錯（如退避後仍 529）：例外往上拋、該張計入失敗，但梗圖已是
        # active 只是沒標註 → 正式站背景 worker（list_active_unannotated）會用 qwen 補標註+
        # 向量自癒，不會遺失；故此處不刻意降級狀態。
        annotation = annotate_meme(conn, vlm, meme, data_dir=data_dir)
        if annotation is None or not annotation.is_meme or annotation.nsfw:
            repo.set_status(conn, meme.meme_id, "removed")
            return "filtered"
        repo.set_status(conn, meme.meme_id, "active")
        if embedder is not None:
            embed_pending_memes(conn, embedder)
    return "imported"


def _split_new(cands, imported_urls) -> tuple[list, int]:
    """依「下載前預先去重」把候選分成待處理與已入庫（後者計 duplicate）。

    待處理者先把 post_url 記進 imported_urls，避免同批跨 adapter 重複送。
    """
    new = []
    dup = 0
    for cand in cands:
        if cand.post_url and cand.post_url in imported_urls:
            dup += 1
        else:
            new.append(cand)
            if cand.post_url:
                imported_urls.add(cand.post_url)
    return new, dup


def _tally(result: str, counts: dict) -> None:
    counts["imported"] += result == "imported"
    counts["duplicate"] += result == "duplicate"
    counts["filtered"] += result == "filtered"
    counts["failed"] += result not in ("imported", "duplicate", "filtered")


def _import_worker(cand, vlm, data_dir):
    """並行工作單元：自開 conn、下載、匯入+標註。

    embedder=None → 不在工作緒 embed（embed_pending_memes 會掃全庫，多緒並呼易衝突）；
    向量化改由主緒批次做（見 _process_parallel）。repo 每寫即 commit，故各緒獨立持久化。
    """
    from memeradar.shared.db import connect

    conn_t = connect()
    try:
        content = _download(cand.images[0]["url"])
        return _import_one(conn_t, cand, content, data_dir, vlm=vlm, embedder=None)
    finally:
        conn_t.close()


def _process_serial(cands, imported_urls, *, conn, data_dir, vlm, embedder, img_delay) -> dict:
    counts = {"imported": 0, "duplicate": 0, "filtered": 0, "failed": 0}
    for i, cand in enumerate(cands, 1):
        if cand.post_url and cand.post_url in imported_urls:
            counts["duplicate"] += 1
        else:
            try:
                content = _download(cand.images[0]["url"])
                result = _import_one(conn, cand, content, data_dir, vlm=vlm, embedder=embedder)
            except Exception as exc:  # noqa: BLE001 單張失敗不中斷整批
                counts["failed"] += 1
                if counts["failed"] <= 5:
                    print(f"  ✗ {cand.post_id}：{exc!r}")
            else:
                _tally(result, counts)
                if result != "duplicate" and cand.post_url:
                    imported_urls.add(cand.post_url)
            time.sleep(img_delay)
        if i % 25 == 0:
            _print_progress(i, len(cands), counts)
    return counts


def _process_parallel(cands, imported_urls, *, conn, data_dir, vlm, embedder, workers) -> dict:
    """並行下載+標註（慢在 VLM），DB 寫入由各工作緒自有 conn 完成；向量化主緒批次補。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from memeradar.understanding.embedding import embed_pending_memes

    new, dup = _split_new(cands, imported_urls)
    counts = {"imported": 0, "duplicate": dup, "filtered": 0, "failed": 0}
    total = len(cands)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_import_worker, c, vlm, data_dir): c for c in new}
        for j, fut in enumerate(as_completed(futs), 1):
            cand = futs[fut]
            try:
                result = fut.result()
            except Exception as exc:  # noqa: BLE001 單張失敗不中斷整批
                counts["failed"] += 1
                if counts["failed"] <= 5:
                    print(f"  ✗ {cand.post_id}：{exc!r}")
                continue
            _tally(result, counts)
            if j % 25 == 0:
                if embedder is not None:
                    embed_pending_memes(conn, embedder)
                _print_progress(sum(counts.values()), total, counts)
    if embedder is not None:
        embed_pending_memes(conn, embedder)  # 收尾：把工作緒設 active 卻還沒向量化的補上
    return counts


def _print_progress(done: int, total: int, c: dict) -> None:
    print(
        f"  …{done}/{total}（入庫 {c['imported']}/重複 {c['duplicate']}/"
        f"濾除 {c['filtered']}/失敗 {c['failed']}）",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="從 memes.tw 批次爬梗圖進庫（解耦匯入）")
    parser.add_argument("--count", type=int, default=2000, help="每個來源最多抓幾張")
    parser.add_argument("--contests", default="", help="逗號分隔的 contest id（除全站最新外）")
    parser.add_argument("--delay", type=float, default=1.0, help="每頁 API 間隔秒（禮貌節流）")
    parser.add_argument("--img-delay", type=float, default=0.15, help="每張圖下載間隔秒")
    parser.add_argument("--dry-run", action="store_true", help="只抓+對映、不下載不入庫")
    parser.add_argument("--local-annotate", action="store_true",
                        help="本地用 Claude 標註+向量後再入庫（需 ANTHROPIC_API_KEY）")
    parser.add_argument("--model", default="claude-haiku-4-5", help="local-annotate 的 Claude 模型")
    parser.add_argument("--ignore-watermark", action="store_true",
                        help="忽略水位、從最新往回爬（回填舊圖用；去重擋掉已入庫的）")
    parser.add_argument("--workers", type=int, default=1,
                        help="並行標註緒數（local-annotate 時 VLM 為瓶頸，>1 大幅加速）")
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

    vlm = embedder = None
    if args.local_annotate:
        if not settings.anthropic_api_key:
            print("✗ --local-annotate 需要 .env 的 ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        import anthropic

        from memeradar.understanding.claude_vlm import ClaudeVlm
        from memeradar.understanding.embedding import get_embedder

        # max_retries：Anthropic 偶發 529 overloaded，靠 SDK 內建指數退避撐過瞬間過載，
        # 免得標註失敗把梗圖留在 active 卻沒標註/沒向量（見 _import_one 註解）。
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=8)
        vlm = ClaudeVlm(client, model=args.model)
        embedder = get_embedder(settings.embedding_backend)
        print(f"🧠 本地完整處理：Claude（{args.model}）標註 + {embedder.model_id} 向量。")

    conn = connect()
    migrate(conn)
    totals = {"imported": 0, "duplicate": 0, "filtered": 0, "failed": 0}
    # 下載前預先去重：已入庫的 post_url 先跳過、不白下載（重跑/回填時最省時間的關鍵；
    # sha256/phash 仍是最終去重保證）。所有 memes_tw 候選都以 platform=memes_tw 入庫。
    imported_urls = repo.imported_source_urls(conn, "memes_tw")
    try:
        for adapter in adapters:
            before = None if args.ignore_watermark else repo.get_watermark(conn, adapter.name)
            cands, after = adapter.fetch(before)
            mode = f"（{args.workers} 緒並行）" if args.workers > 1 and vlm else ""
            print(f"\n[{adapter.name}] 抓到 {len(cands)} 張"
                  f"（水位 {before} → {after}）{mode}，匯入中…")
            if args.workers > 1 and vlm is not None:
                counts = _process_parallel(
                    cands, imported_urls, conn=conn, data_dir=settings.memeradar_data_dir,
                    vlm=vlm, embedder=embedder, workers=args.workers,
                )
            else:
                counts = _process_serial(
                    cands, imported_urls, conn=conn, data_dir=settings.memeradar_data_dir,
                    vlm=vlm, embedder=embedder, img_delay=args.img_delay,
                )
            if after:
                repo.set_watermark(conn, adapter.name, after)
            print(f"[{adapter.name}] 完成：入庫 {counts['imported']} / "
                  f"重複 {counts['duplicate']} / 濾除 {counts['filtered']} / "
                  f"失敗 {counts['failed']}")
            for k, v in counts.items():
                totals[k] += v
    finally:
        conn.close()

    print(f"\n✅ 全部完成：入庫 {totals['imported']} / 重複 {totals['duplicate']} / "
          f"濾除 {totals['filtered']} / 失敗 {totals['failed']}")
    if not args.local_annotate:
        print("標註由正式站背景 worker 慢慢處理；標註完才會出現在探索/推薦。")
    else:
        print("已本地標註+向量完成，入庫即可用（active 者已進探索/推薦池）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
