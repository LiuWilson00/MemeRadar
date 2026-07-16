"""ClaudeVlm：與 NvidiaVlm 同介面（image + prompt → 原始文字）。"""

from __future__ import annotations

from memeradar.understanding.claude_vlm import ClaudeVlm


def _resp(text: str):
    block = type("B", (), {"type": "text", "text": text})()
    return type("R", (), {"content": [block]})()


class _StubAnthropic:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                return _resp(outer.reply)

        self.messages = _Messages()


def test_annotate_sends_image_and_returns_text():
    client = _StubAnthropic('{"is_meme": true}')
    vlm = ClaudeVlm(client, model="claude-haiku-4-5")
    out = vlm.annotate("BASE64DATA", "image/jpeg", "系統提示", "使用者提示")
    assert out == '{"is_meme": true}'
    kw = client.calls[0]
    assert kw["model"] == "claude-haiku-4-5"
    assert kw["system"] == "系統提示"
    content = kw["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["data"] == "BASE64DATA"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[1]["text"] == "使用者提示"


def test_model_override_per_call():
    client = _StubAnthropic("{}")
    ClaudeVlm(client).annotate("b", "image/png", "s", "u", model="claude-sonnet-5")
    assert client.calls[0]["model"] == "claude-sonnet-5"


def test_returns_empty_when_no_text_block():
    class _NoText:
        messages = type("M", (), {"create": lambda self, **k: type("R", (), {"content": []})()})()

    assert ClaudeVlm(_NoText()).annotate("b", "image/jpeg", "s", "u") == ""
