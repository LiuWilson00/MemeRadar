#!/usr/bin/env python
"""用真 NVIDIA key 打 PaddleOCR + NV-CLIP，印出原始回應與我方 parser 的結果。

快速模式的兩個 client（memeradar/understanding/ocr.py、nvclip.py）單元測試用
stub 驗證解析邏輯，*無法*證明真實 API 的欄位命名。此腳本用真 key 打一次，讓你
確認 ``_extract_text`` 有正確從真實回應取出文字（見 memory：stub 可能掩蓋 API 漂移）。

**不會寫入任何資料**（純唯讀 API 呼叫）。

用法（在 repo 根目錄）：
    # 金鑰可放 .env（NVIDIA_API_KEYS=...）或環境變數
    python scripts/smoke_ocr_nvclip.py --image path/to/screenshot.png

若最後 OCR 的「_extract_text 取出」是空的或亂碼，把印出的「原始回應」貼回來，
我就能照真實形狀調整 parser。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memeradar.shared.config import get_settings  # noqa: E402
from memeradar.understanding.nvclip import NvClip, ZeroShotClassifier  # noqa: E402
from memeradar.understanding.ocr import (  # noqa: E402
    DEFAULT_URL,
    NvidiaOcr,
    _extract_text,
    _media_type,
)

# 沒字圖的情緒/類別零樣本候選（示範用；正式跑用 taxonomy 全集）
LABELS = [
    "生氣", "開心", "無奈", "尷尬", "驚訝", "難過",
    "得意", "無言", "讚", "問號", "害怕", "翻白眼",
]


def _ocr_raw(image_bytes: bytes, key: str) -> None:
    import requests

    media = _media_type(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {"input": [{"type": "image_url", "url": f"data:{media};base64,{b64}"}]}
    resp = requests.post(
        DEFAULT_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    print(f"HTTP {resp.status_code}  ({DEFAULT_URL})")
    try:
        raw = resp.json()
    except ValueError:
        print("回應非 JSON：", resp.text[:800])
        return
    print("原始回應（前 2500 字）：")
    print(json.dumps(raw, ensure_ascii=False, indent=2)[:2500])
    print("\n→ 我方 _extract_text 取出：")
    print(repr(_extract_text(raw)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PaddleOCR + NV-CLIP 真 API smoke test（不寫庫）"
    )
    parser.add_argument("--image", required=True, type=Path, help="測試用截圖路徑")
    parser.add_argument("--labels", nargs="*", default=LABELS, help="零樣本候選標籤")
    args = parser.parse_args(argv)

    keys = get_settings().nvidia_keys()
    if not keys:
        print("✗ 找不到 NVIDIA_API_KEYS（設環境變數或寫入 .env）", file=sys.stderr)
        return 1
    if not args.image.exists():
        print(f"✗ 找不到圖片：{args.image}", file=sys.stderr)
        return 1

    image_bytes = args.image.read_bytes()
    print(f"圖片：{args.image}（{len(image_bytes)} bytes，{_media_type(image_bytes)}）\n")

    print("=" * 64)
    print("① PaddleOCR")
    print("=" * 64)
    try:
        _ocr_raw(image_bytes, keys[0])
        print("\n→ NvidiaOcr.ocr() 封裝結果：")
        print(repr(NvidiaOcr(keys).ocr(image_bytes)))
    except Exception as exc:  # noqa: BLE001 smoke test：任何錯誤都印出即可
        print(f"✗ OCR 失敗：{exc!r}")

    print("\n" + "=" * 64)
    print("② NV-CLIP 零樣本情緒/類別")
    print("=" * 64)
    try:
        clip = NvClip(keys)
        img_vec = clip.embed_image(image_bytes)
        print(f"影像向量維度：{len(img_vec)}")
        top = ZeroShotClassifier(clip, args.labels).classify(image_bytes, top_k=5)
        print(f"候選 {len(args.labels)} 個 → top5：{top}")
    except Exception as exc:  # noqa: BLE001
        print(f"✗ NV-CLIP 失敗：{exc!r}")

    print(
        "\n✅ 完成。若 OCR 的『_extract_text 取出』是空的或亂碼，"
        "把上面的『原始回應』貼回給我，我照真實形狀調 parser。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
