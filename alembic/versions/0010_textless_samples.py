"""textless_samples：沒字圖分類的訓練集（影像 embedding + VLM 標籤）。

資料飛輪：快速模式沒字圖用小 VLM 取標籤時，順手把 (影像 embedding, 標籤) 存下來，
日後拿來訓練便宜的 image→emotion 分類器取代 VLM。隱私：只存 embedding + 標籤，不存原圖。

Revision ID: 0010_textless_samples
Revises: 0009_bug_reports
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op

revision = "0010_textless_samples"
down_revision = "0009_bug_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE textless_samples (
            sample_id     TEXT PRIMARY KEY,
            embedding     TEXT,          -- JSON float array（影像向量，隱私：不存原圖）
            labels        TEXT,          -- JSON array（VLM 給的情緒/類別標籤）
            model_version TEXT,
            client_id     TEXT,
            created_at    TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_textless_samples_created ON textless_samples (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS textless_samples")
