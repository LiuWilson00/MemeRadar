-- 0001_initial: 依 docs/01-architecture.md §4 概念模型落地
-- JSON 欄位以 TEXT 儲存；時間為 ISO 8601 TEXT。
-- 向量在 Demo 階段存 embeddings.vector（JSON），正式向量索引於 P1-5 定案後外掛。

CREATE TABLE memes (
    meme_id       TEXT PRIMARY KEY,
    image_uri     TEXT NOT NULL,
    sha256        TEXT NOT NULL UNIQUE,
    phash         TEXT,
    width         INTEGER,
    height        INTEGER,
    hotness       REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'pending_review', 'removed')),
    first_seen_at TEXT NOT NULL
);

CREATE INDEX idx_memes_status ON memes (status);
CREATE INDEX idx_memes_phash ON memes (phash);

CREATE TABLE meme_sources (
    source_id    TEXT PRIMARY KEY,
    meme_id      TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
    platform     TEXT NOT NULL,
    post_url     TEXT,
    post_title   TEXT,
    top_comments TEXT NOT NULL DEFAULT '[]',
    upvotes      INTEGER,
    posted_at    TEXT,
    crawled_at   TEXT NOT NULL
);

CREATE INDEX idx_sources_meme ON meme_sources (meme_id);

CREATE TABLE meme_annotations (
    meme_id       TEXT PRIMARY KEY REFERENCES memes (meme_id) ON DELETE CASCADE,
    model_version TEXT NOT NULL,
    is_meme       INTEGER NOT NULL DEFAULT 1,
    nsfw          INTEGER NOT NULL DEFAULT 0,
    ocr_text      TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    characters    TEXT NOT NULL DEFAULT '[]',
    franchise     TEXT,
    template_name TEXT,
    emotions      TEXT NOT NULL DEFAULT '[]',
    usage_hints   TEXT NOT NULL DEFAULT '[]',
    categories    TEXT NOT NULL DEFAULT '[]',
    confidence    REAL,
    annotated_at  TEXT NOT NULL
);

CREATE INDEX idx_annotations_franchise ON meme_annotations (franchise);
CREATE INDEX idx_annotations_template ON meme_annotations (template_name);

CREATE TABLE embeddings (
    meme_id    TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('text_retrieval', 'image_dedup')),
    model      TEXT NOT NULL,
    vector     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (meme_id, kind, model)
);

CREATE TABLE recommendation_logs (
    query_id        TEXT PRIMARY KEY,
    conversation    TEXT NOT NULL,
    intent_result   TEXT,
    params_snapshot TEXT NOT NULL,
    candidates      TEXT,
    final_results   TEXT,
    latency_ms      INTEGER,
    created_at      TEXT NOT NULL
);

CREATE TABLE feedback_events (
    feedback_id TEXT PRIMARY KEY,
    query_id    TEXT NOT NULL REFERENCES recommendation_logs (query_id) ON DELETE CASCADE,
    meme_id     TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
    rank        INTEGER NOT NULL,
    rating      TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_feedback_query ON feedback_events (query_id);
CREATE INDEX idx_feedback_meme ON feedback_events (meme_id);
