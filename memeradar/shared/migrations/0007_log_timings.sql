-- 0007_log_timings: 推薦紀錄存分階段耗時（意圖 / 檢索 / 重排 / 總計）
-- 之前只存 latency_ms（總延遲），看不出哪階段慢；存 JSON 後可持續監控。

ALTER TABLE recommendation_logs ADD COLUMN timings TEXT;
