"""Claude 視覺標註 adapter：與 :class:`NvidiaVlm` 同介面（``.annotate`` → 原始 JSON 文字）。

本地用 Anthropic API 高品質標註 —— 不吃 NVIDIA 免費層限流、速度穩、中文 OCR / 梗理解好。
標註量大，預設用成本/速度取向的 haiku；要更高品質可換 sonnet。
只實作 ``annotate``（標註管線只用到它）。
"""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL = "claude-haiku-4-5"


class ClaudeVlm:
    model = DEFAULT_MODEL

    def __init__(self, client: Any, model: str = DEFAULT_MODEL, *, max_tokens: int = 1024):
        self._client = client
        self.model = model
        self._max_tokens = max_tokens

    def annotate(
        self,
        image_b64: str,
        media_type: str,
        system: str,
        user_text: str,
        *,
        task: str = "annotate",
        meme_id: str | None = None,
        log: Any = None,
        model: str | None = None,
    ) -> str:
        """送圖 + prompt 給 Claude，回原始文字（結構化解析由 parse_annotation 負責）。"""
        resp = self._client.messages.create(
            model=model or self.model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""
