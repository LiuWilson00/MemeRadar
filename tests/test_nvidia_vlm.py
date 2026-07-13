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
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
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
