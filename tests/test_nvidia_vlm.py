"""NVIDIA NIM VLM client：多把 key 輪替 + 撞速率限制換 key + 全冷卻就等 + logging。

全用假 client，不打網路。速率限制以 ``status_code == 429`` 判定（openai 錯誤帶此屬性）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from memeradar.understanding.nvidia_vlm import (
    NvidiaVlm,
    VlmExhaustedError,
    VlmInputRejectedError,
)


class FakeErr(Exception):
    def __init__(self, status: int, body: str = ""):
        super().__init__(f"HTTP {status}{(' ' + body) if body else ''}")
        self.status_code = status


class FakeClient:
    """script: 每次呼叫依序取一個動作：'ok:<text>' / 'len:<text>'（撞 max_tokens）/
    '429' / 'err'（用盡後重複最後一個）。"""

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
        if action == "reject":
            raise FakeErr(400, '{"error":{"code":"data_inspection_failed"}}')
        if action == "400":
            raise FakeErr(400, '{"error":{"message":"temporarily unavailable"}}')
        if action.startswith("perm:"):
            raise FakeErr(int(action.split(":", 1)[1]))
        kind, text = action.split(":", 1)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=text),
                finish_reason="length" if kind == "len" else "stop",
            )],
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


class TestPermanentErrorsAreNotMaskedAsExhaustion:
    """2026-07-30 事故：NVIDIA 把 qwen/qwen3.5-122b-a10b 下架（HTTP 410 Gone，EOL
    2026-07-20），但錯誤被收斂成「所有 key 皆不可用且已達等待上限 8s」——看起來像限流，
    害人往配額方向查。永久性錯誤換 key 或再等都不會好，必須立刻原文拋出。
    """

    @pytest.mark.parametrize("status", [401, 403, 404, 410])
    def test_permanent_status_fails_immediately_with_real_reason(self, status):
        clients = [FakeClient([f"perm:{status}"]) for _ in range(4)]
        vlm, _logs, _clock = make(clients, max_wait_s=8.0)

        with pytest.raises(VlmExhaustedError) as exc:
            vlm.chat("sys", "hi")

        msg = str(exc.value)
        assert str(status) in msg, f"錯誤訊息沒帶 HTTP 狀態碼：{msg}"
        assert "qwen/test" in msg, f"錯誤訊息沒帶模型名，看不出是哪個模型死了：{msg}"
        assert "等待上限" not in msg, f"永久性錯誤被誤報成限流耗盡：{msg}"
        # 不該把 4 把 key 全打過、也不該在 deadline 內反覆重打
        assert sum(c.calls for c in clients) == 1, "永久性錯誤仍在重試，白打 API"

    def test_rate_limit_exhaustion_still_reports_wait_cap(self):
        clients = [FakeClient(["429"]) for _ in range(2)]
        vlm, _logs, _clock = make(clients, max_wait_s=8.0, cooldown_s=30.0)

        with pytest.raises(VlmExhaustedError, match="等待上限"):
            vlm.chat("sys", "hi")

    def test_transient_error_message_carries_last_reason(self):
        """暫時性錯誤耗盡時，也要把最後一個真實錯誤帶出來，別只留一句空話。"""
        clients = [FakeClient(["err"]) for _ in range(2)]
        vlm, _logs, _clock = make(clients, max_wait_s=8.0)

        with pytest.raises(VlmExhaustedError) as exc:
            vlm.chat("sys", "hi")

        assert "500" in str(exc.value), f"沒帶出最後的真實錯誤：{exc.value}"


class TestFastFailBudgetAllowsRetry:
    """單次呼叫逾時若 >= 總預算，永遠只會嘗試一次——4 把 key 全閒著也不會換。

    2026-07-30：fast_fail 設 client timeout=15s、max_wait_s=8s，於是一通慢呼叫逾時後
    deadline 早已過，直接放棄。而實測 NVIDIA 免費層延遲變異極大（同圖 3.4～12.9s），
    「快速放棄＋換把重試」的成功率遠高於「耐心等一次」。
    """

    def test_slow_call_is_retried_on_another_key(self):
        clock = Clock()

        class _Slow(FakeClient):
            """模擬呼叫耗時：每次 create 都讓時鐘前進 timeout 秒後失敗。"""

            def __init__(self, script, cost):
                super().__init__(script)
                self.cost = cost

            def _create(self, **kwargs):
                clock.t += self.cost
                return super()._create(**kwargs)

        slow = _Slow(["err"], cost=6.0)  # 第一把慢到逾時
        fast = _Slow(["ok:成功"], cost=1.0)  # 第二把很快
        vlm = NvidiaVlm(
            clients=[slow, fast], key_ids=["k0", "k1"], model="m",
            now=clock.now, sleep=clock.sleep,
            cooldown_s=5.0, error_cooldown_s=1.0, max_wait_s=20.0,
        )

        assert vlm.chat("sys", "hi") == "成功"
        assert fast.calls == 1, "第一把逾時後沒有換第二把重試"

    def test_build_default_vlm_keeps_timeout_below_total_budget(self):
        """不變量：單次逾時必須 < 總等待預算，否則整個 key 輪替機制形同虛設。"""
        from memeradar.understanding import annotator

        captured = {}

        def fake_build_clients(keys, *, base_url=None, timeout=25.0):
            captured["timeout"] = timeout
            return [FakeClient(["ok:x"]) for _ in keys], list(keys)

        class _S:
            vlm_model = "m"
            vlm_base_url = "https://example.test/v1"
            vlm_disable_reasoning = False

            def vlm_keys(self):
                return ["k1", "k2"]

        import memeradar.understanding.nvidia_vlm as nv

        orig_build, orig_settings = nv.build_clients, annotator.get_settings
        nv.build_clients = fake_build_clients
        annotator.get_settings = lambda: _S()
        try:
            vlm = annotator.build_default_vlm(fast_fail=True)
        finally:
            nv.build_clients, annotator.get_settings = orig_build, orig_settings

        assert captured["timeout"] < vlm._max_wait_s, (
            f"單次逾時 {captured['timeout']}s >= 總預算 {vlm._max_wait_s}s"
            "——一次逾時就用完預算，永遠不會換 key 重試"
        )


class TestProviderIsConfigurable:
    """2026-07-30：NVIDIA 把 qwen 系列下架後改走 OpenRouter。供應商端點不能寫死——
    寫死的話換家就得改程式、重新部署，而這半年已經被迫換兩次了。
    """

    def test_build_default_vlm_uses_configured_base_url(self):
        from memeradar.understanding import annotator

        captured = {}

        def fake_build_clients(keys, *, base_url, timeout=25.0):
            captured.update(base_url=base_url, keys=list(keys))
            return [FakeClient(["ok:x"]) for _ in keys], list(keys)

        class _S:
            vlm_base_url = "https://example.test/v1"
            vlm_model = "vendor/some-model"
            vlm_disable_reasoning = True

            def vlm_keys(self):
                return ["k1", "k2"]

        import memeradar.understanding.nvidia_vlm as nv

        orig_build, orig_settings = nv.build_clients, annotator.get_settings
        nv.build_clients = fake_build_clients
        annotator.get_settings = lambda: _S()
        try:
            vlm = annotator.build_default_vlm()
        finally:
            nv.build_clients, annotator.get_settings = orig_build, orig_settings

        assert captured["base_url"] == "https://example.test/v1"
        assert vlm.model == "vendor/some-model"

    def test_missing_keys_names_the_env_var(self):
        from memeradar.understanding import annotator

        class _S:
            vlm_base_url = "https://example.test/v1"
            vlm_model = "m"
            vlm_disable_reasoning = True

            def vlm_keys(self):
                return []

        orig = annotator.get_settings
        annotator.get_settings = lambda: _S()
        try:
            with pytest.raises(RuntimeError, match="VLM_API_KEYS"):
                annotator.build_default_vlm()
        finally:
            annotator.get_settings = orig


class TestModelOverride:
    def test_build_default_vlm_accepts_model_override(self):
        """CLI 回填時偶爾要換更好的那顆；但預設必須沿用設定，不可各處自己寫死。"""
        from memeradar.understanding import annotator

        class _S:
            vlm_base_url = "https://example.test/v1"
            vlm_model = "vendor/from-settings"
            vlm_disable_reasoning = False

            def vlm_keys(self):
                return ["k1"]

        import memeradar.understanding.nvidia_vlm as nv

        orig_build, orig_settings = nv.build_clients, annotator.get_settings
        nv.build_clients = lambda keys, *, base_url=None, timeout=25.0: (
            [FakeClient(["ok:x"]) for _ in keys], list(keys)
        )
        annotator.get_settings = lambda: _S()
        try:
            assert annotator.build_default_vlm().model == "vendor/from-settings"
            assert annotator.build_default_vlm(model="vendor/override").model == "vendor/override"
        finally:
            nv.build_clients, annotator.get_settings = orig_build, orig_settings


class TestOutputBudgetReachesTheAPI:
    """各模組宣告的 MAX_OUTPUT_TOKENS 必須真的傳進 API。

    2026-07-31 事故：rerank.py 寫了 MAX_OUTPUT_TOKENS=2000，但 NvidiaVlm 用自己的預設
    1024——25 個候選要 score + 25 字理由，輸出永遠停在 1024、JSON 被截斷、call_structured
    重試三次全敗，最後退回純向量排序。使用者等 18-70 秒換來一句「rerank 暫不可用」。
    """

    def test_chat_honours_per_call_max_tokens(self):
        client = FakeClient(["ok:x"])
        vlm, _logs, _clock = make([client])
        vlm.chat("sys", "hi", max_tokens=2000)
        assert client.last_kwargs["max_tokens"] == 2000

    def test_annotate_honours_per_call_max_tokens(self):
        client = FakeClient(["ok:x"])
        vlm, _logs, _clock = make([client])
        vlm.annotate("b64", "image/png", "sys", "hi", max_tokens=2048)
        assert client.last_kwargs["max_tokens"] == 2048

    def test_falls_back_to_client_default(self):
        client = FakeClient(["ok:x"])
        vlm, _logs, _clock = make([client], max_tokens=777)
        vlm.chat("sys", "hi")
        assert client.last_kwargs["max_tokens"] == 777

    def test_call_structured_forwards_module_budget(self):
        """rerank 這種「每個候選都要一段輸出」的任務，budget 傳不到就必然截斷。"""
        from pydantic import BaseModel

        from memeradar.understanding.nvidia_vlm import call_structured

        class _Out(BaseModel):
            ok: bool

        client = FakeClient(['ok:{"ok": true}'])
        vlm, _logs, _clock = make([client])
        call_structured(vlm, _Out, "sys", "user", max_tokens=2000)
        assert client.last_kwargs["max_tokens"] == 2000


class TestTruncationIsVisible:
    """撞 max_tokens 的回應必須在 vlm_calls 標成 truncated，不能混在 ok 裡。

    2026-07-31：rerank 輸出被截斷成半截 JSON，下游只記錄「解析失敗」，在 vlm_calls
    看起來卻全是 status=ok，害「查梗圖要 30 秒」這個問題繞了一大圈才定位。
    """

    def _record(self, action):
        vlm, logs, _ = make([FakeClient([action])])
        vlm.chat("s", "u", task="rerank")
        return logs[0]

    def test_hitting_the_limit_is_recorded_as_truncated(self):
        record = self._record("len:{\"scores\": [{\"candidate_id\": 1,")
        assert record["status"] == "truncated"
        assert "max_tokens" in record["error"]

    def test_normal_completion_stays_ok(self):
        record = self._record("ok:{}")
        assert record["status"] == "ok"
        assert record["error"] is None


class TestInputRejectionIsNotRetried:
    """內容審查退件（HTTP 400 data_inspection_failed）對同一張圖是必然重現的。

    2026-07-31 回填爬蟲：每被退一張，客戶端就把它當暫時性錯誤，輪完所有 key 再等滿
    max_wait 180 秒才放棄。約 6% 的圖會被退 → 整個回填時間變成三倍以上。
    """

    def test_gives_up_immediately_instead_of_waiting_out_the_budget(self):
        vlm, logs, clock = make([FakeClient(["reject"]), FakeClient(["reject"])],
                                max_wait_s=180.0)
        t0 = clock.now()

        with pytest.raises(VlmInputRejectedError):
            call(vlm)

        assert clock.now() - t0 == 0.0, "不該為了退件而等待"
        assert len(logs) == 1, "不該再輪下一把 key"
        assert logs[0]["status"] == "rejected"

    def test_a_plain_400_is_still_treated_as_transient(self):
        """只有內容審查退件是必然重現的；其他 400 可能只是一時的，維持原本的重試邏輯。"""
        vlm, logs, _ = make([FakeClient(["400", "ok:{}"])], max_wait_s=60.0)

        assert call(vlm) == "{}"  # 重試後成功，沒有被當成永久錯誤

    def test_rejection_is_still_a_vlm_exhausted_error(self):
        """呼叫端（爬蟲 / API）既有的 VlmExhaustedError 攔截不能因此漏接。"""
        assert issubclass(VlmInputRejectedError, VlmExhaustedError)
