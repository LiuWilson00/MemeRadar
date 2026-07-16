"""輕量進程內快取：TTL 單值 + 有界 LRU。時鐘可注入，測試不依賴真實時間。"""

from __future__ import annotations

from memeradar.shared.cache import LruCache, TTLCache


def test_ttl_caches_within_window_then_recomputes():
    box = {"t": 0.0}
    calls = {"n": 0}
    c = TTLCache(10.0, clock=lambda: box["t"])

    def compute():
        calls["n"] += 1
        return calls["n"]

    assert c.get_or_compute(compute) == 1
    assert c.get_or_compute(compute) == 1  # 視窗內：不重算
    assert calls["n"] == 1
    box["t"] = 10.1  # 過期
    assert c.get_or_compute(compute) == 2
    assert calls["n"] == 2


def test_lru_caches_by_key():
    calls = {"n": 0}
    c = LruCache(max_size=2)

    def compute(v):
        calls["n"] += 1
        return v

    assert c.get_or_compute("a", lambda: compute("A")) == "A"
    assert c.get_or_compute("a", lambda: compute("A")) == "A"  # 命中：不重算
    assert calls["n"] == 1


def test_lru_evicts_least_recently_used():
    c = LruCache(max_size=2)
    c.get_or_compute("a", lambda: 1)
    c.get_or_compute("b", lambda: 2)
    c.get_or_compute("a", lambda: 99)  # 觸碰 a → a 變最近用（99 不會取代既有的 1）
    c.get_or_compute("c", lambda: 3)  # 超上限 → 淘汰最久未用的 b

    assert c.get_or_compute("a", lambda: -1) == 1  # a 還在（原值 1）
    assert c.get_or_compute("c", lambda: -1) == 3  # c 還在
    recomputed = {"n": 0}

    def rc():
        recomputed["n"] += 1
        return 22

    c.get_or_compute("b", rc)  # b 被淘汰過 → 重算
    assert recomputed["n"] == 1
