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


class TestFailuresAreDistinguishableFromEmptyResults:
    """Threads 兩種情況都不回話（公開版面貼錯誤訊息更糟），但 log 要分得出是哪一種。"""

    def _cfg(self):
        return {"api": "https://api.example", "admin": "u:p"}

    def _patch(self, monkeypatch, *, status=200, payload=None, redirect=None):
        import memeradar.bot.threads as bot

        class Resp:
            def __init__(self, code, data=None, headers=None):
                self.status_code = code
                self._data = data or {}
                self.text = "err"
                self.headers = headers or {}

            def json(self):
                return self._data

        monkeypatch.setattr(bot.requests, "post", lambda *a, **k: Resp(status, payload))
        monkeypatch.setattr(
            bot.requests, "get",
            lambda *a, **k: Resp(302, headers={"Location": redirect}) if redirect
            else Resp(200))
        return bot

    def test_auth_failure_flagged_with_hint(self, monkeypatch):
        bot = self._patch(monkeypatch, status=401)
        out = bot.recommend_meme_url(self._cfg(), "在幹嘛")
        assert out.failed is True and out.url is None
        assert "MEMERADAR_ADMIN" in (out.detail or ""), "401 要直接點名最可能的原因"

    def test_empty_results_is_not_a_failure(self, monkeypatch):
        bot = self._patch(monkeypatch, status=200, payload={"results": []})
        out = bot.recommend_meme_url(self._cfg(), "在幹嘛")
        assert out.failed is False and out.url is None

    def test_missing_r2_redirect_is_a_failure(self, monkeypatch):
        """圖片端點沒 302 = R2 公開網址沒設，Threads 貼不出去——那是故障不是沒梗圖。"""
        bot = self._patch(monkeypatch, status=200,
                          payload={"results": [{"image_url": "/memes/m_x/image"}]})
        out = bot.recommend_meme_url(self._cfg(), "在幹嘛")
        assert out.failed is True and "R2_PUBLIC_BASE_URL" in (out.detail or "")

    def test_success_returns_public_url(self, monkeypatch):
        bot = self._patch(monkeypatch, status=200,
                          payload={"results": [{"image_url": "/memes/m_x/image"}]},
                          redirect="https://pub-x.r2.dev/images/m_x.jpg")
        out = bot.recommend_meme_url(self._cfg(), "在幹嘛")
        assert out.failed is False
        assert out.url == "https://pub-x.r2.dev/images/m_x.jpg"
