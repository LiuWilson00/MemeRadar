"""NVIDIA NIM hosted Nemotron OCR v2（multilingual）：影像 → 文字。

快速模式用來取代 VLM 截圖解析——Nemotron OCR v2 是輕量 CV 模型（非 LLM），
無冷啟動，單一模型自動處理繁中/簡中/日/韓/英/俄（比 PaddleOCR 快 ~28×，且
PaddleOCR hosted 實測只認拉丁字、讀不出中文）。多把 key 輪流、失敗換 key 重試。

回應形狀已用真 key 驗證（scripts/smoke_ocr_nvclip.py）：
``data[0].text_detections[].text_prediction.text`` + ``bounding_box.points``。
``_extract_text`` 依邊界框由上到下、左到右組回；未知形狀退回遞迴撈字（保險）。
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

# hosted CV 端點（同一組 NVIDIA key）；自架 NIM 則為 http://host:8000/v1/infer。
# 沿用 NIM OCR 標準 I/O，故換模型只換這條 URL、parser 不動。
DEFAULT_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v2"


def _media_type(image_bytes: bytes) -> str:
    """依 magic bytes 粗略判斷 png / jpeg（PaddleOCR 僅接受這兩種）。"""
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


class NvidiaOcr:
    """NVIDIA NIM hosted OCR client（預設 Nemotron OCR v2）。``ocr(image_bytes) -> str``。"""

    def __init__(
        self,
        keys: list[str],
        *,
        url: str | None = None,
        post: Callable[..., Any] | None = None,
        timeout: float = 30.0,
    ):
        if not keys:
            raise RuntimeError("NVIDIA OCR 需要 NVIDIA_API_KEYS")
        self._keys = list(keys)
        self._url = url or DEFAULT_URL
        self._post = post  # 可注入 transport 供測試；預設 requests.post
        self._timeout = timeout
        self._rr = 0

    def _do_post(self, headers: dict, json: dict):
        if self._post is not None:
            return self._post(self._url, headers=headers, json=json)
        import requests

        return requests.post(self._url, headers=headers, json=json, timeout=self._timeout)

    def ocr(self, image_bytes: bytes) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{_media_type(image_bytes)};base64,{b64}"
        payload = {"input": [{"type": "image_url", "url": data_uri}]}
        last_exc: Exception | None = None
        for _ in range(max(2, len(self._keys) * 2)):
            key = self._keys[self._rr % len(self._keys)]
            self._rr += 1
            try:
                resp = self._do_post(
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                return _extract_text(resp.json())
            except Exception as exc:  # noqa: BLE001 速率限制/瞬斷 → 換 key 重試
                last_exc = exc
                time.sleep(0.3)
        raise RuntimeError(f"NVIDIA OCR 失敗：{last_exc}")


def _extract_text(data: Any) -> str:
    """把 OCR 回應組回文字：優先用結構化偵測框（依 y、x 排序），失敗退回遞迴撈字。"""
    lines = _structured_lines(data)
    if lines:
        lines.sort(key=lambda t: (round(t[0], 3), t[1]))
        return "\n".join(t[2] for t in lines if t[2].strip())
    texts = [t for t in _collect_texts(data) if t.strip()]
    return "\n".join(texts)


def _structured_lines(data: Any) -> list[tuple[float, float, str]]:
    """從已知形狀取 (y, x, text)；找不到偵測框則回空。"""
    detections = _find_detections(data)
    out: list[tuple[float, float, str]] = []
    for det in detections:
        if not isinstance(det, dict):
            continue
        text = _detection_text(det)
        if not text:
            continue
        y, x = _detection_xy(det)
        out.append((y, x, text))
    return out


def _find_detections(data: Any) -> list:
    """定位偵測框陣列：data[0] 之下的 text_detections / content / detections / results。"""
    node: Any = data
    if isinstance(node, dict):
        for key in ("data", "results", "predictions", "outputs"):
            if isinstance(node.get(key), list) and node[key]:
                node = node[key][0]
                break
    if isinstance(node, dict):
        for key in ("text_detections", "detections", "content", "results", "texts"):
            if isinstance(node.get(key), list):
                return node[key]
    if isinstance(node, list):
        return node
    return []


def _detection_text(det: dict) -> str:
    pred = det.get("text_prediction")
    if isinstance(pred, dict) and isinstance(pred.get("text"), str):
        return pred["text"]
    for key in ("text", "label", "content", "value"):
        if isinstance(det.get(key), str):
            return det[key]
    return ""


def _detection_xy(det: dict) -> tuple[float, float]:
    """取偵測框左上角 (y, x) 供排序；抓不到就回 (0, 0)。"""
    box = det.get("bounding_box") or det.get("bbox") or det.get("box") or det
    points = None
    if isinstance(box, dict):
        points = box.get("points") or box.get("polys") or box.get("polygon")
    elif isinstance(box, list):
        points = box
    if isinstance(points, list) and points:
        ys, xs = [], []
        for p in points:
            if isinstance(p, dict):
                ys.append(_num(p.get("y")))
                xs.append(_num(p.get("x")))
            elif isinstance(p, list | tuple) and len(p) >= 2:
                xs.append(_num(p[0]))
                ys.append(_num(p[1]))
        if ys and xs:
            return min(ys), min(xs)
    return 0.0, 0.0


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _collect_texts(data: Any) -> list[str]:
    """遞迴撈出所有名為 text/label/content 的字串值（未知形狀時的保險）。"""
    out: list[str] = []

    def walk(node: Any):
        if isinstance(node, dict):
            for key, val in node.items():
                if key in ("text", "label", "content", "value") and isinstance(val, str):
                    out.append(val)
                else:
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return out
