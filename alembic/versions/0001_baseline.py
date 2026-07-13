"""baseline：以 SQLite 版 schema（migrations 0001–0011）為基準，落地到 PostgreSQL。

差異對應：
- JSON 文字欄位 → JSONB（characters/emotions/usage_hints/categories/top_comments、
  recommendation_logs 的各 JSON 欄、tasks.result）
- embeddings.vector（原 JSON 文字）→ pgvector ``vector(1024)`` + HNSW 餘弦索引
- REAL → DOUBLE PRECISION；is_meme/nsfw 沿用 0/1 INTEGER（減少程式改動）
- 時間欄位維持 TEXT（存 ISO 字串，_now_iso()）；DEFAULT 改用 now()::text
- schema_migrations 由 Alembic 的 alembic_version 取代，不建

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-13
"""
from __future__ import annotations

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    # ── memes（被多表參照，先建）──
    """
    CREATE TABLE memes (
        meme_id       TEXT PRIMARY KEY,
        image_uri     TEXT NOT NULL,
        sha256        TEXT NOT NULL UNIQUE,
        phash         TEXT,
        width         INTEGER,
        height        INTEGER,
        hotness       DOUBLE PRECISION NOT NULL DEFAULT 0,
        status        TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'pending_review', 'removed')),
        first_seen_at TEXT NOT NULL,
        engagement    DOUBLE PRECISION NOT NULL DEFAULT 0,
        last_seen_at  TEXT
    )
    """,
    """
    CREATE TABLE meme_annotations (
        meme_id       TEXT PRIMARY KEY REFERENCES memes (meme_id) ON DELETE CASCADE,
        model_version TEXT NOT NULL,
        is_meme       INTEGER NOT NULL DEFAULT 1,
        nsfw          INTEGER NOT NULL DEFAULT 0,
        ocr_text      TEXT NOT NULL DEFAULT '',
        description   TEXT NOT NULL DEFAULT '',
        characters    JSONB NOT NULL DEFAULT '[]'::jsonb,
        franchise     TEXT,
        template_name TEXT,
        emotions      JSONB NOT NULL DEFAULT '[]'::jsonb,
        usage_hints   JSONB NOT NULL DEFAULT '[]'::jsonb,
        categories    JSONB NOT NULL DEFAULT '[]'::jsonb,
        confidence    DOUBLE PRECISION,
        annotated_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE embeddings (
        meme_id    TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
        kind       TEXT NOT NULL CHECK (kind IN ('text_retrieval', 'image_dedup')),
        model      TEXT NOT NULL,
        vector     vector(1024) NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (meme_id, kind, model)
    )
    """,
    """
    CREATE TABLE meme_sources (
        source_id    TEXT PRIMARY KEY,
        meme_id      TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
        platform     TEXT NOT NULL,
        post_url     TEXT,
        post_title   TEXT,
        top_comments JSONB NOT NULL DEFAULT '[]'::jsonb,
        upvotes      INTEGER,
        posted_at    TEXT,
        crawled_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE recommendation_logs (
        query_id        TEXT PRIMARY KEY,
        conversation    JSONB NOT NULL,
        intent_result   JSONB,
        params_snapshot JSONB NOT NULL,
        candidates      JSONB,
        final_results   JSONB,
        latency_ms      INTEGER,
        created_at      TEXT NOT NULL,
        timings         JSONB,
        input_type      TEXT,
        client_id       TEXT
    )
    """,
    """
    CREATE TABLE feedback_events (
        feedback_id TEXT PRIMARY KEY,
        query_id    TEXT NOT NULL REFERENCES recommendation_logs (query_id) ON DELETE CASCADE,
        meme_id     TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
        rank        INTEGER NOT NULL,
        rating      TEXT NOT NULL CHECK (rating IN ('up', 'down')),
        note        TEXT,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE dedup_reviews (
        review_id       TEXT PRIMARY KEY,
        meme_id         TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
        matched_meme_id TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
        layer           TEXT NOT NULL,
        score           DOUBLE PRECISION,
        resolution      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (resolution IN ('pending', 'merged', 'distinct')),
        created_at      TEXT NOT NULL DEFAULT (now()::text)
    )
    """,
    """
    CREATE TABLE vlm_calls (
        call_id           TEXT PRIMARY KEY,
        created_at        TEXT NOT NULL,
        key_id            TEXT,
        model             TEXT,
        task              TEXT,
        meme_id           TEXT,
        status            TEXT,
        latency_ms        INTEGER,
        prompt_tokens     INTEGER,
        completion_tokens INTEGER,
        error             TEXT
    )
    """,
    """
    CREATE TABLE tasks (
        task_id     TEXT PRIMARY KEY,
        client_id   TEXT,
        input_type  TEXT,
        label       TEXT,
        status      TEXT NOT NULL DEFAULT 'pending',
        result      JSONB,
        error       TEXT,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE crawl_state (
        source     TEXT PRIMARY KEY,
        watermark  TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (now()::text)
    )
    """,
    """
    CREATE TABLE crawl_health (
        source               TEXT PRIMARY KEY,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        last_error           TEXT,
        updated_at           TEXT NOT NULL DEFAULT (now()::text)
    )
    """,
    # ── indexes ──
    "CREATE INDEX idx_annotations_franchise ON meme_annotations (franchise)",
    "CREATE INDEX idx_annotations_template ON meme_annotations (template_name)",
    "CREATE INDEX idx_dedup_reviews_pending ON dedup_reviews (resolution)",
    "CREATE INDEX idx_feedback_meme ON feedback_events (meme_id)",
    "CREATE INDEX idx_feedback_query ON feedback_events (query_id)",
    "CREATE UNIQUE INDEX idx_feedback_query_meme ON feedback_events (query_id, meme_id)",
    "CREATE INDEX idx_logs_client ON recommendation_logs (client_id)",
    "CREATE INDEX idx_memes_phash ON memes (phash)",
    "CREATE INDEX idx_memes_status ON memes (status)",
    "CREATE INDEX idx_sources_meme ON meme_sources (meme_id)",
    "CREATE INDEX idx_tasks_client ON tasks (client_id, created_at DESC)",
    "CREATE INDEX idx_vlm_calls_created ON vlm_calls (created_at)",
    "CREATE INDEX idx_vlm_calls_key ON vlm_calls (key_id)",
    # pgvector 餘弦相似度索引（HNSW）；only text_retrieval 會被檢索，加 partial 省空間
    "CREATE INDEX idx_embeddings_vector ON embeddings "
    "USING hnsw (vector vector_cosine_ops) WHERE kind = 'text_retrieval'",
]

_TABLES = [
    "crawl_health", "crawl_state", "settings", "tasks", "vlm_calls",
    "dedup_reviews", "feedback_events", "recommendation_logs", "meme_sources",
    "embeddings", "meme_annotations", "memes",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
