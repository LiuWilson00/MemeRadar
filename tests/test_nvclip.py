"""NvClip（NV-CLIP hosted embeddings）與零樣本分類單元測試。

embed 走 OpenAI 相容形狀（與 NvidiaBgeM3Embedder 同一介面，已驗證）；
真實影像向量維度/品質須用真 key 跑 scripts/smoke_ocr_nvclip.py 確認。
"""

from __future__ import annotations

import pytest

from memeradar.understanding.nvclip import (
    NvClip,
    ZeroShotClassifier,
    image_data_uri,
    zero_shot_labels,
)


class _FakeClient:
    """模擬 OpenAI client 的 embeddings.create（回傳 .data[i].embedding / .index）。"""

    def __init__(self, key: str):
        self.key = key
        self.calls: list[dict] = []
        outer = self

        class _Embeddings:
            def create(self, *, model, input, **kw):
                outer.calls.append({"model": model, "input": list(input)})
                data = [
                    type("E", (), {"embedding": [float(len(s)), 1.0], "index": i})()
                    for i, s in enumerate(input)
                ]
                return type("R", (), {"data": data})()

        self.embeddings = _Embeddings()


def test_zero_shot_labels_picks_top_by_cosine():
    labels = ["生氣", "開心", "無奈"]
    label_vecs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    image_vec = [0.9, 0.1, 0.0]  # 最接近「生氣」，其次「開心」
    assert zero_shot_labels(image_vec, label_vecs, labels, top_k=2) == ["生氣", "開心"]


def test_zero_shot_labels_respects_min_score():
    labels = ["生氣", "開心"]
    label_vecs = [[1.0, 0.0], [0.0, 1.0]]
    image_vec = [1.0, 0.0]
    # 「開心」cosine=0 < min_score → 被濾掉
    assert zero_shot_labels(image_vec, label_vecs, labels, top_k=2, min_score=0.5) == ["生氣"]


def test_nvclip_embed_roundtrips_openai_shape():
    clip = NvClip(["k1"], client_factory=_FakeClient)
    vecs = clip.embed(["生氣", "開心的貓"])
    assert len(vecs) == 2
    assert vecs[0] == [2.0, 1.0]  # len("生氣")=2
    assert vecs[1] == [4.0, 1.0]  # len("開心的貓")=4


def test_nvclip_embed_image_sends_data_uri():
    captured = {}

    class _Recorder(_FakeClient):
        def __init__(self, key):
            super().__init__(key)
            captured["client"] = self

    clip = NvClip(["k1"], client_factory=_Recorder)
    clip.embed_image(b"\x89PNG imagebytes")
    sent = captured["client"].calls[0]["input"][0]
    assert sent.startswith("data:image/") and ";base64," in sent


def test_image_data_uri_detects_jpeg():
    assert image_data_uri(b"\xff\xd8\xff jpeg").startswith("data:image/jpeg;base64,")
    assert image_data_uri(b"\x89PNG").startswith("data:image/png;base64,")


def test_nvclip_requires_keys():
    with pytest.raises(RuntimeError):
        NvClip([])


class _RoutedClip:
    """embed 依標籤回固定向量；embed_image 回接近「生氣」的向量。"""

    def __init__(self):
        self.embed_calls = 0

    def embed(self, inputs):
        self.embed_calls += 1
        return [[1.0, 0.0] if s == "生氣" else [0.0, 1.0] for s in inputs]

    def embed_image(self, image_bytes):
        return [0.95, 0.05]


def test_zero_shot_classifier_classifies_and_caches_label_vectors():
    clip = _RoutedClip()
    clf = ZeroShotClassifier(clip, ["生氣", "開心"])
    assert clf.classify(b"img1", top_k=1) == ["生氣"]
    clf.classify(b"img2", top_k=1)
    assert clip.embed_calls == 1  # 標籤向量只算一次（跨呼叫快取）
