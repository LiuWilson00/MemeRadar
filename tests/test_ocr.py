"""NvidiaOcr（PaddleOCR hosted）單元測試。

注意：這些測試用 stub transport 驗證「解析與排序邏輯」，*不*證明真實 API 的
回應欄位命名正確——真實形狀須用真 key 跑 scripts/smoke_ocr_nvclip.py 確認
（見 memory：stub 可能掩蓋真實 SDK/API 漂移）。
"""

from __future__ import annotations

import pytest

from memeradar.understanding.ocr import NvidiaOcr


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _detection(text: str, y: float, x: float = 0.1):
    return {
        "text_prediction": {"text": text, "confidence": 0.9},
        "bounding_box": {
            "points": [
                {"x": x, "y": y},
                {"x": x + 0.2, "y": y},
                {"x": x + 0.2, "y": y + 0.03},
                {"x": x, "y": y + 0.03},
            ]
        },
    }


def test_ocr_joins_detections_in_reading_order():
    # 回應中偵測框順序打亂（下面那行先出現），輸出仍須由上到下
    payload = {
        "data": [
            {
                "text_detections": [
                    _detection("下面這行", y=0.60),
                    _detection("上面這行", y=0.10),
                ]
            }
        ]
    }
    calls = []

    def fake_post(url, *, headers, json):
        calls.append((url, headers, json))
        return _FakeResp(payload)

    ocr = NvidiaOcr(["key-abcd"], post=fake_post)
    text = ocr.ocr(b"\x89PNG fake-bytes")

    assert text == "上面這行\n下面這行"
    # 送出的 payload 帶 base64 data URI 與 Bearer 授權
    url, headers, body = calls[0]
    assert headers["Authorization"] == "Bearer key-abcd"
    assert body["input"][0]["url"].startswith("data:image/")
    assert ";base64," in body["input"][0]["url"]


def test_ocr_empty_detections_returns_empty_string():
    ocr = NvidiaOcr(["k"], post=lambda *a, **k: _FakeResp({"data": [{"text_detections": []}]}))
    assert ocr.ocr(b"png") == ""


def test_ocr_rotates_key_and_retries_on_transient_error():
    attempts = {"n": 0}

    def flaky_post(url, *, headers, json):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("429 rate limited")
        return _FakeResp({"data": [{"text_detections": [_detection("成功", y=0.2)]}]})

    ocr = NvidiaOcr(["k1", "k2"], post=flaky_post)
    assert ocr.ocr(b"png") == "成功"
    assert attempts["n"] == 2  # 第一次失敗、換 key 重試成功


def test_ocr_fallback_collects_text_when_shape_differs():
    # 真實欄位命名若與預期不同，仍應盡量撈出文字（形狀保險）
    payload = {"result": {"texts": [{"text": "撈得到"}, {"text": "也撈得到"}]}}
    ocr = NvidiaOcr(["k"], post=lambda *a, **k: _FakeResp(payload))
    out = ocr.ocr(b"png")
    assert "撈得到" in out and "也撈得到" in out


def test_ocr_requires_keys():
    with pytest.raises(RuntimeError):
        NvidiaOcr([])


def _png(w: int, h: int) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_downscale_shrinks_large_image():
    import io

    from PIL import Image

    from memeradar.understanding.ocr import _downscale_if_large

    big = _png(4000, 4000)  # 16MP
    out = _downscale_if_large(big, max_pixels=6_000_000)
    w, h = Image.open(io.BytesIO(out)).size
    assert w * h <= 6_000_000  # 縮到門檻內
    assert w < 4000 and h < 4000  # 尺寸確實變小


def test_downscale_leaves_small_image_untouched():
    from memeradar.understanding.ocr import _downscale_if_large

    small = _png(200, 200)
    assert _downscale_if_large(small, max_pixels=6_000_000) is small


def test_downscale_returns_original_on_non_image():
    from memeradar.understanding.ocr import _downscale_if_large

    assert _downscale_if_large(b"not an image") == b"not an image"
