"""資料存取層：dataclass ↔ PostgreSQL 的寫讀，含 JSON 欄位序列化。

所有寫入函式自行 commit；呼叫端只需持有 ``db.connect()``（psycopg）的連線。
JSON 欄位以 TEXT 存 JSON 字串（_dumps/_loads）；向量以 pgvector ``vector`` 型別
（寫入 ::vector 轉型、讀回為 '[..]' 文字經 _loads 還原）。
註：部分型別註記仍寫作 ``sqlite3.Connection``（歷史遺留，執行期不影響；連線實為
psycopg）。
"""

from __future__ import annotations

import json
import sqlite3

from memeradar.shared.models import (
    Embedding,
    FeedbackEvent,
    Meme,
    MemeAnnotation,
    MemeSource,
    RecommendationLog,
    _now_iso,
    new_id,
)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None) -> object:
    return None if value is None else json.loads(value)


# ── memes ────────────────────────────────────────────────────────────


def insert_meme(conn: sqlite3.Connection, meme: Meme) -> None:
    conn.execute(
        """
        INSERT INTO memes (meme_id, image_uri, sha256, phash, width, height,
                           hotness, status, first_seen_at, engagement, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            meme.meme_id,
            meme.image_uri,
            meme.sha256,
            meme.phash,
            meme.width,
            meme.height,
            meme.hotness,
            meme.status,
            meme.first_seen_at,
            meme.engagement,
            meme.last_seen_at,
        ),
    )
    conn.commit()


# memes 的純量欄位清單（明確列出、排除 image_data BYTEA）。熱路徑（get_meme、佇列列舉等）
# 只需這些欄位；用 SELECT * 會白拉幾百 KB 圖檔位元組（_row_to_meme 也根本沒讀 image_data）。
_MEME_COL_NAMES = (
    "meme_id", "image_uri", "sha256", "phash", "width", "height",
    "hotness", "status", "first_seen_at", "engagement", "last_seen_at",
)
_MEME_COLS = ", ".join(_MEME_COL_NAMES)
_MEME_COLS_M = ", ".join(f"m.{c}" for c in _MEME_COL_NAMES)


def _row_to_meme(row: sqlite3.Row) -> Meme:
    return Meme(
        meme_id=row["meme_id"],
        image_uri=row["image_uri"],
        sha256=row["sha256"],
        phash=row["phash"],
        width=row["width"],
        height=row["height"],
        hotness=row["hotness"],
        status=row["status"],
        first_seen_at=row["first_seen_at"],
        engagement=row["engagement"],
        last_seen_at=row["last_seen_at"],
    )


def get_meme(conn: sqlite3.Connection, meme_id: str) -> Meme | None:
    row = conn.execute(
        f"SELECT {_MEME_COLS} FROM memes WHERE meme_id = %s", (meme_id,)
    ).fetchone()
    return _row_to_meme(row) if row else None


def find_meme_by_sha256(conn: sqlite3.Connection, sha256: str) -> Meme | None:
    row = conn.execute(
        f"SELECT {_MEME_COLS} FROM memes WHERE sha256 = %s", (sha256,)
    ).fetchone()
    return _row_to_meme(row) if row else None


def set_status(conn: sqlite3.Connection, meme_id: str, status: str) -> None:
    conn.execute("UPDATE memes SET status = %s WHERE meme_id = %s", (status, meme_id))
    conn.commit()


def get_image_data(conn: sqlite3.Connection, meme_id: str) -> bytes | None:
    """取 DB 內的圖片位元組（雲端免 volume 時圖存這）；沒有則 None。"""
    row = conn.execute(
        "SELECT image_data FROM memes WHERE meme_id = %s", (meme_id,)
    ).fetchone()
    if row and row["image_data"] is not None:
        return bytes(row["image_data"])
    return None


def set_image_data(conn: sqlite3.Connection, meme_id: str, data: bytes) -> None:
    conn.execute("UPDATE memes SET image_data = %s WHERE meme_id = %s", (data, meme_id))
    conn.commit()


def count_memes(conn: sqlite3.Connection, status: str | None = None) -> int:
    if status is None:
        row = conn.execute("SELECT COUNT(*) AS n FROM memes").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM memes WHERE status = %s", (status,)
        ).fetchone()
    return row["n"]


def list_memes_missing_annotation(conn: sqlite3.Connection, limit: int | None = None) -> list[Meme]:
    """列出尚未標註且未下架的梗圖（標註管線的工作佇列）。"""
    sql = f"""
        SELECT {_MEME_COLS_M} FROM memes m
        LEFT JOIN meme_annotations a ON a.meme_id = m.meme_id
        WHERE a.meme_id IS NULL AND m.status != 'removed'
        ORDER BY m.first_seen_at
    """
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)
    return [_row_to_meme(r) for r in conn.execute(sql, params).fetchall()]


def list_active_unannotated(conn: sqlite3.Connection, limit: int = 1) -> list[Meme]:
    """背景標註佇列：active 且尚未標註的梗圖（舊到新）。

    只取 active——標註失敗會轉 pending_review，藉此排除、避免同一張壞圖無限重試；
    限流耗盡（未寫標註）者維持 active，下輪會再被撿起來。
    """
    return [
        _row_to_meme(r)
        for r in conn.execute(
            f"""
            SELECT {_MEME_COLS_M} FROM memes m
            LEFT JOIN meme_annotations a ON a.meme_id = m.meme_id
            WHERE a.meme_id IS NULL AND m.status = 'active'
            ORDER BY m.first_seen_at
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    ]


def count_active_unannotated(conn: sqlite3.Connection) -> int:
    """待背景標註的張數（供上傳頁顯示進度）。"""
    return conn.execute(
        """
        SELECT COUNT(*) AS n FROM memes m
        LEFT JOIN meme_annotations a ON a.meme_id = m.meme_id
        WHERE a.meme_id IS NULL AND m.status = 'active'
        """
    ).fetchone()["n"]


