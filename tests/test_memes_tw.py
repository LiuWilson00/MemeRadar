"""memes.tw adapter：JSON API → Candidate、增量水位、節流。"""

from __future__ import annotations

import urllib.parse as up

from memeradar.ingestion.memes_tw import MemesTwAdapter, to_candidate


def _meme(i: int, **kw) -> dict:
    base = {
        "id": i,
        "url": f"https://memes.tw/wtf/{i}",
        "src": f"https://memeprod/{i}.jpg",
        "title": f"梗圖{i}",
        "total_like_count": 0,
        "created_at": {"timestamp": 1700000000},
        "hashtag": "",
        "author": {"id": 1, "name": "阿明"},
    }
    base.update(kw)
    return base


def _fake_api(pages: dict[int, list]):
    def get(url: str) -> list:
        q = up.parse_qs(up.urlparse(url).query)
        return pages.get(int(q.get("page", ["1"])[0]), [])

    return get


def _adapter(pages, **kw):
    return MemesTwAdapter(http_get=_fake_api(pages), sleep=lambda s: None, request_delay=0, **kw)


def test_to_candidate_maps_fields():
    c = to_candidate(_meme(10, title=" 嗨 ", total_like_count=42, hashtag="#廢", url="u"))
    assert c.platform == "memes_tw"
    assert c.post_id == "10"
    assert c.post_title == "嗨"
    assert c.post_url == "u"
    assert c.upvotes == 42
    assert c.images == [{"url": "https://memeprod/10.jpg", "order": 0}]
    assert "#廢" in c.top_comments and any("阿明" in x for x in c.top_comments)
    assert c.posted_at.startswith("2023-11-14")  # epoch 1700000000 → UTC


def test_fetch_newest_up_to_max():
    pages = {1: [_meme(105), _meme(104)], 2: [_meme(103), _meme(102)], 3: [_meme(101)]}
    cands, wm = _adapter(pages, max_items=3).fetch(None)
    assert [c.post_id for c in cands] == ["105", "104", "103"]  # 取最新 3 張
    assert wm == "105"  # 水位＝最大 id


def test_incremental_stops_at_watermark():
    pages = {1: [_meme(105), _meme(104), _meme(103), _meme(102)]}
    cands, wm = _adapter(pages, max_items=100).fetch("103")  # 只要 id > 103
    assert [c.post_id for c in cands] == ["105", "104"]
    assert wm == "105"


def test_skips_items_without_src():
    pages = {1: [_meme(10), {"id": 9, "title": "無圖"}, _meme(8)]}
    cands, _ = _adapter(pages, max_items=10).fetch(None)
    assert [c.post_id for c in cands] == ["10", "8"]  # 無 src 的 9 被跳過


def test_contest_uses_distinct_name_and_param():
    seen = {}

    def get(url):
        seen["url"] = url
        return []

    a = MemesTwAdapter(contest=11, http_get=get, sleep=lambda s: None)
    assert a.name == "memes_tw_c11"
    a.fetch(None)
    assert "contest=11" in seen["url"]


def test_empty_page_ends_crawl():
    cands, wm = _adapter({1: [_meme(5)], 2: []}, max_items=100).fetch(None)
    assert [c.post_id for c in cands] == ["5"]
    assert wm == "5"
