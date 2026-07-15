"""固定 embedding 向量維度為 1024 + 建 HNSW 餘弦索引（正式環境檢索效能）。

背景：``embeddings.vector`` 原為不固定維度——讓測試用小向量、也曾相容 image_dedup
等不同維度。但 pgvector 建 HNSW 需固定維度。正式環境檢索用的 embedding 一律是
NVIDIA bge-m3 = 1024 維，故固定為 ``vector(1024)`` 並建 HNSW（餘弦）索引，
把「每次推薦全表掃描」變成近似最近鄰。

測試環境用維度不一的小向量 → 由 ``MEMERADAR_SKIP_VECTOR_INDEX=1`` 跳過本遷移
（索引純為正式效能優化，不改任何查詢語意；檢索 SQL 兩邊相同，測試走順序掃描）。

註：日後若重新啟用爬蟲的 CLIP 去重（image_dedup，非 1024 維），需改用獨立欄位/表，
現行 API 路徑未使用 image_dedup。

Revision ID: 0007_vector_index
Revises: 0006_gallery
Create Date: 2026-07-15
"""
from __future__ import annotations

import os

from alembic import op

revision = "0007_vector_index"
down_revision = "0006_gallery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if os.environ.get("MEMERADAR_SKIP_VECTOR_INDEX") == "1":
        return  # 測試環境：保留不固定維度，小向量照舊可用
    # 保險：清掉任何非 1024 維向量（正式理論上全是 bge-m3 1024）；被清者由標註流程重建
    op.execute("DELETE FROM embeddings WHERE vector_dims(vector) <> 1024")
    op.execute(
        "ALTER TABLE embeddings ALTER COLUMN vector TYPE vector(1024) "
        "USING vector::vector(1024)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw "
        "ON embeddings USING hnsw (vector vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_embeddings_hnsw")
    # 維度由 vector(1024) 還原為不固定屬無損，通常不需要；此處留著固定維度即可
