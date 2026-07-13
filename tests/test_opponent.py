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


class StubVlm:
    """NVIDIA VLM stub：回傳固定原始文字（raw）。"""

    model = "qwen/test"

    def __init__(self, raw: str):
        self.raw = raw
        self.calls: list[dict] = []

    def annotate(self, image_b64, media_type, system, user_text, **kwargs):
        self.calls.append({"system": system, "user_text": user_text, **kwargs})
        return self.raw


SAMPLE = OpponentMeme(
    ocr_text="我就爛",
    description="海綿寶寶攤手站立，一臉理直氣壯",
    emotions=["擺爛", "理直氣壯"],
    read="對方擺爛耍賴，擺明不想被說服",
)


class TestAnalyzeOpponentMeme:
    def test_returns_parsed_understanding(self):
        result = analyze_opponent_meme(StubVlm(SAMPLE.model_dump_json()), PNG)
        assert result.ocr_text == "我就爛"
        assert result.read

    def test_task_tagged_opponent(self):
        vlm = StubVlm(SAMPLE.model_dump_json())
        analyze_opponent_meme(vlm, PNG)
        assert vlm.calls[0]["task"] == "opponent"

    def test_non_json_raises_refused(self):
        with pytest.raises(OpponentMemeRefusedError):
            analyze_opponent_meme(StubVlm("抱歉我無法解析"), PNG)

    def test_unsupported_image_raises(self):
        with pytest.raises(ValueError):
            analyze_opponent_meme(StubVlm(SAMPLE.model_dump_json()), b"not-an-image")


class TestBuildBattleTurn:
    def test_frames_opponent_meme_as_other_turn(self):
        turn = build_battle_turn(SAMPLE)
        # 合成的對話輪次要帶入梗圖的文字、畫面與解讀，供意圖分析理解「對方出了什麼」
        assert "我就爛" in turn
        assert "海綿寶寶" in turn
        assert "擺爛耍賴" in turn
        # 明示這是「對方丟梗圖」的情境，而非對方說了這句話
        assert "梗圖" in turn
