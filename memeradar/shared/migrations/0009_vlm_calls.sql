-- 0009_vlm_calls: NVIDIA VLM 呼叫用量紀錄（監控哪把 key 被打爆 / 限流 / 延遲）
CREATE TABLE vlm_calls (
    call_id           TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    key_id            TEXT,          -- key 末 4 碼（不存完整 key）
    model             TEXT,
    task              TEXT,          -- annotate / screenshot / opponent
    meme_id           TEXT,
    status            TEXT,          -- ok / rate_limited / error / parse_fail
    latency_ms        INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    error             TEXT
);

CREATE INDEX idx_vlm_calls_key ON vlm_calls (key_id);
CREATE INDEX idx_vlm_calls_created ON vlm_calls (created_at);
