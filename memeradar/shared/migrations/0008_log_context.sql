-- 0008_log_context: 推薦紀錄補上下文，供未來以回饋做優化
-- input_type：文字 / 截圖 / 對方梗圖（三種輸入的推薦動態不同，優化時要分開看）
-- client_id：localStorage 隨機匿名碼（無個資），供分群 / per-user 分析
-- （LLM 模型記在 params_snapshot.models，比照 embedding_signature，不另開欄。）

ALTER TABLE recommendation_logs ADD COLUMN input_type TEXT;
ALTER TABLE recommendation_logs ADD COLUMN client_id TEXT;

CREATE INDEX idx_logs_client ON recommendation_logs (client_id);
