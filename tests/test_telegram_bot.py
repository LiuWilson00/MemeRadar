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
