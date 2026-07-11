"""資料存取層：dataclass ↔ SQLite 的寫讀，含 JSON 欄位序列化。

所有寫入函式自行 commit；呼叫端只需持有 ``db.connect()`` 的連線。
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
                           hotness, status, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    )


def get_meme(conn: sqlite3.Connection, meme_id: str) -> Meme | None:
    row = conn.execute("SELECT * FROM memes WHERE meme_id = ?", (meme_id,)).fetchone()
    return _row_to_meme(row) if row else None


def find_meme_by_sha256(conn: sqlite3.Connection, sha256: str) -> Meme | None:
    row = conn.execute("SELECT * FROM memes WHERE sha256 = ?", (sha256,)).fetchone()
    return _row_to_meme(row) if row else None


def set_status(conn: sqlite3.Connection, meme_id: str, status: str) -> None:
    conn.execute("UPDATE memes SET status = ? WHERE meme_id = ?", (status, meme_id))
    conn.commit()


def add_hotness(conn: sqlite3.Connection, meme_id: str, delta: float) -> None:
    conn.execute("UPDATE memes SET hotness = hotness + ? WHERE meme_id = ?", (delta, meme_id))
    conn.commit()


def count_memes(conn: sqlite3.Connection, status: str | None = None) -> int:
    if status is None:
        row = conn.execute("SELECT COUNT(*) AS n FROM memes").fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM memes WHERE status = ?", (status,)).fetchone()
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
        sql += " LIMIT ?"
        params = (limit,)
    return [_row_to_meme(r) for r in conn.execute(sql, params).fetchall()]


def list_memes_missing_embedding(
    conn: sqlite3.Connection, kind: str, model: str, limit: int | None = None
) -> list[Meme]:
    """列出已標註為梗圖、狀態 active、但缺少指定簽名向量的梗圖（向量化工作佇列）。"""
    sql = """
        SELECT m.* FROM memes m
        JOIN meme_annotations a ON a.meme_id = m.meme_id
        LEFT JOIN embeddings e
            ON e.meme_id = m.meme_id AND e.kind = ? AND e.model = ?
        WHERE e.meme_id IS NULL AND m.status = 'active' AND a.is_meme = 1
        ORDER BY m.first_seen_at
    """
    params: tuple = (kind, model)
    if limit is not None:
        sql += " LIMIT ?"
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "SELECT * FROM meme_annotations WHERE meme_id = ?", (meme_id,)
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


# ── meme_sources ─────────────────────────────────────────────────────


def add_source(conn: sqlite3.Connection, src: MemeSource) -> None:
    conn.execute(
        """
        INSERT INTO meme_sources (source_id, meme_id, platform, post_url, post_title,
                                  top_comments, upvotes, posted_at, crawled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "SELECT * FROM meme_sources WHERE meme_id = ? ORDER BY crawled_at", (meme_id,)
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
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (meme_id, kind, model) DO UPDATE SET
            vector = excluded.vector,
            created_at = excluded.created_at
        """,
        (emb.meme_id, emb.kind, emb.model, _dumps(emb.vector), emb.created_at),
    )
    conn.commit()


def get_embeddings(
    conn: sqlite3.Connection, meme_id: str, kind: str | None = None
) -> list[Embedding]:
    if kind is None:
        rows = conn.execute("SELECT * FROM embeddings WHERE meme_id = ?", (meme_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM embeddings WHERE meme_id = ? AND kind = ?", (meme_id, kind)
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
                                         latency_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log.query_id,
            _dumps(log.conversation),
            _dumps(log.intent_result) if log.intent_result is not None else None,
            _dumps(log.params_snapshot),
            _dumps(log.candidates) if log.candidates is not None else None,
            _dumps(log.final_results) if log.final_results is not None else None,
            log.latency_ms,
            log.created_at,
        ),
    )
    conn.commit()


def get_recommendation_log(conn: sqlite3.Connection, query_id: str) -> RecommendationLog | None:
    row = conn.execute(
        "SELECT * FROM recommendation_logs WHERE query_id = ?", (query_id,)
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
        created_at=row["created_at"],
    )


# ── feedback_events ──────────────────────────────────────────────────


def insert_feedback(conn: sqlite3.Connection, fb: FeedbackEvent) -> None:
    conn.execute(
        """
        INSERT INTO feedback_events (feedback_id, query_id, meme_id, rank, rating,
                                     note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (fb.feedback_id, fb.query_id, fb.meme_id, fb.rank, fb.rating, fb.note, fb.created_at),
    )
    conn.commit()


def list_feedback(conn: sqlite3.Connection, query_id: str) -> list[FeedbackEvent]:
    rows = conn.execute(
        "SELECT * FROM feedback_events WHERE query_id = ? ORDER BY created_at", (query_id,)
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
