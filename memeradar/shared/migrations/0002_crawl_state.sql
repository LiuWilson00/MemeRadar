-- 0002_crawl_state: 爬蟲水位（docs/02 §6 增量抓取）
-- 每來源記錄上次成功抓取的位置（語意由 adapter 自訂，Reddit 為貼文時間 ISO）。

CREATE TABLE crawl_state (
    source     TEXT PRIMARY KEY,
    watermark  TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
