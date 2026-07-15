"""NVIDIA NIM hosted NV-CLIP：影像/文字 → 共用向量空間，供零樣本分類。

快速模式用來處理「沒有文字的圖」：把輸入圖 embed，跟一組預先算好的
「情緒/類別」標籤向量算 cosine，取最相近的幾個標籤當檢索 query。

embed 走 OpenAI 相容 embeddings 端點（``model=nvidia/nvclip``），輸入陣列
可放文字或 ``data:image/...;base64,`` 圖。多把 key 輪流、失敗換 key 重試。
NV-CLIP 的向量空間與 bge-m3 *不同*，只用於挑標籤、不入庫。
"""

from __future__ import annotations

import base64
import math
import time
from collections.abc import Callable
from typing import Any

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


def image_data_uri(image_bytes: bytes) -> str:
    media = "image/jpeg" if image_bytes[:3] == b"\xff\xd8\xff" else "image/png"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{media};base64,{b64}"


class NvClip:
    """NV-CLIP embeddings client。``embed(inputs)`` / ``embed_image(bytes)``。"""

    model_id = "nvclip"
    _NV_MODEL = "nvidia/nvclip"

    def __init__(
        self,
        keys: list[str],
        *,
        base_url: str = NVIDIA_BASE_URL,
        client_factory: Callable[[str], Any] | None = None,
    ):
        if not keys:
            raise RuntimeError("NV-CLIP 需要 NVIDIA_API_KEYS")
        if client_factory is not None:
            self._clients = [client_factory(k) for k in keys]
        else:
            from openai import OpenAI

            self._clients = [OpenAI(base_url=base_url, api_key=k) for k in keys]
        self._rr = 0

    def embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        last_exc: Exception | None = None
        for _ in range(max(2, len(self._clients) * 2)):
            client = self._clients[self._rr % len(self._clients)]
            self._rr += 1
            try:
                resp = client.embeddings.create(model=self._NV_MODEL, input=inputs)
                return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            except Exception as exc:  # noqa: BLE001 速率限制/瞬斷 → 換 key 重試
                last_exc = exc
                time.sleep(0.3)
        raise RuntimeError(f"NV-CLIP embedding 失敗：{last_exc}")

    def embed_image(self, image_bytes: bytes) -> list[float]:
        return self.embed([image_data_uri(image_bytes)])[0]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def zero_shot_labels(
    image_vec: list[float],
    label_vecs: list[list[float]],
    labels: list[str],
    *,
    top_k: int = 3,
    min_score: float = 0.0,
) -> list[str]:
    """回傳與 image_vec cosine 最高的前 top_k 個標籤（過濾低於 min_score 者）。"""
    scored = [
        (label, _cosine(image_vec, vec))
        for label, vec in zip(labels, label_vecs, strict=True)
    ]
    scored.sort(key=lambda t: t[1], reverse=True)
    return [label for label, score in scored[:top_k] if score >= min_score]


class ZeroShotClassifier:
    """把 NV-CLIP 包成「圖 → 情緒/類別標籤」；標籤向量惰性計算並快取（只算一次）。"""

    def __init__(self, clip: Any, labels: list[str]):
        self._clip = clip
        self._labels = list(labels)
        self._label_vecs: list[list[float]] | None = None

    def _ensure_label_vecs(self) -> list[list[float]]:
        if self._label_vecs is None:
            self._label_vecs = self._clip.embed(self._labels)
        return self._label_vecs

    def classify(self, image_bytes: bytes, *, top_k: int = 3, min_score: float = 0.0) -> list[str]:
        image_vec = self._clip.embed_image(image_bytes)
        return zero_shot_labels(
            image_vec, self._ensure_label_vecs(), self._labels, top_k=top_k, min_score=min_score
        )
