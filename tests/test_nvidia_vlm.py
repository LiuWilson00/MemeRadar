"""NVIDIA NIM VLM client：多把 key 輪替 + 撞速率限制換 key + 全冷卻就等 + logging。

全用假 client，不打網路。速率限制以 ``status_code == 429`` 判定（openai 錯誤帶此屬性）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from memeradar.understanding.nvidia_vlm import NvidiaVlm, VlmExhaustedError


class FakeErr(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status_code = status


class FakeClient:
    """script: 每次呼叫依序取一個動作：'ok:<text>' / '429' / 'err'（用盡後重複最後一個）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.last_kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        action = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if action == "429":
            raise FakeErr(429)
        if action == "err":
            raise FakeErr(500)
        text = action.split("ok:", 1)[1]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(clients, **kw):
    logs = []
    clock = Clock()
    opts = {"cooldown_s": 30.0, "max_wait_s": 300.0, **kw}
    vlm = NvidiaVlm(
        clients=clients,
        key_ids=[f"k{i}" for i in range(len(clients))],
        model="qwen/test",
        log=logs.append,
        now=clock.now,
        sleep=clock.sleep,
        **opts,
    )
    return vlm, logs, clock


def call(vlm):
    return vlm.annotate("BASE64", "image/png", "系統指引", "標註這張圖")


class TestRotation:
    def test_round_robin_spreads_across_keys(self):
        clients = [FakeClient(["ok:a"]), FakeClient(["ok:b"]), FakeClient(["ok:c"])]
        vlm, *_ = make(clients)
        outs = [call(vlm) for _ in range(3)]
        assert set(outs) == {"a", "b", "c"}
        assert all(c.calls == 1 for c in clients)  # 三把 key 各用一次

    def test_returns_text_content(self):
        vlm, *_ = make([FakeClient(["ok:我就爛"])])
        assert call(vlm) == "我就爛"


class TestRateLimit:
    def test_429_cools_key_and_rotates_to_next(self):
        clients = [FakeClient(["429"]), FakeClient(["ok:b"])]
        vlm, logs, _ = make(clients)
        assert call(vlm) == "b"  # 第一把限流 → 換第二把成功
        statuses = [r["status"] for r in logs]
        assert "rate_limited" in statuses and statuses[-1] == "ok"

    def test_all_keys_cooling_then_waits_and_retries(self):
        # 兩把 key 第一次都 429，之後 ok；全冷卻時應等待再重試（不 fallback、不放棄）
        clients = [FakeClient(["429", "ok:a"]), FakeClient(["429", "ok:b"])]
        vlm, logs, clock = make(clients)
        out = call(vlm)
        assert out in {"a", "b"}
        assert clock.t > 1000.0  # 有等待（sleep 推進時鐘）
        assert sum(1 for r in logs if r["status"] == "rate_limited") == 2

    def test_raises_when_all_keys_exhausted_past_deadline(self):
        clients = [FakeClient(["429"]), FakeClient(["429"])]
        vlm, *_ = make(clients, max_wait_s=60.0)
        with pytest.raises(VlmExhaustedError):
            call(vlm)


class TestLogging:
    def test_logs_key_id_status_and_latency(self):
        vlm, logs, _ = make([FakeClient(["ok:x"])])
        call(vlm)
        rec = logs[-1]
        assert rec["key_id"] == "k0"
        assert rec["status"] == "ok"
        assert rec["model"] == "qwen/test"
        assert "latency_ms" in rec

    def test_per_call_log_and_meme_id_override_instance_log(self):
        vlm, instance_logs, _ = make([FakeClient(["ok:x"])])
        per_call = []
        vlm.annotate("B", "image/png", "s", "u", meme_id="m_42", log=per_call.append)
        assert instance_logs == []  # 未落到 instance log
        assert per_call[-1]["meme_id"] == "m_42"


class TestTextChatAndStructured:
    def test_chat_text_only_no_image(self):
        client = FakeClient(['ok:{"summary":"對方生氣"}'])
        vlm, *_ = make([client])
        out = vlm.chat("你是意圖分析器", "分析這段對話")
        assert out == '{"summary":"對方生氣"}'
        # 純文字：user content 是字串，不含 image_url
        user_msg = client.last_kwargs["messages"][-1]["content"]
        assert isinstance(user_msg, str)

    def test_call_structured_parses_and_retries(self):
        from pydantic import BaseModel

        class R(BaseModel):
            summary: str

        # 第一次回非 JSON → 重試 → 第二次回合法 JSON
        client = FakeClient(["ok:抱歉無法分析", 'ok:{"summary":"對方生氣"}'])
        vlm, *_ = make([client])
        from memeradar.understanding.nvidia_vlm import call_structured

        result = call_structured(vlm, R, "系統", "使用者", retries=2)
        assert result is not None and result.summary == "對方生氣"

    def test_call_structured_returns_none_when_exhausted(self):
        from pydantic import BaseModel

        class R(BaseModel):
            summary: str

        vlm, *_ = make([FakeClient(["ok:完全不是 JSON"])])
        from memeradar.understanding.nvidia_vlm import call_structured

        assert call_structured(vlm, R, "系統", "使用者", retries=1) is None


class TestVlmCallLogTable:
    def test_insert_and_query_stats(self, tmp_path):
        from memeradar.shared import repository as repo
        from memeradar.shared.db import connect, migrate

        conn = connect(tmp_path / "db.sqlite3")
        migrate(conn)
        repo.insert_vlm_call(conn, {
            "key_id": "…abcd", "model": "qwen/x", "task": "annotate", "meme_id": "m1",
            "status": "ok", "latency_ms": 1200, "prompt_tokens": 200, "completion_tokens": 80,
            "error": None,
        })
        repo.insert_vlm_call(conn, {
            "key_id": "…abcd", "model": "qwen/x", "task": "annotate", "meme_id": "m2",
            "status": "rate_limited", "latency_ms": 50, "prompt_tokens": None,
            "completion_tokens": None, "error": None,
        })
        stats = repo.vlm_call_stats(conn)
        row = {(s["key_id"], s["status"]): s["n"] for s in stats}
        assert row[("…abcd", "ok")] == 1
        assert row[("…abcd", "rate_limited")] == 1
        conn.close()
