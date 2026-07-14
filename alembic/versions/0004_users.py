"""users：Google 登入的使用者（共用圖庫貢獻者、無限使用配額）。

只用 Google 身分（google_sub 唯一），不存密碼。session 由後端 JWT 管理。

Revision ID: 0004_users
Revises: 0003_events
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op

revision = "0004_users"
down_revision = "0003_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            user_id       TEXT PRIMARY KEY,
            google_sub    TEXT UNIQUE NOT NULL,
            email         TEXT,
            name          TEXT,
            picture       TEXT,
            role          TEXT NOT NULL DEFAULT 'user',
            created_at    TEXT NOT NULL,
            last_login_at TEXT
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users")
