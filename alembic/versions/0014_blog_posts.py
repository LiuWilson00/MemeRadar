"""blog_posts：每日一梗的考據專欄。

一篇文章 = 一張梗圖的來龍去脈。調研結果與文章存在一起，因為審核者要看的正是
「這段話有沒有來源撐著」——把 verdict / confidence / sources / unverified_claims 拆成
獨立欄位（而非埋在 JSON 裡）才查得動，發布閘門也才寫得出 SQL。

status 的三態：
- draft     調研信心不足，等人工看過（低信心不自動上線，見 docs/PoC：連 sonnet 都會
            偶爾編出連不上的網址）
- published 已上線，公開頁看得到
- rejected  人工判定不可用（保留不刪，避免同一張圖被重複選中再寫一次）

meme_id 上 UNIQUE：同一張梗圖只寫一次，選圖時直接靠這張表排除已寫過的。
"""

from alembic import op

revision = "0014_blog_posts"
down_revision = "0013_task_error_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE blog_posts (
            post_id       TEXT PRIMARY KEY,
            slug          TEXT NOT NULL UNIQUE,   -- 網址用，人看得懂
            meme_id       TEXT NOT NULL UNIQUE,   -- 一圖一文
            title         TEXT NOT NULL,
            article_html  TEXT NOT NULL,
            status        TEXT NOT NULL,          -- draft / published / rejected
            verdict       TEXT,                   -- identified / partial / unknown
            confidence    REAL,
            origin        TEXT,                   -- JSON：work/year/scene/characters/region
            caption_is_original INTEGER,          -- 1/0/NULL：圖上的字是否為原作台詞
            caption_note  TEXT,
            sources       TEXT,                   -- JSON array：{title,url,supports}
            unverified_claims TEXT,               -- JSON array：查不到證據、不得寫進正文的事
            model_version TEXT,                   -- 調研模型，供日後比較品質
            cost_usd      REAL,                   -- 該篇實際花費，供成本追蹤
            featured_on   TEXT,                   -- 預定/實際的「每日一梗」日期 YYYY-MM-DD
            created_at    TEXT NOT NULL,
            published_at  TEXT
        )
        """
    )
    # 公開頁只查 published 且依日期倒序 → 複合索引連排序一起吃掉
    op.execute(
        "CREATE INDEX idx_blog_status_featured ON blog_posts (status, featured_on)"
    )
    # 排程器每天問一次「今天有文了嗎」
    op.execute("CREATE INDEX idx_blog_featured_on ON blog_posts (featured_on)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_blog_featured_on")
    op.execute("DROP INDEX IF EXISTS idx_blog_status_featured")
    op.execute("DROP TABLE IF EXISTS blog_posts")
