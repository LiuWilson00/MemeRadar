-- 0004_crawl_health: 來源健康度（docs/02 §6：連續 3 次失敗告警）

CREATE TABLE crawl_health (
    source               TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT,
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
