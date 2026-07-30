"""送進 VLM 前的圖片預處理。

2026-07-30 實測（真 key、真梗圖）：VLM 呼叫延遲與圖片尺寸強相關——同一張圖
600×600 約 3.2s、1800×1800 則 6.7～12.9s。而線上路徑（對手梗圖 / 截圖解析）
單次呼叫有秒級預算，手機拍的原圖動輒 3000×4000，很容易直接撞逾時。

VLM 讀梗圖文字並不需要原始解析度（實測縮到 1024 仍能完整讀出圖中中文），
故一律先縮再送。OCR 路徑另有自己較寬鬆的上限（見 understanding/ocr.py）。
"""

from __future__ import annotations

import io

# 實測 1024 是延遲與可讀性的平衡點（仍讀得出細項文字，體積約 184KB）
VLM_MAX_SIDE = 1024


def downscale_for_vlm(image_bytes: bytes, *, max_side: int = VLM_MAX_SIDE) -> bytes:
    """長邊超過 ``max_side`` 就等比縮小並重壓 JPEG；已夠小 / 非圖片則原樣回。

    ⚠️ 有縮圖時**輸出格式會變成 JPEG**，呼叫端若要組 data URI，media type 必須在
    縮圖**之後**判定，否則會出現「宣稱 image/png 卻是 JPEG 位元組」的錯誤標示。

    縮圖失敗不擋主流程（大不了原圖送出去，由呼叫端的逾時處理）。
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
        longest = max(width, height)
        if longest <= max_side:
            return image_bytes
        scale = max_side / longest
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        img.draft("RGB", new_size)  # JPEG 解碼時即降解析，省記憶體
        img = img.convert("RGB").resize(new_size)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:  # noqa: BLE001 縮圖失敗不可擋住主流程
        return image_bytes
