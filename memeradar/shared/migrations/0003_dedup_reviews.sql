-- 0003_dedup_reviews: 去重人工複核佇列（docs/02 §4 L3 灰色地帶 0.92–0.97）
-- P4-2 複核頁裁決：merged（合併為重複）或 distinct（確為不同梗，如同模板不同字）。

CREATE TABLE dedup_reviews (
    review_id       TEXT PRIMARY KEY,
    meme_id         TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
    matched_meme_id TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
    layer           TEXT NOT NULL,
    score           REAL,
    resolution      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (resolution IN ('pending', 'merged', 'distinct')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_dedup_reviews_pending ON dedup_reviews (resolution);
