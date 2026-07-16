"""效能索引：recommendation_logs(created_at) + events(event_type, created_at)。

高併發 review #9：EXPLAIN(prod) 實測確認的兩個熱查詢缺索引——
- /history、/report 儀表板對 recommendation_logs 依 created_at 排序/範圍過濾 → 原本 Seq Scan+Sort。
- list_chat_feedback / list_reported_memes 依 event_type 過濾再依 created_at 排序 → 原本
  idx_events_type 只吃到過濾、還要 Sort；改複合索引 (event_type, created_at) 連排序一起吃掉。
（meme_annotations.is_meme/nsfw 索引經 EXPLAIN 確認 franchise/category 統計是 seqscan+hash join、
 不會用到，故不加、免白扛寫入成本。）

Revision ID: 0012_perf_indexes
Revises: 0011_favorites
Create Date: 2026-07-17
"""
from __future__ import annotations

from alembic import op

revision = "0012_perf_indexes"
down_revision = "0011_favorites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_logs_created "
        "ON recommendation_logs (created_at)"
    )
    # 複合 (event_type, created_at) 涵蓋原 idx_events_type 的所有用途（最左前綴），故取代之
    op.execute("DROP INDEX IF EXISTS idx_events_type")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_type_created "
        "ON events (event_type, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_events_type_created")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type)")
    op.execute("DROP INDEX IF EXISTS idx_logs_created")
