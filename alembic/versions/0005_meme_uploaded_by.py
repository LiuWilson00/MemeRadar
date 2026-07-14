"""memes.uploaded_by：使用者上傳到共用圖庫的歸屬（供每日上傳配額 / 檢舉追溯）。

爬蟲 / 人工 seed 的圖此欄為 NULL；使用者上傳才填 user_id。

Revision ID: 0005_meme_uploaded_by
Revises: 0004_users
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op

revision = "0005_meme_uploaded_by"
down_revision = "0004_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE memes ADD COLUMN uploaded_by TEXT "
        "REFERENCES users (user_id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX idx_memes_uploaded_by ON memes (uploaded_by)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memes_uploaded_by")
    op.execute("ALTER TABLE memes DROP COLUMN IF EXISTS uploaded_by")
