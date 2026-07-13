"""後台可調的各任務模型設定（存在 settings 表；空 = 用 VLM 預設模型）。"""

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


class TestModelSettings:
    def test_empty_by_default(self, conn):
        assert repo.get_task_models(conn) == {}

    def test_set_and_get_roundtrip(self, conn):
        repo.set_task_models(conn, {"intent": "qwen/qwen3.5-397b-a17b", "rerank": None})
        assert repo.get_task_models(conn) == {"intent": "qwen/qwen3.5-397b-a17b"}

    def test_update_overwrites_and_empty_clears(self, conn):
        repo.set_task_models(conn, {"annotation": "meta/llama-4-maverick-17b-128e-instruct"})
        assert repo.get_task_models(conn)["annotation"] == "meta/llama-4-maverick-17b-128e-instruct"
        # 改設別的
        repo.set_task_models(conn, {"annotation": "qwen/qwen3.5-122b-a10b"})
        assert repo.get_task_models(conn)["annotation"] == "qwen/qwen3.5-122b-a10b"
        # 空字串 / None = 回預設（刪除該筆）
        repo.set_task_models(conn, {"annotation": ""})
        assert "annotation" not in repo.get_task_models(conn)

    def test_keys_are_the_five_pipeline_tasks(self):
        assert set(repo.TASK_MODEL_KEYS) == {
            "annotation", "intent", "rerank", "screenshot", "opponent"
        }
