"""events：輕量行為事件（下載 / 選分類 等），供分析與排行榜。

讚/踩已在 feedback_events、搜尋/推薦已在 recommendation_logs；此表補其餘輕量點擊。

Revision ID: 0003_events
Revises: 0002_meme_image_data
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op

revision = "0003_events"
down_revision = "0002_meme_image_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE events (
            event_id   TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            client_id  TEXT,
            meme_id    TEXT REFERENCES memes (meme_id) ON DELETE SET NULL,
            meta       TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_events_type ON events (event_type)")
    op.execute("CREATE INDEX idx_events_meme ON events (meme_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS events")
