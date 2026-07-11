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
from typing import Protocol

from memeradar.shared import repository as repo
from memeradar.shared.models import Embedding
from memeradar.understanding.retrieval_doc import (
    RETRIEVAL_DOC_VERSION,
    build_retrieval_document,
)

DEFAULT_BACKEND = "bge-m3"
BATCH_SIZE = 32


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


_BACKENDS: dict[str, type] = {
    "bge-m3": BgeM3Embedder,
}


def get_embedder(name: str) -> Embedder:
    if name not in _BACKENDS:
        available = "、".join(sorted(_BACKENDS))
        raise ValueError(f"未知的 embedding 後端：{name!r}（可用：{available}）")
    return _BACKENDS[name]()


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
