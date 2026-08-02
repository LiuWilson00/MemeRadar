"""Telegram bot 的觸發/上下文判斷（context_for）——純邏輯，不打網路。"""

from __future__ import annotations

from memeradar.bot.app import context_for

ME = {"id": 999, "username": "memeradar_super_bot"}


def _msg(text=None, chat="group", reply=None, frm=1):
    d = {"from": {"id": frm}, "chat": {"type": chat}, "message_id": 1}
    if text is not None:
        d["text"] = text
    if reply is not None:
        d["reply_to_message"] = reply
    return d


def test_private_chat_uses_the_message():
    assert context_for(_msg("你好廢", chat="private"), ME) == "你好廢"


def test_group_mention_uses_own_text():
    assert context_for(_msg("@memeradar_super_bot 我心情差", chat="group"), ME) == "我心情差"


def test_group_reply_plus_mention_uses_replied_text():
    # 回覆別人那則 + @bot → 上下文＝被回覆的那則（對方講的話）
    msg = _msg("@memeradar_super_bot", reply={"from": {"id": 2}, "text": "你怎麼這麼笨"})
    assert context_for(msg, ME) == "你怎麼這麼笨"


def test_group_without_mention_is_ignored():
    assert context_for(_msg("隨便聊天"), ME) is None


def test_own_message_is_ignored():
    assert context_for(_msg("test", frm=999), ME) is None


class TestFailuresAreDistinguishableFromEmptyResults:
    """API 掛掉不能跟「真的沒梗圖」回同一句話。

    原本 recommend_meme 不管 401、500 還是查無結果一律回 None，bot 就都回
    「找不到合適的梗圖 😅 換句話再試？」——使用者與維運者都無從分辨。2026-08-02 查
    bot 故障時卡在這裡：後端明明健康，卻只能從 Zeabur log 撈 stderr 才知道真正狀態碼。
    """

    def _cfg(self):
        return {"api": "https://api.example", "admin": "u:p", "token": "t"}

    def _patch(self, monkeypatch, *, status=200, payload=None, image=b"img"):
        import memeradar.bot.app as bot

        class Resp:
            def __init__(self, code, data=None, content=b""):
                self.status_code = code
                self._data = data or {}
                self.text = "err"
                self.content = content

            def json(self):
                return self._data

            def raise_for_status(self):
                pass

        monkeypatch.setattr(bot.requests, "post",
                            lambda *a, **k: Resp(status, payload))
        monkeypatch.setattr(bot.requests, "get",
                            lambda *a, **k: Resp(200, content=image))
        return bot

    def test_auth_failure_is_reported_as_a_failure(self, monkeypatch):
        bot = self._patch(monkeypatch, status=401)
        outcome = bot.recommend_meme(self._cfg(), "在幹嘛")
        assert outcome.failed is True
        assert outcome.image is None
        assert "401" in (outcome.detail or ""), "技術細節要留著給維運看"

    def test_server_error_is_reported_as_a_failure(self, monkeypatch):
        bot = self._patch(monkeypatch, status=500)
        assert bot.recommend_meme(self._cfg(), "在幹嘛").failed is True

    def test_empty_results_is_not_a_failure(self, monkeypatch):
        bot = self._patch(monkeypatch, status=200, payload={"results": []})
        outcome = bot.recommend_meme(self._cfg(), "在幹嘛")
        assert outcome.failed is False and outcome.image is None

    def test_success_returns_image(self, monkeypatch):
        bot = self._patch(
            monkeypatch, status=200,
            payload={"results": [{"image_url": "/memes/m_x/image"}]}, image=b"PNGDATA")
        outcome = bot.recommend_meme(self._cfg(), "在幹嘛")
        assert outcome.failed is False and outcome.image == b"PNGDATA"

    def test_the_two_user_messages_differ(self):
        """使用者看到的兩句話必須不同，否則這次修正等於白做。"""
        import memeradar.bot.app as bot

        assert bot.NO_MATCH_REPLY != bot.FAILURE_REPLY


class TestBotTokenPreferredOverAdminPassword:
    """有 bot 專用憑證就別再送後台管理員密碼——那正是這次要消滅的東西。"""

    def _run(self, monkeypatch, cfg):
        import memeradar.bot.app as bot
        seen = {}

        class Resp:
            status_code = 200
            text = ""
            content = b"x"

            def json(self):
                return {"results": []}

            def raise_for_status(self):
                pass

        def fake_post(url, **kw):
            seen.update(auth=kw.get("auth"), headers=kw.get("headers"))
            return Resp()

        monkeypatch.setattr(bot.requests, "post", fake_post)
        monkeypatch.setattr(bot.requests, "get", lambda *a, **k: Resp())
        bot.recommend_meme(cfg, "在幹嘛")
        return seen

    def test_bot_token_used_and_admin_password_not_sent(self, monkeypatch):
        seen = self._run(monkeypatch, {"api": "https://x", "admin": "boss:secret",
                                       "bot_token": "tok-123"})
        assert seen["headers"] == {"X-Bot-Token": "tok-123"}
        assert seen["auth"] is None, "有 bot 憑證時不該再送 admin 帳密"

    def test_falls_back_to_admin_when_no_bot_token(self, monkeypatch):
        """換發期間舊部署還沒設 BOT_TOKEN，不能一改就斷線。"""
        seen = self._run(monkeypatch, {"api": "https://x", "admin": "boss:secret",
                                       "bot_token": ""})
        assert seen["auth"] == ("boss", "secret")
        assert seen["headers"] is None
