-- 0006_feedback_unique: 回饋對「同一查詢的同一張圖」冪等（改投以最新為準）
-- 避免使用者改投（讚→倒讚）在報表重複計數。
-- 先移除既有重複（保留每組最新插入的一筆，rowid 隨插入遞增），再建唯一索引。

DELETE FROM feedback_events
WHERE rowid NOT IN (
    SELECT MAX(rowid) FROM feedback_events GROUP BY query_id, meme_id
);

CREATE UNIQUE INDEX idx_feedback_query_meme ON feedback_events (query_id, meme_id);
