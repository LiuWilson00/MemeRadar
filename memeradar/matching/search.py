"""向量檢索 + metadata 過濾（docs/04 §2.3；Q1 決策落地）。

Q1 決策（2026-07-11）：Demo 量級（<1 萬張）採 **SQLite 單庫 + 程式內餘弦**——
零新依賴、零部署。``VectorSearcher`` 為薄介面，量級成長或延遲超出預算
（P3-7 規模化驗證）時，換 pgvector / Qdrant 只需替換實作。

一致性設計：不維護獨立索引，直接查主庫——下架（status=removed）、待審、
非梗圖在查詢層過濾，天然不會出現「索引與 DB 不同步」問題（docs/03 §3.2
的對帳需求由結構保證）。
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from memeradar.shared.models import MemeAnnotation
from memeradar.shared.repository import annotation_from_row
from memeradar.shared.taxonomy import get_taxonomy

DEFAULT_MIN_SIMILARITY = 0.0


@dataclass(frozen=True)
class SearchFilters:
    """metadata 預過濾條件（docs/04 §2.3）。空 tuple = 不限。"""

    franchises: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    exclude_nsfw: bool = True


@dataclass(frozen=True)
class SearchHit:
    meme_id: str
    similarity: float
    annotation: MemeAnnotation
    hotness: float = 0.0  # 熱度（排序端最終分數微調用，docs/04 §2.4）


class VectorSearcher(Protocol):
    def search(
        self,
        query_vector: list[float],
        *,
        k: int,
        filters: SearchFilters,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> list[SearchHit]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"向量維度不符：query={len(a)}、索引={len(b)}（embedding 簽名是否一致？）")
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


class SqliteBruteForceSearcher:
    """SQL 做 metadata 預過濾，Python 對過濾後候選算餘弦、取 Top-K。"""

    def __init__(self, conn: sqlite3.Connection, signature: str):
        self._conn = conn
        self._signature = signature

    def search(
        self,
        query_vector: list[float],
        *,
        k: int,
        filters: SearchFilters,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> list[SearchHit]:
        sql = """
            SELECT a.*, e.vector AS emb_vector, m.hotness AS meme_hotness
            FROM memes m
            JOIN meme_annotations a ON a.meme_id = m.meme_id
            JOIN embeddings e
                ON e.meme_id = m.meme_id
               AND e.kind = 'text_retrieval'
               AND e.model = ?
            WHERE m.status = 'active' AND a.is_meme = 1
        """
        params: list = [self._signature]

        if filters.exclude_nsfw:
            sql += " AND a.nsfw = 0"

        if filters.franchises:
            taxonomy = get_taxonomy()
            normalized = [taxonomy.normalize_franchise(f) for f in filters.franchises]
            sql += f" AND a.franchise IN ({','.join('?' * len(normalized))})"
            params.extend(normalized)

        if filters.categories:
            placeholders = ",".join("?" * len(filters.categories))
            sql += (
                " AND EXISTS (SELECT 1 FROM json_each(a.categories)"
                f" WHERE json_each.value IN ({placeholders}))"
            )
            params.extend(filters.categories)

        scored: list[SearchHit] = []
        for row in self._conn.execute(sql, params):
            similarity = _cosine(query_vector, json.loads(row["emb_vector"]))
            if similarity < min_similarity:
                continue
            scored.append(
                SearchHit(
                    meme_id=row["meme_id"],
                    similarity=similarity,
                    annotation=annotation_from_row(row),
                    hotness=row["meme_hotness"],
                )
            )

        scored.sort(key=lambda h: (-h.similarity, h.meme_id))
        return scored[:k]
