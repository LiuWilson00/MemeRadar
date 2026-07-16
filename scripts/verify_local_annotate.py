#!/usr/bin/env python
"""驗證「本地用 Claude 標註 memes.tw 梗圖」的想法：抓幾張真圖 → Claude 標註 → 印結果+耗時。

不寫任何 DB。用 .env 的 ANTHROPIC_API_KEY。預設 haiku（成本/速度取向，適合大量）。
    python scripts/verify_local_annotate.py --count 5
    python scripts/verify_local_annotate.py --count 3 --model claude-sonnet-5
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memeradar.ingestion.memes_tw import MemesTwAdapter  # noqa: E402
from memeradar.understanding.annotator import (  # noqa: E402
    build_system_prompt,
    build_user_text,
    parse_annotation,
)
from memeradar.understanding.claude_vlm import DEFAULT_MODEL, ClaudeVlm  # noqa: E402


def _download(url: str) -> bytes:
    import requests

    r = requests.get(url, headers={"User-Agent": "MemeRadar/1.0"}, timeout=20)
    r.raise_for_status()
    return r.content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="驗證本地 Claude 標註 memes.tw 梗圖")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    from memeradar.shared.config import get_settings

    key = get_settings().anthropic_api_key
    if not key:
        print("✗ .env 缺 ANTHROPIC_API_KEY", file=sys.stderr)
        return 1
    import anthropic

    vlm = ClaudeVlm(anthropic.Anthropic(api_key=key), model=args.model)
    system = build_system_prompt()
    user_text = build_user_text(None)

    cands, _ = MemesTwAdapter(max_items=args.count, sleep=lambda s: None).fetch(None)
    print(f"用 {args.model} 標註 {len(cands)} 張 memes.tw 梗圖…\n")
    t_total = 0.0
    n_ok = n_meme = 0
    for i, c in enumerate(cands, 1):
        try:
            img = _download(c.images[0]["url"])
        except Exception as e:  # noqa: BLE001
            print(f"[{i}] 下載失敗：{e!r}")
            continue
        media = "image/jpeg" if img[:3] == b"\xff\xd8\xff" else "image/png"
        b64 = base64.standard_b64encode(img).decode("ascii")
        t0 = time.time()
        raw = vlm.annotate(b64, media, system, user_text)
        dt = time.time() - t0
        t_total += dt
        ann = parse_annotation(raw)
        print(f"── [{i}] {c.post_title[:30]}  ({dt:.1f}s)")
        if ann is None:
            print(f"     ✗ 解析失敗，原始回覆：{raw[:160]}")
            continue
        n_ok += 1
        n_meme += ann.is_meme
        print(f"     is_meme={ann.is_meme} nsfw={ann.nsfw} conf={ann.confidence}")
        print(f"     OCR：{ann.ocr_text[:60]}")
        print(f"     franchise={ann.franchise} emotions={ann.emotions}")
        print(f"     categories={ann.categories} usage={ann.usage_hints[:2]}")
    if n_ok:
        print(f"\n✅ {n_ok}/{len(cands)} 標註成功（其中 {n_meme} 判為梗圖），"
              f"平均 {t_total / max(n_ok, 1):.1f}s/張")
    print("若品質OK：把爬蟲改成用 Claude 本地標註+向量後再入庫，進正式庫就直接可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
