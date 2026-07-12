"""梗圖大戰：理解對方丟來的梗圖（記憶體處理，不落庫）。"""

from __future__ import annotations

import pytest

from memeradar.understanding.opponent import (
    OpponentMeme,
    OpponentMemeRefusedError,
    analyze_opponent_meme,
    build_battle_turn,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # 只需通過 magic bytes 偵測


class StubResponse:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


class StubClient:
    def __init__(self, response):
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                return response

        self.messages = _Messages()


SAMPLE = OpponentMeme(
    ocr_text="我就爛",
    description="海綿寶寶攤手站立，一臉理直氣壯",
    emotions=["擺爛", "理直氣壯"],
    read="對方擺爛耍賴，擺明不想被說服",
)


class TestAnalyzeOpponentMeme:
    def test_returns_parsed_understanding(self):
        client = StubClient(StubResponse(SAMPLE))
        result = analyze_opponent_meme(client, PNG)
        assert result.ocr_text == "我就爛"
        assert result.read

    def test_uses_structured_output_and_disables_thinking(self):
        client = StubClient(StubResponse(SAMPLE))
        analyze_opponent_meme(client, PNG)
        call = client.calls[0]
        assert call["output_format"] is OpponentMeme
        assert call["thinking"] == {"type": "disabled"}  # 延遲敏感路徑

    def test_refusal_raises(self):
        client = StubClient(StubResponse(None, stop_reason="refusal"))
        with pytest.raises(OpponentMemeRefusedError):
            analyze_opponent_meme(client, PNG)

    def test_unsupported_image_raises(self):
        client = StubClient(StubResponse(SAMPLE))
        with pytest.raises(ValueError):
            analyze_opponent_meme(client, b"not-an-image")


class TestBuildBattleTurn:
    def test_frames_opponent_meme_as_other_turn(self):
        turn = build_battle_turn(SAMPLE)
        # 合成的對話輪次要帶入梗圖的文字、畫面與解讀，供意圖分析理解「對方出了什麼」
        assert "我就爛" in turn
        assert "海綿寶寶" in turn
        assert "擺爛耍賴" in turn
        # 明示這是「對方丟梗圖」的情境，而非對方說了這句話
        assert "梗圖" in turn
