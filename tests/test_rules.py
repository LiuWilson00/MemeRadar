"""P3-4 測試：規則引擎價值過濾（規格：docs/02 §5 第一層）。"""

from memeradar.ingestion.base import Candidate
from memeradar.ingestion.rules import RuleConfig, check_candidate, check_image, check_image_url


def candidate(**overrides) -> Candidate:
    fields = {
        "platform": "reddit",
        "post_id": "p1",
        "post_url": "https://reddit.com/p1",
        "post_title": "標題",
        "top_comments": [],
        "upvotes": 500,
        "posted_at": "2026-07-11T00:00:00+00:00",
        "images": [{"url": "https://i.redd.it/x.png", "order": 0}],
    }
    fields.update(overrides)
    return Candidate(**fields)


RULES = RuleConfig()


class TestCandidateRules:
    def test_passes_normal_candidate(self):
        assert check_candidate(candidate(), RULES) is None

    def test_upvote_threshold_by_platform(self):
        assert check_candidate(candidate(upvotes=50), RULES) == "互動門檻"
        # dcard 門檻較低（50）
        assert check_candidate(candidate(platform="dcard", upvotes=60), RULES) is None
        # 未知平台用預設門檻
        assert check_candidate(candidate(platform="ptt", upvotes=5), RULES) == "互動門檻"

    def test_none_upvotes_passes(self):
        # 無互動數的來源（如人工）不套門檻
        assert check_candidate(candidate(upvotes=None), RULES) is None


class TestImageUrlRules:
    def test_unsupported_extension_rejected(self):
        assert check_image_url("https://i.redd.it/x.gif", RULES) == "不支援格式"
        assert check_image_url("https://v.redd.it/clip", RULES) == "不支援格式"
        assert check_image_url("https://i.redd.it/x.png?width=640", RULES) is None

    def test_blocklist(self):
        rules = RuleConfig(url_blocklist=("adserver.example",))
        assert check_image_url("https://adserver.example/x.png", rules) == "網址黑名單"


class TestImageRules:
    def test_min_short_side(self):
        assert check_image(150, 800, RULES) == "尺寸過小"
        assert check_image(200, 800, RULES) is None

    def test_aspect_ratio(self):
        assert check_image(200, 900, RULES) == "長寬比異常"  # 1:4.5
        assert check_image(200, 800, RULES) is None  # 恰為 1:4
        assert check_image(900, 200, RULES) == "長寬比異常"
