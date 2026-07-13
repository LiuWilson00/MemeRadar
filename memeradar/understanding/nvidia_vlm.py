"""NVIDIA NIM VLM client：多把免費 key 輪替 + 撞速率限制自動換 key + 全冷卻就等。

- OpenAI 相容端點（``integrate.api.nvidia.com/v1``）跑 Qwen 等 vision 模型。
- 免費方案有速率限制 → 多把 key round-robin 均攤；撞 429 就把該把 key 冷卻
  ``cooldown_s`` 秒並換下一把；全部冷卻時**等待**（依使用者決策「卡住就等就好」，
  不 fallback），直到 ``max_wait_s`` 上限才拋 ``VlmExhaustedError``。
- 每次呼叫都經 ``log`` 記錄（key 末碼 / 狀態 / 延遲 / token），供監控哪把 key 被打爆。
- 速率限制以 ``status_code == 429`` 判定（openai 的錯誤物件帶此屬性）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

BASE_URL = "https://integrate.api.nvidia.com/v1"

# Console 模型切換按鈕的候選清單（NVIDIA NIM 上實測可吃圖的 vision 模型；
# 首項為預設，繁中理解最佳）。
VISION_MODELS = [
    "qwen/qwen3.5-122b-a10b",
    "qwen/qwen3.5-397b-a17b",
    "nvidia/nemotron-nano-12b-v2-vl",
    "meta/llama-4-maverick-17b-128e-instruct",
    "meta/llama-3.2-90b-vision-instruct",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
]


class VlmExhaustedError(RuntimeError):
    """所有 key 皆限流 / 失敗且超過等待上限時拋出。"""


def build_clients(keys: list[str]) -> tuple[list[Any], list[str]]:
    """由 key 清單建立 OpenAI client 與其遮罩後的 key id（供 log）。"""
    from openai import OpenAI

    clients = [OpenAI(base_url=BASE_URL, api_key=k) for k in keys]
    key_ids = [("…" + k[-4:]) if len(k) >= 4 else "…" for k in keys]
    return clients, key_ids


class NvidiaVlm:
    def __init__(
        self,
        clients: list[Any],
        key_ids: list[str],
        model: str,
        *,
        log: Callable[[dict], None] = lambda rec: None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        cooldown_s: float = 30.0,
        max_wait_s: float = 180.0,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ):
        if not clients:
            raise ValueError("NvidiaVlm 需要至少一把 key")
        self._clients = clients
        self._key_ids = key_ids
        self._model = model
        self._log = log
        self._now = now
        self._sleep = sleep
        self._cooldown_s = cooldown_s
        self._max_wait_s = max_wait_s
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._cool = [0.0] * len(clients)  # 每把 key 冷卻到期的時間戳
        self._rr = 0  # round-robin 指標

    @property
    def model(self) -> str:
        return self._model

    def _acquire(self) -> int | None:
        """回傳下一把可用 key 的索引（round-robin，跳過冷卻中）；全冷卻回 None。"""
        n = len(self._clients)
        now = self._now()
        for offset in range(n):
            i = (self._rr + offset) % n
            if self._cool[i] <= now:
                self._rr = (i + 1) % n
                return i
        return None

    def annotate(
        self,
        image_b64: str,
        media_type: str,
        system: str,
        user_text: str,
        *,
        task: str = "annotate",
        meme_id: str | None = None,
        log: Callable[[dict], None] | None = None,
        model: str | None = None,
    ) -> str:
        """送圖 + prompt 給 VLM，回傳原始文字（結構化解析由呼叫端負責）。

        ``log`` / ``meme_id`` 為單次呼叫用：讓呼叫端把用量寫進帶當前連線的 log 表。
        ``model`` 覆寫本次使用的模型（Console 模型切換按鈕用）。
        """
        sink = log or self._log
        use_model = model or self._model
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                    },
                ],
            },
        ]
        deadline = self._now() + self._max_wait_s
        while True:
            i = self._acquire()
            if i is None:  # 全部冷卻 → 等到最近一把解凍（卡住就等）
                if self._now() >= deadline:
                    break
                wait = max(0.1, min(min(self._cool) - self._now(), deadline - self._now()))
                self._sleep(wait)
                continue

            t0 = self._now()
            try:
                resp = self._clients[i].chat.completions.create(
                    model=use_model,
                    messages=messages,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                )
                usage = getattr(resp, "usage", None)
                self._emit(sink, i, task, meme_id, use_model, "ok", t0, usage=usage)
                return resp.choices[0].message.content or ""
            except Exception as exc:  # noqa: BLE001 — 依 status_code 分流
                status = getattr(exc, "status_code", None)
                if status == 429:
                    self._cool[i] = self._now() + self._cooldown_s
                    self._emit(sink, i, task, meme_id, use_model, "rate_limited", t0)
                else:
                    self._emit(sink, i, task, meme_id, use_model, "error", t0, error=str(exc)[:200])
                if self._now() >= deadline:
                    break

        raise VlmExhaustedError(
            f"NVIDIA VLM 所有 key 皆不可用且已達等待上限 {self._max_wait_s:.0f}s"
        )

    def _emit(self, sink, i, task, meme_id, model, status, t0, *, usage=None, error=None) -> None:
        sink(
            {
                "key_id": self._key_ids[i],
                "model": model,
                "task": task,
                "meme_id": meme_id,
                "status": status,
                "latency_ms": int((self._now() - t0) * 1000),
                "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
                "error": error,
            }
        )
