"""非同步任務：送出後背景執行，user 可離開再回來查進度/結果（以 client_id 分群）。"""

from __future__ import annotations

import pytest

from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


class TestTaskLifecycle:
    def test_create_get_and_status_transitions(self, conn):
        repo.create_task(conn, "t1", client_id="c_abc", input_type="text", label="你報告又遲交了")
        task = repo.get_task(conn, "t1")
        assert task["status"] == "pending"
        assert task["client_id"] == "c_abc"
        assert task["label"] == "你報告又遲交了"
        assert task["result"] is None

        repo.set_task_status(conn, "t1", "running")
        assert repo.get_task(conn, "t1")["status"] == "running"

        repo.set_task_status(conn, "t1", "done", result={"query_id": "q1", "results": [1, 2]})
        done = repo.get_task(conn, "t1")
        assert done["status"] == "done"
        assert done["result"]["query_id"] == "q1"

    def test_error_status_stores_message(self, conn):
        repo.create_task(conn, "t2", client_id="c_abc", input_type="meme_battle", label="梗圖大戰")
        repo.set_task_status(conn, "t2", "error", error="模型無法解析")
        task = repo.get_task(conn, "t2")
        assert task["status"] == "error"
        assert task["error"] == "模型無法解析"

    def test_get_missing_task_returns_none(self, conn):
        assert repo.get_task(conn, "nope") is None


class TestTaskHistory:
    def test_list_by_client_newest_first(self, conn):
        for i in range(3):
            repo.create_task(conn, f"t{i}", client_id="c_me", input_type="text", label=f"任務{i}",
                             created_at=f"2026-07-1{i}T00:00:00+00:00")
        repo.create_task(conn, "other", client_id="c_other", input_type="text", label="別人的")

        history = repo.list_tasks_by_client(conn, "c_me")
        assert [t["task_id"] for t in history] == ["t2", "t1", "t0"]  # 新到舊
        assert all(t["client_id"] == "c_me" for t in history)
        # 歷史列表不夾帶完整 result（省傳輸），但標記是否已完成
        assert "has_result" in history[0]

    def test_list_respects_limit(self, conn):
        for i in range(5):
            repo.create_task(conn, f"t{i}", client_id="c_me", input_type="text", label="x")
        assert len(repo.list_tasks_by_client(conn, "c_me", limit=2)) == 2
