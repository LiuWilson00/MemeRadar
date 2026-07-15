"""VlmClassifier：沒字圖 → 關鍵詞（VLM）+ 影像 embedding（飛輪訓練集）。"""

from __future__ import annotations

from memeradar.understanding.classifier import Classification, VlmClassifier, parse_labels


class _StubVlm:
    model = "qwen/test"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict] = []

    def annotate(self, image_b64, media_type, system, user_text, *, task="annotate",
                 model=None, **kw):
        self.calls.append({"task": task, "model": model})
        return self.reply


class _StubEmbedder:
    def embed_image(self, image_bytes):
        return [0.1, 0.2, 0.3]


def test_parse_labels_dedups_strips_and_caps():
    assert parse_labels("開心，開心, 得意 炫耀、無言、讚、問號、害怕", top_k=5) == [
        "開心", "得意", "炫耀", "無言", "讚",
    ]
    assert parse_labels("生氣。 無奈！") == ["生氣", "無奈"]
    assert parse_labels("") == []


def test_classify_returns_labels_and_embedding():
    clf = VlmClassifier(_StubVlm("生氣、無奈、翻白眼"), _StubEmbedder())
    r = clf.classify(b"\x89PNG")
    assert isinstance(r, Classification)
    assert r.labels == ["生氣", "無奈", "翻白眼"]
    assert r.embedding == [0.1, 0.2, 0.3]
    assert r.model_version == "qwen/test"


def test_classify_uses_textless_task():
    vlm = _StubVlm("開心")
    VlmClassifier(vlm, None).classify(b"png")
    assert vlm.calls[0]["task"] == "textless_classify"


def test_classify_without_embedder_returns_none_embedding():
    r = VlmClassifier(_StubVlm("開心、得意"), None).classify(b"png")
    assert r.labels == ["開心", "得意"]
    assert r.embedding is None


def test_classify_degrades_when_embedding_fails():
    class Broken:
        def embed_image(self, b):
            raise RuntimeError("embed 404")

    r = VlmClassifier(_StubVlm("生氣"), Broken()).classify(b"png")
    assert r.labels == ["生氣"]  # 標籤仍在
    assert r.embedding is None  # embedding 失敗 → None，不擋回應
