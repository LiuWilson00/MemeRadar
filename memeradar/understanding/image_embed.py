"""NVIDIA NIM hosted 影像 embedding（nvidia/llama-nemotron-embed-vl-1b-v2）。

快速模式沒字圖走小 VLM 取「情緒/類別」標籤；同時把「影像 embedding + VLM 標籤」
存成訓練集，日後訓練便宜的 image→emotion 分類器取代 VLM（資料飛輪）。此 embedder
負責影像那半——把圖 embed 成向量存起來當訓練特徵。

NV-CLIP（nvidia/nvclip）已棄用、我方 org 也未開通；改用實測可用的
llama-nemotron-embed-vl-1b-v2（2048 維，影像用 data-uri 字串、input_type=passage）。
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


def image_data_uri(image_bytes: bytes) -> str:
    media = "image/jpeg" if image_bytes[:3] == b"\xff\xd8\xff" else "image/png"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{media};base64,{b64}"


class NvImageEmbedder:
    """影像 embedding client。``embed_image(bytes) -> vec``（供飛輪訓練集儲存）。"""

    model_id = "llama-nemotron-embed-vl-1b-v2"
    _NV_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2"

    def __init__(
        self,
        keys: list[str],
        *,
        base_url: str = NVIDIA_BASE_URL,
        client_factory: Callable[[str], Any] | None = None,
    ):
        if not keys:
            raise RuntimeError("影像 embedding 需要 NVIDIA_API_KEYS")
        if client_factory is not None:
            self._clients = [client_factory(k) for k in keys]
        else:
            from openai import OpenAI

            # timeout=30：卡住的呼叫快速失敗，不讓 SDK 預設 ~600s 拖住呼叫端
            self._clients = [
                OpenAI(base_url=base_url, api_key=k, timeout=30.0, max_retries=2) for k in keys
            ]
        self._rr = 0

    def _embed(self, inputs: list[str], input_type: str) -> list[list[float]]:
        last_exc: Exception | None = None
        for _ in range(max(2, len(self._clients) * 2)):
            client = self._clients[self._rr % len(self._clients)]
            self._rr += 1
            try:
                resp = client.embeddings.create(
                    model=self._NV_MODEL, input=inputs,
                    extra_body={"input_type": input_type},
                )
                return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            except Exception as exc:  # noqa: BLE001 速率限制/瞬斷 → 換 key 重試
                last_exc = exc
                time.sleep(0.3)
        raise RuntimeError(f"影像 embedding 失敗：{last_exc}")

    def embed_image(self, image_bytes: bytes) -> list[float]:
        return self._embed([image_data_uri(image_bytes)], "passage")[0]
