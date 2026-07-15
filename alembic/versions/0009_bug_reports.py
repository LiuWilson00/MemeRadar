"""bug_reports：前台使用者主動回報的問題（描述 + 操作麵包屑 + 裝置資訊）。

浮動回報鈕送出一筆，後台「問題回報」分頁瀏覽，便於重現 debug。

Revision ID: 0009_bug_reports
Revises: 0008_client_errors
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op

revision = "0009_bug_reports"
down_revision = "0008_client_errors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE bug_reports (
            report_id   TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            breadcrumbs TEXT,          -- JSON array（最近操作紀錄）
            url         TEXT,
            user_agent  TEXT,
            client_id   TEXT,
            meta        TEXT,          -- JSON（視窗尺寸 / 版本等）
            created_at  TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_bug_reports_created ON bug_reports (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bug_reports")
