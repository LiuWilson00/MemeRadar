-- 0010_tasks: 非同步推薦任務（送出後背景執行，user 可離開再回來查進度/結果）
-- result 存完整推薦回應 JSON；歷史以 client_id 分群（我們已在推薦時記錄 client_id）。
CREATE TABLE tasks (
    task_id     TEXT PRIMARY KEY,
    client_id   TEXT,
    input_type  TEXT,          -- text / screenshot / meme_battle
    label       TEXT,          -- 給人看的短標題（對話首句 / 「梗圖大戰」等）
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending / running / done / error
    result      TEXT,          -- 完成時的推薦回應 JSON
    error       TEXT,          -- 失敗訊息
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX idx_tasks_client ON tasks (client_id, created_at DESC);
