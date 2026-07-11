"""P2-5 測試：截圖解析（規格：docs/04 §2.1）。"""

import pytest
from pydantic import ValidationError

from memeradar.matching.screenshot import (
    DEFAULT_PARSE_MODEL,
    ScreenshotParseError,
    ScreenshotParseResult,
    detect_media_type,
    parse_screenshot,
)

VALID = {
    "app_guess": "line",
    "conversation": [
        {"speaker": "other", "text": "你報告又遲交了！", "confidence": 0.98},
        {"speaker": "me", "text": "抱歉抱歉", "confidence": 0.97},
    ],
    "warnings": ["最上方一則訊息被裁切，未納入"],
}


class TestSchema:
    def test_valid_payload_parses(self):
        result = ScreenshotParseResult(**VALID)
        assert result.app_guess == "line"
        assert result.conversation[0].speaker == "other"
        assert result.conversation[1].confidence == pytest.approx(0.97)

    def test_speaker_locked_to_me_other(self):
        bad = {**VALID, "conversation": [{"speaker": "left", "text": "x", "confidence": 1.0}]}
        with pytest.raises(ValidationError):
            ScreenshotParseResult(**bad)

    def test_unknown_app_allowed(self):
        result = ScreenshotParseResult(**{**VALID, "app_guess": "unknown"})
        assert result.app_guess == "unknown"


class TestMediaType:
    def test_png_jpeg_webp_detected(self):
        assert detect_media_type(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "image/png"
        assert detect_media_type(b"\xff\xd8\xff\xe0" + b"\x00" * 8) == "image/jpeg"
        assert detect_media_type(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4) == "image/webp"

    def test_unknown_bytes_raise(self):
        with pytest.raises(ValueError, match="不支援"):
            detect_media_type(b"GIF89a" + b"\x00" * 8)


class StubResponse:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


class StubClient:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                return outer.response

        self.messages = _Messages()


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class TestParseScreenshot:
    def test_happy_path_and_call_shape(self):
        client = StubClient(StubResponse(ScreenshotParseResult(**VALID)))

        result = parse_screenshot(client, PNG_BYTES)

        assert result.conversation[0].text == "你報告又遲交了！"
        call = client.calls[0]
        assert call["model"] == DEFAULT_PARSE_MODEL
        assert call["thinking"] == {"type": "disabled"}  # 延遲敏感路徑
        assert call["output_format"] is ScreenshotParseResult
        assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
        image_block = call["messages"][0]["content"][0]
        assert image_block["type"] == "image"
        assert image_block["source"]["media_type"] == "image/png"

    def test_refusal_raises(self):
        client = StubClient(StubResponse(None, stop_reason="refusal"))
        with pytest.raises(ScreenshotParseError):
            parse_screenshot(client, PNG_BYTES)
