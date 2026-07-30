"""任務失敗文案：使用者看到的字串不得洩漏內部細節（供應商 / 模型 / 狀態碼）。

前台 console/src/lib/api.ts 會把 task.error 原封不動顯示，所以那一欄就是產品文案。
真正的技術原因走 task.error_detail 與 runtime log。
"""

from __future__ import annotations

import pytest

from memeradar.api.error_copy import FALLBACK_COPY, user_facing
from memeradar.matching.intent import IntentRefusedError
from memeradar.matching.screenshot import ScreenshotParseError
from memeradar.understanding.embedding import EmbeddingUnavailableError
from memeradar.understanding.nvidia_vlm import VlmConfigError, VlmExhaustedError
from memeradar.understanding.opponent import OpponentMemeRefusedError


class TestMapping:
    @pytest.mark.parametrize(
        "exc",
        [
            IntentRefusedError("模型拒絕"),
            OpponentMemeRefusedError("模型拒絕"),
            ScreenshotParseError("截圖壞了"),
            VlmExhaustedError("NVIDIA VLM 所有 key 皆不可用且已達等待上限 50s"),
            EmbeddingUnavailableError("embedding 供應商全數失敗——nvidia：500"),
        ],
    )
    def test_known_errors_get_their_own_copy(self, exc):
        copy = user_facing(exc)
        assert copy and copy != FALLBACK_COPY, f"{type(exc).__name__} 沒有對應文案"

    def test_unknown_error_falls_back(self):
        assert user_facing(ValueError("看不懂的錯")) == FALLBACK_COPY

    def test_vlm_config_error_reuses_vlm_copy(self):
        """VlmConfigError 是 VlmExhaustedError 的子類（模型下架/未開通）——
        對使用者來說都是「它壞了」，不該讓「模型已下架」這種內部狀態外流。"""
        assert user_facing(VlmConfigError("模型 'x' 不可用（HTTP 410）")) == user_facing(
            VlmExhaustedError("等待上限")
        )


class TestNoInternalLeaks:
    """這是本模組存在的理由：任何一句文案洩漏內部細節都算 bug。"""

    FORBIDDEN = [
        "nvidia", "NVIDIA", "claude", "openai", "bge", "embedding", "VLM", "vlm",
        "HTTP", "http", "API", "api", "key", "token", "timeout", "逾時", "供應商",
        "模型", "500", "410", "429",
    ]

    def test_every_copy_is_clean(self):
        from memeradar.api import error_copy

        for copy in error_copy.all_copy():
            for word in self.FORBIDDEN:
                assert word not in copy, f"文案洩漏內部細節 {word!r}：{copy!r}"

    def test_copy_is_not_empty_and_reads_as_a_sentence(self):
        from memeradar.api import error_copy

        for copy in error_copy.all_copy():
            assert 6 <= len(copy) <= 40, f"文案長度不合理：{copy!r}"
            assert copy[-1] in "。？！", f"文案沒有正常結尾：{copy!r}"
