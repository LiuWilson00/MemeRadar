"""輕量進程內快取：TTL 單值（少數昂貴聚合端點）+ 有界 LRU（重複 query 的 embedding）。

單一 replica、in-memory 即可（多 replica 各有一份，短 TTL 下無妨）。皆執行緒安全
（FastAPI sync 端點跑在 threadpool）。時鐘可注入以利測試。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class TTLCache:
    """單值 TTL 快取：給 /leaderboard、/report/dashboard 這類可容忍短暫過期的全表聚合。"""

    def __init__(self, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._value: Any = None
        self._at: float | None = None
        self._lock = threading.Lock()

    def get_or_compute(self, compute: Callable[[], Any]) -> Any:
        with self._lock:
            now = self._clock()
            if self._at is not None and now - self._at < self._ttl:
                return self._value
            # 鎖內計算：序列化並發 miss，避免同一刻多個請求一起打昂貴查詢（thundering herd）
            self._value = compute()
            self._at = now
            return self._value


class LruCache:
    """有界 LRU（key→value）：給重複短 query 的 embedding 結果快取，避免重打 hosted embed。"""

    def __init__(self, max_size: int = 512):
        self._max = max_size
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        # 鎖外計算：embedding 是 HTTP，別佔著鎖（並發 miss 頂多重算一次，可接受）
        value = compute()
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
            return value
