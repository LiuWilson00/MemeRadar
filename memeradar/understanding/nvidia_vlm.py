"""線上 LLM/VLM client：多把 key 輪替 + 撞速率限制自動換 key + 全冷卻就等。

- OpenAI 相容端點；供應商由 ``settings.vlm_base_url`` 決定（2026-07-30 起預設 OpenRouter，
  先前是 NVIDIA NIM）。類別名 ``NvidiaVlm`` 是歷史遺留，改名要動十來個檔案，暫留。
- 免費方案有速率限制 → 多把 key round-robin 均攤；撞 429 就把該把 key 冷卻
  ``cooldown_s`` 秒並換下一把；全部冷卻時**等待**（依使用者決策「卡住就等就好」，
  不 fallback），直到 ``max_wait_s`` 上限才拋 ``VlmExhaustedError``。
- 每次呼叫都經 ``log`` 記錄（key 末碼 / 狀態 / 延遲 / token），供監控哪把 key 被打爆。
- 速率限制以 ``status_code == 429`` 判定（openai 的錯誤物件帶此屬性）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

BASE_URL = "https://integrate.api.nvidia.com/v1"

# Console 模型切換按鈕的候選清單（首項為預設）。OpenRouter 的 model id。
#
# 2026-07-30 用真 key、線上真實中文梗圖、專案真實 prompt 實測（皆已關閉 thinking）：
VISION_MODELS = [
    # $0.065/$0.26。看圖 6.4s、意圖 1.85s、JSON 3/3、繁中 60% → 預設
    "qwen/qwen3.5-flash-02-23",
    # $0.26/$2.08。看圖 10.2s、OCR 最完整（你 7/20 前一直在用的那顆）→ 想拚品質時用
    "qwen/qwen3.5-122b-a10b",
    # $0.03/$0.13 最便宜，但實測上游持續回 429，暫不可用；哪天通了值得再測
    "qwen/qwen3.7-flash",
]


class VlmExhaustedError(RuntimeError):
    """所有 key 皆限流 / 失敗且超過等待上限時拋出。"""


class VlmConfigError(VlmExhaustedError):
    """模型下架 / 未對此帳號開通 / 認證失敗——重試與換 key 都不會好。

    刻意繼承 ``VlmExhaustedError``：呼叫端既有的優雅降級（退向量排序、回 503、
    標註留給下輪）全部照舊，只是錯誤訊息會帶出真正的原因而非「限流耗盡」。
    """


# 這些狀態碼換 key 或再等都不會好（模型 EOL 回 410、未開通回 404、認證 401/403）
_PERMANENT_STATUSES = frozenset({401, 403, 404, 410})


def build_clients(
    keys: list[str], *, base_url: str = BASE_URL, timeout: float = 25.0
) -> tuple[list[Any], list[str]]:
    """由 key 清單建立 OpenAI client 與其遮罩後的 key id（供 log）。

    timeout：單次呼叫最多等幾秒（避免掛住的請求拖到 SDK 預設 ~600s）。線上推薦（意圖/rerank）
    要更短、卡住就快速失敗放人（見 build_default_vlm(fast_fail=True)）；批次標註可寬鬆些。
    max_retries=0：SDK 不自行重試 429／連線錯誤——由本類的 key 輪替迴圈即時換把處理，反應更快。
    """
    from openai import OpenAI

    clients = [
        OpenAI(base_url=base_url, api_key=k, timeout=timeout, max_retries=0) for k in keys
    ]
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
        # 暫時性錯誤（5xx / 連線）後這把 key 的短冷卻：迴圈本身不 sleep，沒有這個
        # 會在 max_wait_s 之前以最高速率空轉狂打 API。
        error_cooldown_s: float = 1.0,
        max_wait_s: float = 180.0,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        extra_body: dict | None = None,
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
        self._error_cooldown_s = error_cooldown_s
        self._max_wait_s = max_wait_s
        self._max_tokens = max_tokens
        self._temperature = temperature
        # 供應商專屬參數，例如 OpenRouter 的 {"reasoning": {"enabled": False}}——
        # 關掉 thinking 是延遲的關鍵（見 shared/config.py 的 vlm_disable_reasoning）
        self._extra_body = extra_body or {}
        self._cool = [0.0] * len(clients)  # 每把 key 冷卻到期的時間戳
        self._rr = 0  # round-robin 指標
        self._lock = threading.Lock()  # 背景標註 worker + 請求緒會並發存取 _cool/_rr

    @property
    def model(self) -> str:
        return self._model

    def _acquire(self) -> int | None:
        """回傳下一把可用 key 的索引（round-robin，跳過冷卻中）；全冷卻回 None。"""
        n = len(self._clients)
        now = self._now()
        with self._lock:
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
        max_tokens: int | None = None,
    ) -> str:
        """送圖 + prompt 給 VLM，回傳原始文字（結構化解析由呼叫端負責）。"""
        content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
        ]
        return self._complete(system, content, task=task, meme_id=meme_id, log=log,
                              model=model, max_tokens=max_tokens)

    def chat(
        self,
        system: str,
        user_text: str,
        *,
        task: str = "text",
        meme_id: str | None = None,
        log: Callable[[dict], None] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """純文字 prompt（意圖 / rerank 等無圖任務用），回傳原始文字。"""
        return self._complete(system, user_text, task=task, meme_id=meme_id, log=log,
                              model=model, max_tokens=max_tokens)

    def _complete(self, system, user_content, *, task, meme_id, log, model,
                  max_tokens=None) -> str:
        """核心：多把 key 輪替 + 撞 429 冷卻換 key + 全冷卻就等。

        ``user_content`` 可為字串（純文字）或 content 陣列（含圖）。
        ``log`` / ``meme_id`` 供呼叫端把用量寫進帶當前連線的表；``model`` 覆寫模型。
        """
        sink = log or self._log
        use_model = model or self._model
        # 各任務自報所需輸出上限（rerank 每個候選都要一段理由，遠超通用預設）；
        # 沒傳就用 client 預設。傳不到的話輸出會被截斷成無效 JSON——見 2026-07-31 事故。
        use_max_tokens = max_tokens or self._max_tokens
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        deadline = self._now() + self._max_wait_s
        last_error: str | None = None
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
                    max_tokens=use_max_tokens,
                    temperature=self._temperature,
                    **({"extra_body": self._extra_body} if self._extra_body else {}),
                )
                usage = getattr(resp, "usage", None)
                choice = resp.choices[0]
                # 撞到 max_tokens 的回應是半截 JSON，下游只會看到「解析失敗」然後原封不動
                # 重試（同樣的請求必然再截斷一次）。把它獨立標成 truncated，才能在 vlm_calls
                # 一眼看出是輸出上限不夠、而不是模型不聽話（2026-07-31 rerank 事故）。
                truncated = getattr(choice, "finish_reason", None) == "length"
                self._emit(
                    sink, i, task, meme_id, use_model,
                    "truncated" if truncated else "ok", t0, usage=usage,
                    error=f"finish_reason=length（輸出撞上 max_tokens={use_max_tokens}）"
                    if truncated else None,
                )
                return choice.message.content or ""
            except Exception as exc:  # noqa: BLE001 — 依 status_code 分流
                status = getattr(exc, "status_code", None)
                if status in _PERMANENT_STATUSES:
                    # 模型下架 / 未開通 / 認證錯 → 換 key 或再等都不會好。立刻原文拋出，
                    # 別收斂成「限流耗盡」害人往配額方向查（2026-07-30：qwen 下架回 410，
                    # 錯誤訊息卻只說「所有 key 皆不可用且已達等待上限 8s」）。
                    self._emit(sink, i, task, meme_id, use_model, "error", t0,
                               error=str(exc)[:200])
                    raise VlmConfigError(
                        f"NVIDIA VLM 模型 {use_model!r} 不可用（HTTP {status}）：{exc}"
                        "——重試無用，請檢查 VLM_MODEL 是否已下架或未對此帳號開通"
                    ) from exc
                last_error = f"HTTP {status}: {exc}" if status else str(exc)
                if status == 429:
                    with self._lock:
                        self._cool[i] = self._now() + self._cooldown_s
                    self._emit(sink, i, task, meme_id, use_model, "rate_limited", t0)
                else:
                    self._emit(sink, i, task, meme_id, use_model, "error", t0, error=str(exc)[:200])
                    # 暫時性錯誤也要讓這把 key 短暫冷卻：否則迴圈不 sleep，會在 deadline
                    # 之前以最高速率空轉狂打 API。
                    with self._lock:
                        self._cool[i] = self._now() + self._error_cooldown_s
                if self._now() >= deadline:
                    break

        tail = f"（最後錯誤：{last_error[:200]}）" if last_error else ""
        raise VlmExhaustedError(
            f"NVIDIA VLM 所有 key 皆不可用且已達等待上限 {self._max_wait_s:.0f}s{tail}"
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


# ── 結構化輸出 helper（意圖 / rerank / 截圖 / 對方梗圖共用）───────────


def extract_json(raw: str) -> str | None:
    """從模型回應抽出 JSON 物件（容忍 markdown 圍欄與前後贅字）。"""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return raw[start : end + 1]


def call_structured(
    vlm: NvidiaVlm,
    result_model,
    system: str,
    user_text: str,
    *,
    image_b64: str | None = None,
    media_type: str | None = None,
    task: str = "text",
    meme_id: str | None = None,
    log: Callable[[dict], None] | None = None,
    model: str | None = None,
    retries: int = 2,
    max_tokens: int | None = None,
):
    """呼叫 VLM 並解析為 ``result_model``（pydantic）；格式/驗證失敗重試，耗盡回 None。

    有 ``image_b64`` 走 vision（annotate），否則走純文字（chat）。
    """
    import json

    from pydantic import ValidationError

    for _ in range(retries + 1):
        if image_b64 is not None:
            raw = vlm.annotate(
                image_b64, media_type or "image/png", system, user_text,
                task=task, meme_id=meme_id, log=log, model=model, max_tokens=max_tokens,
            )
        else:
            raw = vlm.chat(system, user_text, task=task, meme_id=meme_id, log=log,
                           model=model, max_tokens=max_tokens)
        fragment = extract_json(raw)
        if fragment is not None:
            try:
                return result_model(**json.loads(fragment))
            except (json.JSONDecodeError, ValidationError, TypeError):
                pass
    return None
