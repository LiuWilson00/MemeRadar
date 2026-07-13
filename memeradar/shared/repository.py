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
    row = conn.execute("SELECT * FROM memes WHERE meme_id = %s", (meme_id,)).fetchone()
    return _row_to_meme(row) if row else None


def find_meme_by_sha256(conn: sqlite3.Connection, sha256: str) -> Meme | None:
    row = conn.execute("SELECT * FROM memes WHERE sha256 = %s", (sha256,)).fetchone()
    return _row_to_meme(row) if row else None


def set_status(conn: sqlite3.Connection, meme_id: str, status: str) -> None:
    conn.execute("UPDATE memes SET status = %s WHERE meme_id = %s", (status, meme_id))
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
    sql = """
        SELECT m.* FROM memes m
        LEFT JOIN meme_annotations a ON a.meme_id = m.meme_id
        WHERE a.meme_id IS NULL AND m.status != 'removed'
        ORDER BY m.first_seen_at
    """
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)
    return [_row_to_meme(r) for r in conn.execute(sql, params).fetchall()]


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
    sql = """
        SELECT m.* FROM memes m
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
) -> None:
    """更新任務狀態；done 時附 result，error 時附 error 訊息。"""
    conn.execute(
        "UPDATE tasks SET status = %s, result = %s, error = %s, updated_at = %s WHERE task_id = %s",
        (
            status,
            _dumps(result) if result is not None else None,
            error,
            _now_iso(),
            task_id,
        ),
    )
    conn.commit()


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
