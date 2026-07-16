"""公開昂貴端點的限流（滑動視窗）。時鐘可注入，測試不依賴真實時間。"""

from __future__ import annotations

from memeradar.api.ratelimit import RateLimiter


def _clock():
    box = {"t": 0.0}
    return box, (lambda: box["t"])


def test_allows_up_to_max_then_blocks():
    box, clk = _clock()
    rl = RateLimiter(3, 60, clock=clk)
    assert [rl.allow("ip1") for _ in range(3)] == [True, True, True]
    assert rl.allow("ip1") is False  # 第 4 次超限


def test_window_slides():
    box, clk = _clock()
    rl = RateLimiter(2, 10, clock=clk)
    assert rl.allow("ip1") and rl.allow("ip1")
    assert rl.allow("ip1") is False
    box["t"] = 10.1  # 視窗過去
    assert rl.allow("ip1") is True


def test_keys_are_independent():
    box, clk = _clock()
    rl = RateLimiter(1, 60, clock=clk)
    assert rl.allow("ip1") is True
    assert rl.allow("ip2") is True  # 不同 key 各自計數
    assert rl.allow("ip1") is False


def test_key_table_is_bounded_lru():
    """防 X-Forwarded-For 洗 key 撐爆記憶體：不同 key 數超過 max_keys 就淘汰最久未用的。"""
    box, clk = _clock()
    rl = RateLimiter(5, 60, clock=clk, max_keys=10)
    for i in range(100):
        rl.allow(f"ip{i}")
    assert len(rl._hits) <= 10  # 表被硬上限綁住，不隨 key 數無界成長
    assert "ip99" in rl._hits  # 最近用到的還在
    assert "ip0" not in rl._hits  # 最久未用的被淘汰
