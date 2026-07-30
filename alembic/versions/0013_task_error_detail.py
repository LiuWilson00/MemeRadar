"""tasks 加 error_detail：把「給使用者看的文案」與「給工程師 debug 的技術細節」分開。

``error`` 會被前台原封不動顯示，所以那欄改存產品文案（見 memeradar/api/error_copy.py）；
原始技術訊息（供應商錯誤碼、模型名、逾時秒數）改放這一欄，同一份 task JSON 回得到。

舊資料的 error 欄仍是技術訊息，不回填——歷史任務不會再被使用者看到。
"""

from alembic import op

revision = "0013_task_error_detail"
down_revision = "0012_perf_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks ADD COLUMN error_detail TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE tasks DROP COLUMN error_detail")
