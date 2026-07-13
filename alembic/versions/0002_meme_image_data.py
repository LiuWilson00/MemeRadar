"""memes 加 image_data BYTEA：圖檔可直接存 DB（免 volume，跟著 DB 走）。

原本圖檔存檔案系統（data_dir/images），上雲要嘛掛 volume（塞檔案麻煩、有停機），
要嘛存物件儲存。本階段先存進 DB：image_data 有值就服務它，否則回退檔案系統。

Revision ID: 0002_meme_image_data
Revises: 0001_baseline
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op

revision = "0002_meme_image_data"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memes ADD COLUMN image_data BYTEA")


def downgrade() -> None:
    op.execute("ALTER TABLE memes DROP COLUMN image_data")
