# Reddit API 存取申請（英文草稿）

送件入口：https://support.reddithelp.com/hc/en-us/requests/new?ticket_form_id=14868593862164
（Reddit Help → Developer Platform → API access request；表單欄位貼下方對應段落）

---

**Use case title**: Non-commercial research: meme-response recommendation prototype

**Description of your app / use case**:

I am building MemeRadar, a personal, non-commercial research prototype that
recommends reaction memes for chat conversations (Traditional Chinese UI).
I would like read-only API access to fetch image posts and their public
metadata (title, upvote count, top comments) from a small set of meme
subreddits (e.g. r/memes, r/dankmemes), strictly within the free-tier rate
limits (≤100 QPM via OAuth), using PRAW with a descriptive User-Agent.

**How will you use the data?**

- Images and metadata are stored locally only, for retrieval/ranking in a
  personal demo. Nothing is republished or redistributed.
- Upvote counts inform a decaying "hotness" score; titles/comments provide
  annotation context.
- No model training: Reddit data will NOT be used to train or fine-tune any
  AI/ML model. Annotation is done by a commercial LLM API at inference time
  only (per-image labeling), not for training.
- Deletion sync: if content is removed from Reddit, the corresponding local
  copy and metadata are deleted on the next sync.
- The project is not monetized in any form.

**Expected volume**: one scheduled fetch per day, ~100–200 posts/day maximum.

**Contact**: dev@remotenc.com

---

送出後把 ticket 編號記在這裡：＿＿＿＿＿＿
核准後：`.env` 填 `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`，
跑 `python -m memeradar.ingestion.pipeline --client praw` 即恢復自動管線。
若核准條件與上述聲明不符（例如仍禁止 LLM 標註），先回報再啟用。
