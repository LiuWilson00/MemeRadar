"""client_errors：收集前台（瀏覽器）拋出的錯誤，供後台 debug（類 CloudWatch）。

前端 ErrorBoundary / window error / unhandledrejection 會 best-effort 回報一筆。

Revision ID: 0008_client_errors
Revises: 0007_vector_index
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op

revision = "0008_client_errors"
down_revision = "0007_vector_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE client_errors (
            error_id   TEXT PRIMARY KEY,
            message    TEXT NOT NULL,
            stack      TEXT,
            url        TEXT,
            user_agent TEXT,
            client_id  TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_client_errors_created ON client_errors (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS client_errors")
