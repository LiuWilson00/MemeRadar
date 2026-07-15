"""meme_favorites：登入使用者的梗圖收藏（一人一圖一筆）。

與 meme_likes（匿名、client_id）不同——收藏綁登入帳號 user_id，跨裝置保留。

Revision ID: 0011_favorites
Revises: 0010_textless_samples
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op

revision = "0011_favorites"
down_revision = "0010_textless_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meme_favorites (
            user_id    TEXT NOT NULL,
            meme_id    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, meme_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_meme_favorites_user ON meme_favorites (user_id, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meme_favorites")
