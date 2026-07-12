-- 0005_hotness_decay: 熱度衰減（docs/06 §3.1）
-- hotness 改為推導值（每日 job 重算），事實來源拆成兩欄：
--   engagement    互動總分 Σ(來源互動分)，只增不減
--   last_seen_at  最後一次出現時間（去重命中同圖再現時刷新）
-- 回填：既有 hotness 即歷史累積互動分；last_seen_at 以 first_seen_at 起算
-- （來源時間粒度不足，首個衰減週期會偏保守，之後由去重命中自然校正）。

ALTER TABLE memes ADD COLUMN engagement REAL NOT NULL DEFAULT 0;
ALTER TABLE memes ADD COLUMN last_seen_at TEXT;

UPDATE memes SET engagement = hotness, last_seen_at = first_seen_at;
