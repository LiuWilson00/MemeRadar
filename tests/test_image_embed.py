"""NvImageEmbedder（llama-nemotron-embed-vl-1b-v2）單元測試。

走 OpenAI 相容 embeddings 形狀；真實影像向量須用真 key 跑 smoke 確認（已驗證可用）。
"""

from __future__ import annotations

import pytest

from memeradar.understanding.image_embed import NvImageEmbedder, image_data_uri


class _FakeClient:
    def __init__(self, key: str):
        self.key = key
        self.calls: list[dict] = []
        outer = self

        class _Embeddings:
            def create(self, *, model, input, **kw):
                outer.calls.append({"model": model, "input": list(input), **kw})
                data = [
                    type("E", (), {"embedding": [float(len(str(s))), 1.0], "index": i})()
                    for i, s in enumerate(input)
                ]
                return type("R", (), {"data": data})()

        self.embeddings = _Embeddings()


def test_embed_image_sends_data_uri_passage():
    captured: dict = {}

    class _Rec(_FakeClient):
        def __init__(self, key):
            super().__init__(key)
            captured["c"] = self

    emb = NvImageEmbedder(["k"], client_factory=_Rec)
    vec = emb.embed_image(b"\x89PNG imagebytes")
    assert isinstance(vec, list) and len(vec) == 2
    call = captured["c"].calls[0]
    assert call["model"] == "nvidia/llama-nemotron-embed-vl-1b-v2"
    assert call["input"][0].startswith("data:image/")
    assert ";base64," in call["input"][0]
    assert call["extra_body"]["input_type"] == "passage"


def test_image_data_uri_detects_jpeg():
    assert image_data_uri(b"\xff\xd8\xff jpeg").startswith("data:image/jpeg;base64,")
    assert image_data_uri(b"\x89PNG").startswith("data:image/png;base64,")


def test_requires_keys():
    with pytest.raises(RuntimeError):
        NvImageEmbedder([])
