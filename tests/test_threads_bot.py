"""Threads bot 的純邏輯：mention 解析 + webhook 簽章驗證（不打網路）。"""

from __future__ import annotations

import hashlib
import hmac

from memeradar.bot.threads import _extract_mentions, _verify_signature


def test_extract_mentions_pulls_id_and_text():
    payload = {"entry": [{"id": "e1", "changes": [
        {"value": {"id": "12345", "text": "@memeradar_bot 你好廢"}}]}]}
    assert _extract_mentions(payload) == [("12345", "@memeradar_bot 你好廢")]


def test_extract_mentions_tolerates_alt_fields_and_empty():
    # media_id 當 id、message 當 text 也認得；沒 entry 回空
    payload = {"entry": [{"changes": [{"value": {"media_id": "999", "message": "嗨"}}]}]}
    assert _extract_mentions(payload) == [("999", "嗨")]
    assert _extract_mentions({}) == []


def test_verify_signature():
    secret = "s3cr3t"
    body = b'{"hello":"world"}'
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert _verify_signature(secret, body, good) is True
    assert _verify_signature(secret, body, "sha256=deadbeef") is False
    assert _verify_signature(secret, body, "") is False
    assert _verify_signature("", body, "") is True  # 未設 app_secret＝不驗
