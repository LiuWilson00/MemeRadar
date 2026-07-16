#!/usr/bin/env python
"""補標註：把 active 卻沒標註的梗圖用 Claude 重標註 + 向量。

修 529 過載等造成的「已入庫、卻漏標註/漏向量」殘留（那種梗圖 active 但搜不到、無 metadata）。
沿用 annotate_meme（標註→濾非梗圖/NSFW→removed）與 embed_pending_memes，與匯入端一致。

    python scripts/reannotate_active_unannotated.py --platform memes_tw
    python scripts/reannotate_active_unannotated.py            # 全部來源
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _unannotated_ids(conn, platform: str | None, limit: int) -> list[str]:
    """active 且沒標註的 meme_id（可選限定來源），舊到新。"""
    if platform:
        sql = """
            SELECT DISTINCT m.meme_id FROM memes m
            JOIN meme_sources s ON s.meme_id = m.meme_id AND s.platform = %s
            LEFT JOIN meme_annotations a ON a.meme_id = m.meme_id
            WHERE a.meme_id IS NULL AND m.status = 'active'
            ORDER BY m.meme_id LIMIT %s
        """
        params: tuple = (platform, limit)
    else:
        sql = """
            SELECT m.meme_id FROM memes m
            LEFT JOIN meme_annotations a ON a.meme_id = m.meme_id
            WHERE a.meme_id IS NULL AND m.status = 'active'
            ORDER BY m.meme_id LIMIT %s
        """
        params = (limit,)
    return [r["meme_id"] for r in conn.execute(sql, params).fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claude 補標註 active 卻漏標的梗圖")
    parser.add_argument("--platform", default=None, help="只補某來源（如 memes_tw）；預設全部")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--model", default="claude-haiku-4-5")
    args = parser.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    import anthropic

    from memeradar.shared import repository as repo
    from memeradar.shared.config import get_settings
    from memeradar.shared.db import connect
    from memeradar.understanding.annotator import annotate_meme
    from memeradar.understanding.claude_vlm import ClaudeVlm
    from memeradar.understanding.embedding import embed_pending_memes, get_embedder

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("✗ 需要 .env 的 ANTHROPIC_API_KEY", file=sys.stderr)
        return 1
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=8)
    vlm = ClaudeVlm(client, model=args.model)
    embedder = get_embedder(settings.embedding_backend)

    conn = connect()
    try:
        ids = _unannotated_ids(conn, args.platform, args.limit)
        scope = args.platform or "全部來源"
        print(f"🩹 {scope}：active 卻漏標註 {len(ids)} 張，用 Claude（{args.model}）補…")
        done = removed = failed = 0
        for i, mid in enumerate(ids, 1):
            meme = repo.get_meme(conn, mid)
            if meme is None:
                continue
            try:
                ann = annotate_meme(conn, vlm, meme, data_dir=settings.memeradar_data_dir)
            except Exception as exc:  # noqa: BLE001 單張失敗不中斷
                failed += 1
                if failed <= 5:
                    print(f"  ✗ {mid}：{exc!r}")
                continue
            if ann is None or not ann.is_meme or ann.nsfw:
                repo.set_status(conn, mid, "removed")
                removed += 1
            else:
                embed_pending_memes(conn, embedder)
                done += 1
            if i % 20 == 0:
                print(f"  …{i}/{len(ids)}（補好 {done}/濾除 {removed}/失敗 {failed}）", flush=True)
            time.sleep(0.05)
        print(f"\n✅ 完成：補好 {done} / 濾除 {removed} / 失敗 {failed}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
