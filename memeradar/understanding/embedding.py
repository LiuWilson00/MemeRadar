"""Embedding 介面封裝與向量化管線（docs/03 §3.2）。

- ``Embedder`` 為薄介面：``embed(texts) -> vectors`` + ``model_id``。
- 後端註冊表 ``get_embedder(name)``：已定案 **bge-m3**（本地自架，2026-07-11 決策）；
  介面保留，之後要加 Voyage 等後端只需註冊新類別。
- 入庫簽名 ``{model_id}|{RETRIEVAL_DOC_VERSION}`` 同時綁定 embedding 模型與
  檢索文件模板版本——兩者任一改版，既有向量即視為過期需重建。
- 重依賴（torch / sentence-transformers）lazy 載入，需安裝 extras：
  ``pip install -e ".[local-embedding]"``。
- CLI：``python -m memeradar.understanding.embedding [--limit N] [--backend bge-m3]``。
"""

from __future__ import annotations

import sqlite3
import sys
import time
from typing import Protocol

from memeradar.shared import repository as repo
from memeradar.shared.models import Embedding
from memeradar.understanding.retrieval_doc import (
    RETRIEVAL_DOC_VERSION,
    build_retrieval_document,
)

# 預設走 NVIDIA hosted bge-m3（與本地 sentence-transformers bge-m3 向量完全相同，
# cosine=1.0），省掉容器內的 torch + 2.3GB 模型與其記憶體。離線開發可設
# EMBEDDING_BACKEND=bge-m3 走本地。
DEFAULT_BACKEND = "nvidia-bge-m3"
BATCH_SIZE = 32
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class BgeM3Embedder:
    """BGE-M3 本地推論（首次使用會自動下載模型權重，約 2.3GB）。"""

    model_id = "bge-m3"

    def __init__(self, device: str | None = None):
        self._device = device
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "本地 embedding 需要 sentence-transformers："
                    '請執行 pip install -e ".[local-embedding]"'
                ) from exc
            self._model = SentenceTransformer("BAAI/bge-m3", device=self._device)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_loaded()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]


class NvidiaBgeM3Embedder:
    """NVIDIA NIM hosted ``baai/bge-m3``。

    與本地 sentence-transformers bge-m3 的向量**完全相同**（實測 cosine=1.0），故
    ``model_id`` 沿用 "bge-m3"、簽名相同、既有向量相容。不需 torch/本地模型，記憶體極省。
    多把 key 輪流以分攤免費層速率限制；失敗換 key 重試。
    """

    model_id = "bge-m3"  # 與本地相同 → 簽名相同 → 既有向量相容
    _NV_MODEL = "baai/bge-m3"

    def __init__(self, keys: list[str], *, batch_size: int = BATCH_SIZE):
        if not keys:
            raise RuntimeError(
                "NVIDIA embedding 需要 NVIDIA_API_KEYS（或設 EMBEDDING_BACKEND=bge-m3 走本地）"
            )
        from openai import OpenAI

        # timeout=30：卡住的 embedding 呼叫快速失敗，不讓 SDK 預設 ~600s 拖住呼叫端
        self._clients = [
            OpenAI(base_url=NVIDIA_BASE_URL, api_key=k, timeout=30.0, max_retries=2)
            for k in keys
        ]
        self._batch = batch_size
        self._rr = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            out.extend(self._embed_batch(texts[i : i + self._batch]))
        return out

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last_exc: Exception | None = None
        for _ in range(max(2, len(self._clients) * 2)):
            client = self._clients[self._rr % len(self._clients)]
            self._rr += 1
            try:
                resp = client.embeddings.create(
                    model=self._NV_MODEL, input=batch,
                    extra_body={"input_type": "passage", "truncate": "END"},
                )
                return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            except Exception as exc:  # noqa: BLE001 速率限制/瞬斷 → 換 key 重試
                last_exc = exc
                time.sleep(0.5)
        raise RuntimeError(f"NVIDIA embedding 失敗：{last_exc}")


_LOCAL_BACKENDS: dict[str, type] = {
    "bge-m3": BgeM3Embedder,
}
_BACKENDS = frozenset({"nvidia-bge-m3", *_LOCAL_BACKENDS})


def get_embedder(name: str) -> Embedder:
    if name == "nvidia-bge-m3":
        from memeradar.shared.config import get_settings

        return NvidiaBgeM3Embedder(get_settings().nvidia_keys())
    if name in _LOCAL_BACKENDS:
        return _LOCAL_BACKENDS[name]()
    available = "、".join(sorted(_BACKENDS))
    raise ValueError(f"未知的 embedding 後端：{name!r}（可用：{available}）")


def embedding_signature(embedder: Embedder) -> str:
    """入庫用簽名：embedding 模型 × 檢索文件模板版本。"""
    return f"{embedder.model_id}|{RETRIEVAL_DOC_VERSION}"


def embed_pending_memes(
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
) -> int:
    """把缺少當前簽名向量的梗圖批次向量化，回傳處理張數（冪等）。"""
    signature = embedding_signature(embedder)
    pending = repo.list_memes_missing_embedding(
        conn, kind="text_retrieval", model=signature, limit=limit
    )
    processed = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        docs = [
            build_retrieval_document(repo.get_annotation(conn, meme.meme_id)) for meme in batch
        ]
        vectors = embedder.embed(docs)
        for meme, vector in zip(batch, vectors, strict=True):
            repo.add_embedding(
                conn,
                Embedding(
                    meme_id=meme.meme_id,
                    kind="text_retrieval",
                    model=signature,
                    vector=vector,
                ),
            )
        processed += len(batch)
    return processed


def main(argv: list[str] | None = None) -> None:
    import argparse

    from memeradar.shared.db import connect, migrate

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="批次向量化已標註的梗圖")
    parser.add_argument("--limit", type=int, default=None, help="最多處理張數（預設全部）")
    parser.add_argument("--backend", default=DEFAULT_BACKEND, choices=sorted(_BACKENDS))
    args = parser.parse_args(argv)

    embedder = get_embedder(args.backend)
    conn = connect()
    try:
        migrate(conn)
        count = embed_pending_memes(conn, embedder)
    finally:
        conn.close()
    print(f"向量化完成：{count} 張（簽名 {embedding_signature(embedder)}）")


if __name__ == "__main__":
    main()
