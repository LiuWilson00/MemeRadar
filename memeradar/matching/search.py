"""向量檢索 + metadata 過濾（docs/04 §2.3）。

上生產環境改用 **PostgreSQL + pgvector**：以 SQL 端 ``<=>`` 餘弦距離排序取 Top-K，
metadata（franchise / category / nsfw / status）於同一查詢過濾。``VectorSearcher``
仍為薄介面。目前 vector 欄不固定維度、未建 HNSW；規模變大時 ALTER 成固定維度並
加索引即可（見 alembic 基準版註記）。

一致性設計：不維護獨立索引，直接查主庫——下架（status=removed）、待審、
非梗圖在查詢層過濾，天然不會出現「索引與 DB 不同步」問題（docs/03 §3.2
的對帳需求由結構保證）。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Protocol

import psycopg.errors

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
    """metadata 過濾 + pgvector SQL 端餘弦（``<=>``）取 Top-K。

    名稱沿用（歷史為 SQLite 程式內餘弦），實作已改為 PostgreSQL + pgvector：
    餘弦相似度 = ``1 - (vector <=> query)``；同分以 meme_id 決定序（與舊行為一致）。
    """

    def __init__(self, conn, signature: str):
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
        qvec = json.dumps(query_vector)  # pgvector 可解析 '[..]' 文字
        # 內層只以「純距離」排序取 Top-K → pgvector HNSW 索引才吃得到（0007_vector_index）。
        # 相似度門檻與同分序（meme_id）挪到外層：內層若帶 min_similarity 於 WHERE、或在
        # ORDER BY 加 meme_id 次鍵，都會讓 HNSW 失效、退化成全表精確掃描。行為等價——
        # 內層取的就是最相似的 K 張，外層再濾掉低於門檻者（門檻只會砍掉最不相似的尾巴）。
        # 規模變大後可調 hnsw.ef_search（預設 40）以確保過濾後仍湊得滿 K 張。
        inner = """
            SELECT a.*, m.hotness AS meme_hotness,
                   e.vector <=> %s::vector AS distance
            FROM memes m
            JOIN meme_annotations a ON a.meme_id = m.meme_id
            JOIN embeddings e
                ON e.meme_id = m.meme_id
               AND e.kind = 'text_retrieval'
               AND e.model = %s
            WHERE m.status = 'active' AND a.is_meme = 1
        """
        params: list = [qvec, self._signature]

        if filters.exclude_nsfw:
            inner += " AND a.nsfw = 0"

        if filters.franchises:
            taxonomy = get_taxonomy()
            normalized = [taxonomy.normalize_franchise(f) for f in filters.franchises]
            inner += f" AND a.franchise IN ({','.join(['%s'] * len(normalized))})"
            params.extend(normalized)

        if filters.categories:
            placeholders = ",".join(["%s"] * len(filters.categories))
            inner += (
                " AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(a.categories::jsonb)"
                f" AS cv WHERE cv IN ({placeholders}))"
            )
            params.extend(filters.categories)

        inner += " ORDER BY e.vector <=> %s::vector LIMIT %s"
        params.extend([qvec, k])

        sql = f"""
            SELECT t.*, 1 - t.distance AS similarity
            FROM ({inner}) t
            WHERE 1 - t.distance >= %s
            ORDER BY t.distance, t.meme_id
        """
        params.append(min_similarity)

        try:
            rows = self._conn.execute(sql, params).fetchall()
        except psycopg.errors.DataException as exc:
            # 查詢向量與索引維度不符（embedding 簽名漂移）——明確報錯，勿悄悄回錯結果
            self._conn.rollback()
            raise ValueError(f"向量維度不符（embedding 簽名是否一致？）：{exc}") from exc

        return [
            SearchHit(
                meme_id=row["meme_id"],
                similarity=row["similarity"],
                annotation=annotation_from_row(row),
                hotness=row["meme_hotness"],
            )
            for row in rows
        ]
