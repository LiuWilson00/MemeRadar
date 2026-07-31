#!/usr/bin/env python
"""用真 NVIDIA key 打 OCR，印出原始回應與我方 parser 的結果。

快速模式的 OCR client（memeradar/understanding/ocr.py）單元測試用 stub 驗證解析
邏輯，*無法*證明真實 API 的欄位命名。此腳本用真 key 打一次，讓你確認
``_extract_text`` 有正確從真實回應取出文字（stub 可能掩蓋 API 漂移）。

2026-08-01：原本還會打 NV-CLIP 做零樣本分類，但 memeradar/understanding/nvclip.py
早已刪除，這支腳本因此 import 失敗、根本跑不起來（ruff 把解析不到的
memeradar.understanding.nvclip 當第三方套件排序，I001 就是這麼冒出來的）。
已移除 NV-CLIP 部分，只保留仍在服役的 OCR。

**不會寫入任何資料**（純唯讀 API 呼叫）。

用法（在 repo 根目錄）：
    # 金鑰可放 .env（NVIDIA_API_KEYS=...）或環境變數
    python scripts/smoke_ocr.py --image path/to/screenshot.png

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
from memeradar.understanding.ocr import (  # noqa: E402
    DEFAULT_URL,
    NvidiaOcr,
    _extract_text,
    _media_type,
)


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
    parser = argparse.ArgumentParser(description="OCR 真 API smoke test（不寫庫）")
    parser.add_argument("--image", required=True, type=Path, help="測試用截圖路徑")
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
    try:
        _ocr_raw(image_bytes, keys[0])
        print("\n→ NvidiaOcr.ocr() 封裝結果：")
        print(repr(NvidiaOcr(keys).ocr(image_bytes)))
    except Exception as exc:  # noqa: BLE001 smoke test：任何錯誤都印出即可
        print(f"✗ OCR 失敗：{exc!r}")

    print(
        "\n✅ 完成。若 OCR 的『_extract_text 取出』是空的或亂碼，"
        "把上面的『原始回應』貼回給我，我照真實形狀調 parser。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
