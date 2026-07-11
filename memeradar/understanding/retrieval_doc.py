"""檢索文件組裝（docs/03 §3.1）。

把標註欄位按固定模板串成一段文字，作為 text embedding 的輸入。
usage_hints 放最前面（權重最高）；模板必須決定性，模板改版 = 全量重建索引，
因此以 ``RETRIEVAL_DOC_VERSION`` 版本化，並隨 embedding 簽名一起入庫。
"""

from __future__ import annotations

from memeradar.shared.models import MemeAnnotation

RETRIEVAL_DOC_VERSION = "doc-v1"


def build_retrieval_document(annotation: MemeAnnotation) -> str:
    lines: list[str] = [f"用途：{hint}" for hint in annotation.usage_hints]
    lines.append(f"情緒：{'、'.join(annotation.emotions)}")
    if annotation.ocr_text:
        lines.append(f"圖中文字：{annotation.ocr_text}")
    lines.append(f"畫面：{annotation.description}")

    tail = f"角色：{'、'.join(annotation.characters) if annotation.characters else '無'}"
    if annotation.franchise:
        tail += f"；出處：{annotation.franchise}"
    lines.append(tail)

    return "\n".join(lines)
