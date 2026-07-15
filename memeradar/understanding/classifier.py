"""沒字圖分類器（快速模式用）：小 VLM 一次視覺呼叫取「情緒/類別」關鍵詞當檢索 query；
同時算影像 embedding，把 (embedding, 標籤) 存成訓練集（資料飛輪，日後訓練便宜分類器）。

複用現有 qwen VLM（帳號確定可用、實測 ~600ms、輸出乾淨關鍵詞），不引新模型/相依。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

_SYSTEM = "你是梗圖情緒/類別標註器。"
_USER = (
    "用 3 到 5 個中文詞描述這張圖表達的情緒、梗或使用情境，"
    "只回關鍵詞、用、分隔，不要句子。"
)
_SPLIT = re.compile(r"[、,，/\n\s]+")
_STRIP = "。.!！?？:：「」\"'（）()"


@dataclass
class Classification:
    labels: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    model_version: str = ""


def parse_labels(raw: str, *, top_k: int = 5) -> list[str]:
    """把 VLM 回覆切成去重、去標點的關鍵詞（取前 top_k）。"""
    out: list[str] = []
    for part in _SPLIT.split((raw or "").strip()):
        token = part.strip(_STRIP).strip()
        if token and token not in out:
            out.append(token)
    return out[:top_k]


class VlmClassifier:
    """沒字圖 → 標籤（VLM）+ 影像 embedding（飛輪訓練集，可選）。"""

    def __init__(self, vlm, image_embedder=None, *, model: str | None = None):
        self._vlm = vlm
        self._embedder = image_embedder
        self._model = model

    def classify(self, image_bytes: bytes, *, top_k: int = 5) -> Classification:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        media = "image/jpeg" if image_bytes[:3] == b"\xff\xd8\xff" else "image/png"
        raw = self._vlm.annotate(
            b64, media, _SYSTEM, _USER, task="textless_classify", model=self._model
        )
        labels = parse_labels(raw, top_k=top_k)
        model_version = self._model or getattr(self._vlm, "model", "vlm")
        embedding: list[float] | None = None
        if self._embedder is not None:
            try:
                embedding = self._embedder.embed_image(image_bytes)
            except Exception:  # noqa: BLE001 訓練 embedding 失敗不影響回應（標籤仍可用）
                embedding = None
        return Classification(labels=labels, embedding=embedding, model_version=model_version)
