-- 0011_settings: 後台可調的執行期設定（key-value）。
-- 目前用途：各任務的 NVIDIA 模型覆寫（key = 'model:<task>'）；無該筆 = 用 VLM 預設。
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
