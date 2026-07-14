"""探索圖庫：使用者對梗圖按讚（meme_likes）+ 彈幕留言（meme_comments）+ 使用者暱稱。

讚以 (meme_id, client_id) 唯一、可取消；留言記擁有者 client_id 與顯示暱稱快照。

Revision ID: 0006_gallery
Revises: 0005_meme_uploaded_by
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op

revision = "0006_gallery"
down_revision = "0005_meme_uploaded_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meme_likes (
            meme_id    TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
            client_id  TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (meme_id, client_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_meme_likes_meme ON meme_likes (meme_id)")
    op.execute(
        """
        CREATE TABLE meme_comments (
            comment_id  TEXT PRIMARY KEY,
            meme_id     TEXT NOT NULL REFERENCES memes (meme_id) ON DELETE CASCADE,
            client_id   TEXT NOT NULL,
            author_name TEXT NOT NULL,
            text        TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT
        )
        """
    )
    op.execute("CREATE INDEX idx_meme_comments_meme ON meme_comments (meme_id)")
    op.execute("ALTER TABLE users ADD COLUMN nickname TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS nickname")
    op.execute("DROP TABLE IF EXISTS meme_comments")
    op.execute("DROP TABLE IF EXISTS meme_likes")