def list_recommendation_logs(
    conn: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> list[dict]:
    """查詢歷史列表（含 👍👎 統計），新到舊。回傳 JSON-ready dict。"""
    rows = conn.execute(
        """
        SELECT r.query_id, r.created_at, r.conversation, r.params_snapshot,
               r.latency_ms, r.final_results,
               COALESCE(SUM(CASE WHEN f.rating = 'up' THEN 1 ELSE 0 END), 0) AS ups,
               COALESCE(SUM(CASE WHEN f.rating = 'down' THEN 1 ELSE 0 END), 0) AS downs
        FROM recommendation_logs r
        LEFT JOIN feedback_events f ON f.query_id = r.query_id
        GROUP BY r.query_id
        ORDER BY r.created_at DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    ).fetchall()
    return [
        {
            "query_id": r["query_id"],
            "created_at": r["created_at"],
            "conversation": _loads(r["conversation"]),
            "params_snapshot": _loads(r["params_snapshot"]),
            "latency_ms": r["latency_ms"],
            "result_count": len(_loads(r["final_results"]) or []),
            "ups": r["ups"],
            "downs": r["downs"],
        }
        for r in rows
    ]


def list_memes_with_annotations(
    conn: sqlite3.Connection,
    *,
    franchise: str | None = None,
    category: str | None = None,
    emotion: str | None = None,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    """梗圖庫瀏覽：含標註摘要與篩選，未標註者 annotation 為 None。"""
    # a.* 在前、m 欄位在後：兩表都有 meme_id，psycopg dict_row 以「後者」為準，
    # 故 m.meme_id 需排在 a.meme_id 之後才會勝出（未標註時 a.meme_id 為 NULL）。
    sql = """
        SELECT a.*, m.meme_id, m.image_uri, m.status, m.hotness, m.width, m.height,
               m.first_seen_at
        FROM memes m
        LEFT JOIN meme_annotations a ON a.meme_id = m.meme_id
        WHERE 1 = 1
    """
    params: list = []
    if status is not None:
        sql += " AND m.status = %s"
        params.append(status)
    if franchise is not None:
        sql += " AND a.franchise = %s"
        params.append(franchise)
    if category is not None:
        sql += (
            " AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(a.categories::jsonb)"
            " AS v WHERE v = %s)"
        )
        params.append(category)
    if emotion is not None:
        sql += (
            " AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(a.emotions::jsonb)"
            " AS v WHERE v = %s)"
        )
        params.append(emotion)
    sql += " ORDER BY m.first_seen_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    results = []
    for row in conn.execute(sql, params):
        annotation = None
        if row["model_version"] is not None:
            ann = annotation_from_row(row)
            annotation = {
                "is_meme": ann.is_meme,
                "nsfw": ann.nsfw,
                "ocr_text": ann.ocr_text,
                "description": ann.description,
                "characters": ann.characters,
                "franchise": ann.franchise,
                "template_name": ann.template_name,
                "emotions": ann.emotions,
                "usage_hints": ann.usage_hints,
                "categories": ann.categories,
                "confidence": ann.confidence,
                "model_version": ann.model_version,
            }
        results.append(
            {
                "meme_id": row["meme_id"],
                "image_uri": row["image_uri"],
                "status": row["status"],
                "hotness": row["hotness"],
                "width": row["width"],
                "height": row["height"],
                "first_seen_at": row["first_seen_at"],
                "annotation": annotation,
            }
        )
    return results


def franchise_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """各 franchise 的可檢索梗圖數（Console 梗圖包下拉選單用）。"""
    rows = conn.execute(
        """
        SELECT a.franchise AS name, COUNT(*) AS n
        FROM meme_annotations a
        JOIN memes m ON m.meme_id = a.meme_id
        WHERE m.status = 'active' AND a.is_meme = 1 AND a.franchise IS NOT NULL
        GROUP BY a.franchise
        ORDER BY n DESC, name
        """
    ).fetchall()
    return {r["name"]: r["n"] for r in rows}


def category_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """各分類的可檢索梗圖數（開放集：直接由庫內實際出現的值統計，含模型自創）。"""
    rows = conn.execute(
        """
        SELECT v.value AS name, COUNT(*) AS n
        FROM meme_annotations a
        JOIN memes m ON m.meme_id = a.meme_id
        CROSS JOIN LATERAL jsonb_array_elements_text(a.categories::jsonb) AS v(value)
        WHERE m.status = 'active' AND a.is_meme = 1
        GROUP BY v.value
        ORDER BY n DESC, name
        """
    ).fetchall()
    return {r["name"]: r["n"] for r in rows}


def list_memes_missing_embedding(
    conn: sqlite3.Connection, kind: str, model: str, limit: int | None = None
) -> list[Meme]:
    """列出已標註為梗圖、狀態 active、但缺少指定簽名向量的梗圖（向量化工作佇列）。"""
    sql = f"""
        SELECT {_MEME_COLS_M} FROM memes m
        JOIN meme_annotations a ON a.meme_id = m.meme_id
        LEFT JOIN embeddings e
            ON e.meme_id = m.meme_id AND e.kind = %s AND e.model = %s
        WHERE e.meme_id IS NULL AND m.status = 'active' AND a.is_meme = 1
        ORDER BY m.first_seen_at
    """
    params: tuple = (kind, model)
    if limit is not None:
        sql += " LIMIT %s"
        params = (kind, model, limit)
    return [_row_to_meme(r) for r in conn.execute(sql, params).fetchall()]


# ── meme_annotations ─────────────────────────────────────────────────


def upsert_annotation(conn: sqlite3.Connection, ann: MemeAnnotation) -> None:
    conn.execute(
        """
        INSERT INTO meme_annotations (meme_id, model_version, is_meme, nsfw, ocr_text,
                                      description, characters, franchise, template_name,
                                      emotions, usage_hints, categories, confidence,
                                      annotated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (meme_id) DO UPDATE SET
            model_version = excluded.model_version,
            is_meme = excluded.is_meme,
            nsfw = excluded.nsfw,
            ocr_text = excluded.ocr_text,
            description = excluded.description,
            characters = excluded.characters,
            franchise = excluded.franchise,
            template_name = excluded.template_name,
            emotions = excluded.emotions,
            usage_hints = excluded.usage_hints,
            categories = excluded.categories,
            confidence = excluded.confidence,
            annotated_at = excluded.annotated_at
        """,
        (
            ann.meme_id,
            ann.model_version,
            int(ann.is_meme),
            int(ann.nsfw),
            ann.ocr_text,
            ann.description,
            _dumps(ann.characters),
            ann.franchise,
            ann.template_name,
            _dumps(ann.emotions),
            _dumps(ann.usage_hints),
            _dumps(ann.categories),
            ann.confidence,
            ann.annotated_at,
        ),
    )
    conn.commit()


def get_annotation(conn: sqlite3.Connection, meme_id: str) -> MemeAnnotation | None:
    row = conn.execute(
        "SELECT * FROM meme_annotations WHERE meme_id = %s", (meme_id,)
    ).fetchone()
    return annotation_from_row(row) if row else None


def annotation_from_row(row: sqlite3.Row) -> MemeAnnotation:
    """由含 meme_annotations 欄位的查詢列建構標註（供 JOIN 查詢共用）。"""
    return MemeAnnotation(
        meme_id=row["meme_id"],
        model_version=row["model_version"],
        is_meme=bool(row["is_meme"]),
        nsfw=bool(row["nsfw"]),
        ocr_text=row["ocr_text"],
        description=row["description"],
        characters=_loads(row["characters"]),
        franchise=row["franchise"],
        template_name=row["template_name"],
        emotions=_loads(row["emotions"]),
        usage_hints=_loads(row["usage_hints"]),
        categories=_loads(row["categories"]),
        confidence=row["confidence"],
        annotated_at=row["annotated_at"],
    )


def set_phash(conn: sqlite3.Connection, meme_id: str, phash: str) -> None:
    conn.execute("UPDATE memes SET phash = %s WHERE meme_id = %s", (phash, meme_id))
    conn.commit()


def list_phashes(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """所有已知 pHash（去重 L2 比對用）。"""
    rows = conn.execute("SELECT meme_id, phash FROM memes WHERE phash IS NOT NULL").fetchall()
    return [(r["meme_id"], r["phash"]) for r in rows]


def update_meme_image(
    conn: sqlite3.Connection,
    meme_id: str,
    *,
    image_uri: str,
    sha256: str,
    width: int,
    height: int,
) -> None:
    """以較高解析度版本替換主圖（docs/02 §4）。"""
    conn.execute(
        "UPDATE memes SET image_uri = %s, sha256 = %s, width = %s, height = %s WHERE meme_id = %s",
        (image_uri, sha256, width, height, meme_id),
    )
    conn.commit()


def list_embeddings_by_kind(
    conn: sqlite3.Connection, *, kind: str, model: str
) -> dict[str, list[float]]:
    """指定簽名的全部向量（去重 L3 比對用；量級 <10 萬列可全載）。"""
    rows = conn.execute(
        "SELECT meme_id, vector FROM embeddings WHERE kind = %s AND model = %s", (kind, model)
    ).fetchall()
    return {r["meme_id"]: _loads(r["vector"]) for r in rows}


# ── dedup_reviews（去重人工佇列）────────────────────────────────────


def add_dedup_review(
    conn: sqlite3.Connection,
    *,
    meme_id: str,
    matched_meme_id: str,
    layer: str,
    score: float | None,
) -> str:
    review_id = new_id("dr")
    conn.execute(
        """
        INSERT INTO dedup_reviews (review_id, meme_id, matched_meme_id, layer, score)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (review_id, meme_id, matched_meme_id, layer, score),
    )
    conn.commit()
    return review_id


def list_dedup_reviews(conn: sqlite3.Connection, resolution: str = "pending") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM dedup_reviews WHERE resolution = %s ORDER BY created_at", (resolution,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_dedup_review(conn: sqlite3.Connection, review_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM dedup_reviews WHERE review_id = %s", (review_id,)
    ).fetchone()
    return dict(row) if row else None


def set_dedup_review_resolution(
    conn: sqlite3.Connection, review_id: str, resolution: str
) -> None:
    conn.execute(
        "UPDATE dedup_reviews SET resolution = %s WHERE review_id = %s", (resolution, review_id)
    )
    conn.commit()


def move_sources(conn: sqlite3.Connection, *, from_meme_id: str, to_meme_id: str) -> None:
    """把重複梗圖的來源 metadata 併入保留的主圖（docs/02 §4 合併）。"""
    conn.execute(
        "UPDATE meme_sources SET meme_id = %s WHERE meme_id = %s", (to_meme_id, from_meme_id)
    )
    conn.commit()


# ── crawl_state（爬蟲水位）──────────────────────────────────────────


def get_watermark(conn: sqlite3.Connection, source: str) -> str | None:
    row = conn.execute(
        "SELECT watermark FROM crawl_state WHERE source = %s", (source,)
    ).fetchone()
    return row["watermark"] if row else None


def set_watermark(conn: sqlite3.Connection, source: str, watermark: str) -> None:
    conn.execute(
        """
        INSERT INTO crawl_state (source, watermark, updated_at) VALUES (%s, %s, now()::text)
        ON CONFLICT (source) DO UPDATE SET
            watermark = excluded.watermark,
            updated_at = excluded.updated_at
        """,
        (source, watermark),
    )
    conn.commit()


# ── crawl_health（來源健康度）───────────────────────────────────────


def get_crawl_failures(conn: sqlite3.Connection, source: str) -> int:
    row = conn.execute(
        "SELECT consecutive_failures FROM crawl_health WHERE source = %s", (source,)
    ).fetchone()
    return row["consecutive_failures"] if row else 0


def record_crawl_failure(conn: sqlite3.Connection, source: str, error: str) -> int:
    """記一次來源失敗，回傳連續失敗次數（≥3 應告警，docs/02 §6）。"""
    conn.execute(
        """
        INSERT INTO crawl_health (source, consecutive_failures, last_error, updated_at)
        VALUES (%s, 1, %s, now()::text)
        ON CONFLICT (source) DO UPDATE SET
            consecutive_failures = crawl_health.consecutive_failures + 1,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (source, error),
    )
    conn.commit()
    return get_crawl_failures(conn, source)


def reset_crawl_failures(conn: sqlite3.Connection, source: str) -> None:
    conn.execute(
        """
        INSERT INTO crawl_health (source, consecutive_failures, last_error, updated_at)
        VALUES (%s, 0, NULL, now()::text)
        ON CONFLICT (source) DO UPDATE SET
            consecutive_failures = 0,
            last_error = NULL,
            updated_at = excluded.updated_at
        """,
        (source,),
    )
    conn.commit()


# ── meme_sources ─────────────────────────────────────────────────────


def add_source(conn: sqlite3.Connection, src: MemeSource) -> None:
    conn.execute(
        """
        INSERT INTO meme_sources (source_id, meme_id, platform, post_url, post_title,
                                  top_comments, upvotes, posted_at, crawled_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            src.source_id,
            src.meme_id,
            src.platform,
            src.post_url,
            src.post_title,
            _dumps(src.top_comments),
            src.upvotes,
            src.posted_at,
            src.crawled_at,
        ),
    )
    conn.commit()


def list_sources(conn: sqlite3.Connection, meme_id: str) -> list[MemeSource]:
    rows = conn.execute(
        "SELECT * FROM meme_sources WHERE meme_id = %s ORDER BY crawled_at", (meme_id,)
    ).fetchall()
    return [
        MemeSource(
            source_id=r["source_id"],
            meme_id=r["meme_id"],
            platform=r["platform"],
            post_url=r["post_url"],
            post_title=r["post_title"],
            top_comments=_loads(r["top_comments"]),
            upvotes=r["upvotes"],
            posted_at=r["posted_at"],
            crawled_at=r["crawled_at"],
        )
        for r in rows
    ]


def imported_source_urls(conn: sqlite3.Connection, platform: str) -> set[str]:
    """某來源平台已入庫的 post_url 集合。

    爬蟲回填「下載前」預先去重用：跳過已入庫的 URL、免得白下載一堆再靠 sha256 擋掉
    （重跑時最省頻寬/時間的關鍵）。sha256/phash 去重仍是最終正確性保證。
    """
    rows = conn.execute(
        "SELECT post_url FROM meme_sources WHERE platform = %s AND post_url IS NOT NULL",
        (platform,),
    ).fetchall()
    return {r["post_url"] for r in rows}


# ── embeddings ───────────────────────────────────────────────────────


def add_embedding(conn: sqlite3.Connection, emb: Embedding) -> None:
    conn.execute(
        """
        INSERT INTO embeddings (meme_id, kind, model, vector, created_at)
        VALUES (%s, %s, %s, %s::vector, %s)
        ON CONFLICT (meme_id, kind, model) DO UPDATE SET
            vector = excluded.vector,
            created_at = excluded.created_at
        """,
        (emb.meme_id, emb.kind, emb.model, _dumps(emb.vector), emb.created_at),
    )
    conn.commit()


def get_vectors(
    conn: sqlite3.Connection, *, kind: str, model: str, meme_ids: list[str]
) -> dict[str, list[float]]:
    """批次載入指定簽名的向量（MMR 多樣化計算用）。缺席的 id 不含在結果中。"""
    if not meme_ids:
        return {}
    placeholders = ",".join(["%s"] * len(meme_ids))
    rows = conn.execute(
        f"SELECT meme_id, vector FROM embeddings"
        f" WHERE kind = %s AND model = %s AND meme_id IN ({placeholders})",
        (kind, model, *meme_ids),
    ).fetchall()
    return {r["meme_id"]: _loads(r["vector"]) for r in rows}


def get_embeddings(
    conn: sqlite3.Connection, meme_id: str, kind: str | None = None
) -> list[Embedding]:
    if kind is None:
        rows = conn.execute("SELECT * FROM embeddings WHERE meme_id = %s", (meme_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM embeddings WHERE meme_id = %s AND kind = %s", (meme_id, kind)
        ).fetchall()
    return [
        Embedding(
            meme_id=r["meme_id"],
            kind=r["kind"],
            model=r["model"],
            vector=_loads(r["vector"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ── recommendation_logs ──────────────────────────────────────────────


def insert_recommendation_log(conn: sqlite3.Connection, log: RecommendationLog) -> None:
    conn.execute(
        """
        INSERT INTO recommendation_logs (query_id, conversation, intent_result,
                                         params_snapshot, candidates, final_results,
                                         latency_ms, timings, input_type, client_id,
                                         created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            log.query_id,
            _dumps(log.conversation),
            _dumps(log.intent_result) if log.intent_result is not None else None,
            _dumps(log.params_snapshot),
            _dumps(log.candidates) if log.candidates is not None else None,
            _dumps(log.final_results) if log.final_results is not None else None,
            log.latency_ms,
            _dumps(log.timings) if log.timings is not None else None,
            log.input_type,
            log.client_id,
            log.created_at,
        ),
    )
    conn.commit()


def get_recommendation_log(conn: sqlite3.Connection, query_id: str) -> RecommendationLog | None:
    row = conn.execute(
        "SELECT * FROM recommendation_logs WHERE query_id = %s", (query_id,)
    ).fetchone()
    if row is None:
        return None
    return RecommendationLog(
        query_id=row["query_id"],
        conversation=_loads(row["conversation"]),
        intent_result=_loads(row["intent_result"]),
        params_snapshot=_loads(row["params_snapshot"]),
        candidates=_loads(row["candidates"]),
        final_results=_loads(row["final_results"]),
        latency_ms=row["latency_ms"],
        timings=_loads(row["timings"]),
        input_type=row["input_type"],
        client_id=row["client_id"],
        created_at=row["created_at"],
    )


# ── feedback_events ──────────────────────────────────────────────────


def insert_feedback(conn: sqlite3.Connection, fb: FeedbackEvent) -> None:
    """寫入回饋；同一查詢的同一張圖冪等（改投以最新為準，不重複計數）。"""
    conn.execute(
        """
        INSERT INTO feedback_events (feedback_id, query_id, meme_id, rank, rating,
                                     note, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (query_id, meme_id) DO UPDATE SET
            feedback_id = excluded.feedback_id,
            rank        = excluded.rank,
            rating      = excluded.rating,
            note        = excluded.note,
            created_at  = excluded.created_at
        """,
        (fb.feedback_id, fb.query_id, fb.meme_id, fb.rank, fb.rating, fb.note, fb.created_at),
    )
    conn.commit()


def list_feedback(conn: sqlite3.Connection, query_id: str) -> list[FeedbackEvent]:
    rows = conn.execute(
        "SELECT * FROM feedback_events WHERE query_id = %s ORDER BY created_at", (query_id,)
    ).fetchall()
    return [
        FeedbackEvent(
            feedback_id=r["feedback_id"],
            query_id=r["query_id"],
            meme_id=r["meme_id"],
            rank=r["rank"],
            rating=r["rating"],
            note=r["note"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ── vlm_calls（NVIDIA VLM 用量紀錄）────────────────────────────────────


def insert_vlm_call(conn: sqlite3.Connection, rec: dict) -> None:
    """寫入一筆 VLM 呼叫紀錄（rec 為 NvidiaVlm log callback 傳來的欄位）。"""
    conn.execute(
        """
        INSERT INTO vlm_calls (call_id, created_at, key_id, model, task, meme_id,
                               status, latency_ms, prompt_tokens, completion_tokens, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            new_id("vc"),
            _now_iso(),
            rec.get("key_id"),
            rec.get("model"),
            rec.get("task"),
            rec.get("meme_id"),
            rec.get("status"),
            rec.get("latency_ms"),
            rec.get("prompt_tokens"),
            rec.get("completion_tokens"),
            rec.get("error"),
        ),
    )
    conn.commit()


def vlm_call_stats(conn: sqlite3.Connection) -> list[dict]:
    """各 key × 狀態的呼叫數與平均延遲（監控哪把 key 被打爆 / 限流率）。"""
    rows = conn.execute(
        """
        SELECT key_id, status, COUNT(*) AS n, AVG(latency_ms) AS avg_ms
        FROM vlm_calls
        GROUP BY key_id, status
        ORDER BY key_id, status
        """
    ).fetchall()
    return [dict(r) for r in rows]


# ── settings（後台可調的執行期設定：目前為各任務模型覆寫）──────────────────

# pipeline 會覆寫模型的五個任務；後台設定頁即以此為準
TASK_MODEL_KEYS = ("annotation", "intent", "rerank", "screenshot", "opponent")
_MODEL_PREFIX = "model:"


def get_task_models(conn: sqlite3.Connection) -> dict[str, str]:
    """回傳有設定覆寫的 {task: model_id}（未設定的任務不列入 → 呼叫端用 VLM 預設）。"""
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE %s", (_MODEL_PREFIX + "%",)
    ).fetchall()
    return {r["key"][len(_MODEL_PREFIX):]: r["value"] for r in rows if r["value"]}


def set_task_models(conn: sqlite3.Connection, mapping: dict[str, str | None]) -> None:
    """設定各任務模型；值為 None / 空字串 = 刪除該覆寫（回 VLM 預設）。"""
    for task, model in mapping.items():
        key = _MODEL_PREFIX + task
        if model:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, %s)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
                """,
                (key, model, _now_iso()),
            )
        else:
            conn.execute("DELETE FROM settings WHERE key = %s", (key,))
    conn.commit()


# ── events（輕量行為事件）+ 排行榜 ─────────────────────────────────────


def insert_event(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    client_id: str | None = None,
    meme_id: str | None = None,
    meta: object | None = None,
) -> None:
    """記一筆行為事件（下載 / 選分類 等）。best-effort，不擋主流程。"""
    conn.execute(
        "INSERT INTO events (event_id, event_type, client_id, meme_id, meta, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (new_id("ev"), event_type, client_id, meme_id,
         _dumps(meta) if meta is not None else None, _now_iso()),
    )
    conn.commit()


def leaderboard(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """熱門梗圖榜：綜合熱度 = 讚×3 + 下載；讚 = 推薦回饋讚 + 圖庫愛心讚。
    只列有互動者（資料少時自然短/空）。"""
    rows = conn.execute(
        """
        SELECT m.meme_id, a.ocr_text, a.franchise,
               COALESCE(fb.n, 0) + COALESCE(gl.n, 0) AS likes,
               COALESCE(dl.n, 0) AS downloads,
               (COALESCE(fb.n, 0) + COALESCE(gl.n, 0)) * 3 + COALESCE(dl.n, 0) AS score
        FROM memes m
        JOIN meme_annotations a ON a.meme_id = m.meme_id
        LEFT JOIN (SELECT meme_id, COUNT(*) AS n FROM feedback_events
                   WHERE rating = 'up' GROUP BY meme_id) fb ON fb.meme_id = m.meme_id
        LEFT JOIN (SELECT meme_id, COUNT(*) AS n FROM meme_likes
                   GROUP BY meme_id) gl ON gl.meme_id = m.meme_id
        LEFT JOIN (SELECT meme_id, COUNT(*) AS n FROM events
                   WHERE event_type = 'download' GROUP BY meme_id) dl ON dl.meme_id = m.meme_id
        WHERE m.status = 'active'
          AND (COALESCE(fb.n, 0) + COALESCE(gl.n, 0) + COALESCE(dl.n, 0)) > 0
        ORDER BY score DESC, m.meme_id
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── 探索圖庫：按讚 / 彈幕留言 ────────────────────────────────────────────


def toggle_like(conn: sqlite3.Connection, meme_id: str, client_id: str) -> dict:
    """對一張圖按讚 / 取消讚（同一 client 對同一圖最多一讚）。回傳新的讚數與狀態。

    原子切換：先試刪（刪到＝原本有讚→取消），沒刪到就插入。INSERT 帶 ON CONFLICT DO NOTHING，
    擋掉並發雙擊時「兩個請求都判定沒讚過→都插入」撞 UNIQUE(meme_id,client_id) 拋 500 的競態。
    """
    deleted = conn.execute(
        "DELETE FROM meme_likes WHERE meme_id = %s AND client_id = %s", (meme_id, client_id)
    ).rowcount
    if deleted:
        liked = False
    else:
        conn.execute(
            "INSERT INTO meme_likes (meme_id, client_id, created_at) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (meme_id, client_id, _now_iso()))
        liked = True
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM meme_likes WHERE meme_id = %s", (meme_id,)).fetchone()["n"]
    return {"likes": n, "liked": liked}


def list_gallery(
    conn: sqlite3.Connection, *, seed: str, offset: int, limit: int,
    client_id: str, exclude_nsfw: bool = True,
) -> list[dict]:
    """探索圖庫一頁：active 且是梗圖（可選排除 NSFW）；隨機但依 seed 穩定分頁。

    回傳每張圖的尺寸（供瀑布流）、讚數/留言數、以及此 client 是否已讚。
    """
    nsfw = "AND a.nsfw = 0" if exclude_nsfw else ""
    # 讚數/留言數/是否已讚都用相關子查詢：目標清單在 ORDER BY+LIMIT 之後才投影，故只對這一頁的
    # 24 列各查一次（走 idx_meme_likes_meme / idx_meme_comments_meme 的單圖索引）。原本兩個
    # LEFT JOIN(GROUP BY) 會把整張 meme_likes / meme_comments 全表聚合，隨互動量成長越來越貴。
    rows = conn.execute(
        f"""
        SELECT m.meme_id, m.width, m.height, a.ocr_text, a.franchise,
               (SELECT COUNT(*) FROM meme_likes l WHERE l.meme_id = m.meme_id) AS likes,
               (SELECT COUNT(*) FROM meme_comments c WHERE c.meme_id = m.meme_id) AS comments,
               EXISTS (SELECT 1 FROM meme_likes l2
                       WHERE l2.meme_id = m.meme_id AND l2.client_id = %s) AS liked
        FROM memes m
        JOIN meme_annotations a ON a.meme_id = m.meme_id
        WHERE m.status = 'active' AND a.is_meme = 1 {nsfw}
        ORDER BY md5(m.meme_id || %s)
        LIMIT %s OFFSET %s
        """,
        (client_id or "", seed, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def add_favorite(conn: sqlite3.Connection, user_id: str, meme_id: str) -> None:
    conn.execute(
        "INSERT INTO meme_favorites (user_id, meme_id, created_at) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, meme_id) DO NOTHING",
        (user_id, meme_id, _now_iso()),
    )
    conn.commit()


def remove_favorite(conn: sqlite3.Connection, user_id: str, meme_id: str) -> None:
    conn.execute(
        "DELETE FROM meme_favorites WHERE user_id = %s AND meme_id = %s", (user_id, meme_id)
    )
    conn.commit()


def is_favorited(conn: sqlite3.Connection, user_id: str, meme_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM meme_favorites WHERE user_id = %s AND meme_id = %s", (user_id, meme_id)
    ).fetchone()
    return row is not None


def list_favorites(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """使用者收藏的梗圖（新到舊），GalleryItem 形狀（image_url 由端點層補）。"""
    rows = conn.execute(
        """
        SELECT m.meme_id, m.width, m.height, a.ocr_text, a.franchise,
               COALESCE(lk.n, 0) AS likes, COALESCE(cm.n, 0) AS comments,
               FALSE AS liked, TRUE AS favorited
        FROM meme_favorites f
        JOIN memes m ON m.meme_id = f.meme_id
        JOIN meme_annotations a ON a.meme_id = m.meme_id
        LEFT JOIN (SELECT meme_id, COUNT(*) AS n FROM meme_likes
                   GROUP BY meme_id) lk ON lk.meme_id = m.meme_id
        LEFT JOIN (SELECT meme_id, COUNT(*) AS n FROM meme_comments
                   GROUP BY meme_id) cm ON cm.meme_id = m.meme_id
        WHERE f.user_id = %s AND m.status = 'active'
        ORDER BY f.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_gallery_item(conn: sqlite3.Connection, meme_id: str, *, client_id: str = "") -> dict | None:
    """單張梗圖的探索卡資料（給 detail 頁 / 分享用）；非 active 或非梗圖回 None。"""
    row = conn.execute(
        """
        SELECT m.meme_id, m.width, m.height, a.ocr_text, a.franchise, a.description,
               (SELECT COUNT(*) FROM meme_likes l WHERE l.meme_id = m.meme_id) AS likes,
               (SELECT COUNT(*) FROM meme_comments c WHERE c.meme_id = m.meme_id) AS comments,
               EXISTS (SELECT 1 FROM meme_likes l2
                       WHERE l2.meme_id = m.meme_id AND l2.client_id = %s) AS liked
        FROM memes m
        JOIN meme_annotations a ON a.meme_id = m.meme_id
        WHERE m.meme_id = %s AND m.status = 'active' AND a.is_meme = 1
        """,
        (client_id or "", meme_id),
    ).fetchone()
    return dict(row) if row else None


def add_comment(
    conn: sqlite3.Connection, meme_id: str, client_id: str, author_name: str, text: str
) -> dict:
    """新增一則彈幕留言（擁有者 client_id、顯示暱稱快照 author_name）。"""
    comment_id = new_id("cmt")
    now = _now_iso()
    conn.execute(
        "INSERT INTO meme_comments (comment_id, meme_id, client_id, author_name, text, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (comment_id, meme_id, client_id, author_name, text, now),
    )
    conn.commit()
    return {"comment_id": comment_id, "author_name": author_name, "text": text,
            "created_at": now, "edited": False, "mine": True}


def list_comments(
    conn: sqlite3.Connection, meme_id: str, client_id: str | None = None
) -> list[dict]:
    """某梗圖的所有彈幕留言（舊到新）；mine 標記是否為此 client 所留。"""
    rows = conn.execute(
        "SELECT comment_id, author_name, text, created_at, updated_at, client_id "
        "FROM meme_comments WHERE meme_id = %s ORDER BY created_at",
        (meme_id,),
    ).fetchall()
    return [
        {"comment_id": r["comment_id"], "author_name": r["author_name"], "text": r["text"],
         "created_at": r["created_at"], "edited": r["updated_at"] is not None,
         "mine": client_id is not None and r["client_id"] == client_id}
        for r in rows
    ]


def update_comment(
    conn: sqlite3.Connection, comment_id: str, client_id: str, text: str
) -> bool:
    """編修自己的留言（client_id 需相符）。回傳是否有更新到。"""
    cur = conn.execute(
        "UPDATE meme_comments SET text = %s, updated_at = %s "
        "WHERE comment_id = %s AND client_id = %s",
        (text, _now_iso(), comment_id, client_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_comment(conn: sqlite3.Connection, comment_id: str, client_id: str) -> bool:
    """刪除自己的留言（client_id 需相符）。回傳是否有刪到。"""
    cur = conn.execute(
        "DELETE FROM meme_comments WHERE comment_id = %s AND client_id = %s",
        (comment_id, client_id),
    )
    conn.commit()
    return cur.rowcount > 0


def set_user_nickname(conn: sqlite3.Connection, user_id: str, nickname: str) -> None:
    """設定登入使用者的顯示暱稱。"""
    conn.execute("UPDATE users SET nickname = %s WHERE user_id = %s", (nickname, user_id))
    conn.commit()


# ── client_errors（前台錯誤回報，供後台 debug）──────────────────────────


def insert_client_error(
    conn: sqlite3.Connection,
    *,
    message: str,
    stack: str | None = None,
    url: str | None = None,
    user_agent: str | None = None,
    client_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO client_errors "
        "(error_id, message, stack, url, user_agent, client_id, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (new_id("cerr"), message, stack, url, user_agent, client_id, _now_iso()),
    )
    conn.commit()


def list_chat_feedback(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """後台：梗友回覆的評價（新到舊），含觸發訊息與梗圖 OCR，供優化選圖。"""
    rows = conn.execute(
        """
        SELECT e.event_id, e.client_id, e.meme_id, e.meta, e.created_at,
               a.ocr_text, a.franchise
        FROM events e
        LEFT JOIN meme_annotations a ON a.meme_id = e.meme_id
        WHERE e.event_type = 'chat_feedback'
        ORDER BY e.created_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        meta = _loads(r["meta"]) if r["meta"] else {}
        meta = meta if isinstance(meta, dict) else {}
        out.append({
            "event_id": r["event_id"], "client_id": r["client_id"], "meme_id": r["meme_id"],
            "rating": meta.get("rating"), "message": meta.get("message"),
            "ocr_text": r["ocr_text"], "franchise": r["franchise"], "created_at": r["created_at"],
        })
    return out


def list_client_errors(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    """後台：最近的前台錯誤（新到舊）。"""
    rows = conn.execute(
        "SELECT error_id, message, stack, url, user_agent, client_id, created_at "
        "FROM client_errors ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def insert_bug_report(
    conn: sqlite3.Connection,
    *,
    description: str,
    breadcrumbs: object | None = None,
    url: str | None = None,
    user_agent: str | None = None,
    client_id: str | None = None,
    meta: object | None = None,
) -> None:
    """使用者主動回報：描述 + 操作麵包屑 + 裝置資訊。"""
    conn.execute(
        "INSERT INTO bug_reports "
        "(report_id, description, breadcrumbs, url, user_agent, client_id, meta, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            new_id("bug"), description,
            _dumps(breadcrumbs) if breadcrumbs is not None else None,
            url, user_agent, client_id,
            _dumps(meta) if meta is not None else None, _now_iso(),
        ),
    )
    conn.commit()


def insert_textless_sample(
    conn: sqlite3.Connection,
    *,
    embedding: object | None,
    labels: object,
    model_version: str,
    client_id: str | None = None,
) -> None:
    """飛輪訓練集：沒字圖的 (影像 embedding, VLM 標籤)。隱私：只存向量+標籤，不存原圖。"""
    conn.execute(
        "INSERT INTO textless_samples "
        "(sample_id, embedding, labels, model_version, client_id, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            new_id("tls"),
            _dumps(embedding) if embedding is not None else None,
            _dumps(labels), model_version, client_id, _now_iso(),
        ),
    )
    conn.commit()


def count_textless_samples(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM textless_samples").fetchone()["n"]


def list_bug_reports(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """後台：使用者回報的問題（新到舊），breadcrumbs / meta 解回物件。"""
    rows = conn.execute(
        "SELECT report_id, description, breadcrumbs, url, user_agent, client_id, meta, created_at "
        "FROM bug_reports ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        crumbs = _loads(r["breadcrumbs"]) if r["breadcrumbs"] else []
        d["breadcrumbs"] = crumbs if isinstance(crumbs, list) else []
        meta = _loads(r["meta"]) if r["meta"] else {}
        d["meta"] = meta if isinstance(meta, dict) else {}
        out.append(d)
    return out


def list_reported_memes(conn: sqlite3.Connection) -> list[dict]:
    """後台檢舉清單：仍未處理（event_type=report）的梗圖，依 distinct 檢舉人數排序。"""
    rows = conn.execute(
        """
        SELECT m.meme_id, a.ocr_text, a.franchise, m.status,
               COUNT(DISTINCT e.client_id) AS reports,
               MAX(e.created_at) AS last_reported
        FROM events e
        JOIN memes m ON m.meme_id = e.meme_id
        LEFT JOIN meme_annotations a ON a.meme_id = m.meme_id
        WHERE e.event_type = 'report'
        GROUP BY m.meme_id, a.ocr_text, a.franchise, m.status
        ORDER BY reports DESC, last_reported DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def resolve_reports(conn: sqlite3.Connection, meme_id: str) -> None:
    """把該梗圖的檢舉標記為已處理（改 event_type，保留列供審計），清出待辦清單。"""
    conn.execute(
        "UPDATE events SET event_type = 'report_resolved' "
        "WHERE meme_id = %s AND event_type = 'report'",
        (meme_id,),
    )
    conn.commit()


# ── users（Google 登入的使用者）────────────────────────────────────────


def upsert_user(
    conn: sqlite3.Connection,
    *,
    google_sub: str,
    email: str | None,
    name: str | None,
    picture: str | None,
) -> dict:
    """依 Google 唯一 ID 建立或更新使用者，回傳整列（含 user_id）。"""
    now = _now_iso()
    row = conn.execute(
        """
        INSERT INTO users
            (user_id, google_sub, email, name, picture, role, created_at, last_login_at)
        VALUES (%s, %s, %s, %s, %s, 'user', %s, %s)
        ON CONFLICT (google_sub) DO UPDATE SET
            email = EXCLUDED.email,
            name = EXCLUDED.name,
            picture = EXCLUDED.picture,
            last_login_at = EXCLUDED.last_login_at
        RETURNING *
        """,
        (new_id("u"), google_sub, email, name, picture, now, now),
    ).fetchone()
    conn.commit()
    return dict(row)


def get_user(conn: sqlite3.Connection, user_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE user_id = %s", (user_id,)).fetchone()
    return dict(row) if row else None


def set_meme_uploaded_by(conn: sqlite3.Connection, meme_id: str, user_id: str) -> None:
    """把梗圖歸屬到上傳的使用者（共用圖庫貢獻）。"""
    conn.execute("UPDATE memes SET uploaded_by = %s WHERE meme_id = %s", (user_id, meme_id))
    conn.commit()


def count_uploads_today(conn: sqlite3.Connection, user_id: str) -> int:
    """某使用者今天（UTC）上傳的張數；供每日上傳配額判斷（含被拒者，防洗版）。"""
    today = _now_iso()[:10]
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM memes WHERE uploaded_by = %s AND LEFT(first_seen_at, 10) = %s",
        (user_id, today),
    ).fetchone()
    return row["n"]


# ── tasks（非同步推薦任務）──────────────────────────────────────────────


def create_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    client_id: str,
    input_type: str,
    label: str,
    created_at: str | None = None,
) -> None:
    """建立一筆 pending 任務（背景執行前）。"""
    now = created_at or _now_iso()
    conn.execute(
        """
        INSERT INTO tasks (task_id, client_id, input_type, label, status,
                           created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'pending', %s, %s)
        """,
        (task_id, client_id, input_type, label, now, now),
    )
    conn.commit()


def set_task_status(
    conn: sqlite3.Connection,
    task_id: str,
    status: str,
    *,
    result: object | None = None,
    error: str | None = None,
    only_if_not: str | None = None,
) -> None:
    """更新任務狀態；done 時附 result，error 時附 error 訊息。

    ``only_if_not``：僅在目前狀態不等於它時才更新（背景任務跑完不覆寫已取消的任務）。
    """
    sql = (
        "UPDATE tasks SET status = %s, result = %s, error = %s, updated_at = %s WHERE task_id = %s"
    )
    params = [status, _dumps(result) if result is not None else None, error, _now_iso(), task_id]
    if only_if_not is not None:
        sql += " AND status != %s"
        params.append(only_if_not)
    conn.execute(sql, params)
    conn.commit()


def cancel_task(conn: sqlite3.Connection, task_id: str, client_id: str) -> bool:
    """使用者取消進行中的任務（僅本人、僅 pending/running）；回傳是否有取消到。"""
    cur = conn.execute(
        "UPDATE tasks SET status = 'cancelled', updated_at = %s "
        "WHERE task_id = %s AND client_id = %s AND status IN ('pending', 'running')",
        (_now_iso(), task_id, client_id),
    )
    conn.commit()
    return cur.rowcount > 0


def abort_orphan_tasks(conn: sqlite3.Connection) -> int:
    """把殘留的 pending/running 任務標成 error（背景 ThreadPool 不跨程序重啟）。

    啟動時呼叫，避免服務重部署後前台永遠輪詢一個永不完成的 running 任務。回傳受影響筆數。
    """
    cur = conn.execute(
        "UPDATE tasks SET status = 'error', error = %s, updated_at = %s "
        "WHERE status IN ('pending', 'running')",
        ("服務重啟，任務中斷，請重新送出", _now_iso()),
    )
    conn.commit()
    return cur.rowcount


def get_task(conn: sqlite3.Connection, task_id: str) -> dict | None:
    """讀單一任務（含完整 result）；查無回 None。"""
    row = conn.execute("SELECT * FROM tasks WHERE task_id = %s", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    task["result"] = _loads(task["result"])
    return task


def list_tasks_by_client(
    conn: sqlite3.Connection, client_id: str, limit: int = 50
) -> list[dict]:
    """某 client 的歷史任務（新到舊）；不夾帶完整 result，只標記是否已完成。"""
    rows = conn.execute(
        """
        SELECT task_id, client_id, input_type, label, status, error,
               created_at, updated_at, (result IS NOT NULL) AS has_result
        FROM tasks
        WHERE client_id = %s
        ORDER BY created_at DESC, task_id DESC
        LIMIT %s
        """,
        (client_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def count_tasks_today(conn: sqlite3.Connection, client_id: str) -> int:
    """某 client 今天（UTC）建立的任務數；供未登入者每日配額判斷。"""
    today = _now_iso()[:10]  # YYYY-MM-DD
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE client_id = %s AND LEFT(created_at, 10) = %s",
        (client_id, today),
    ).fetchone()
    return row["n"]
