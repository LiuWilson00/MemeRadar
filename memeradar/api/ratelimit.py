"""公開昂貴端點的限流：每個 key（IP）滑動視窗計數。

單一 replica、in-memory 即可（見 docs/deployment-zeabur.md §9：多 replica 要換 Redis）。
時鐘可注入以利測試；並發下以 lock 保護（FastAPI sync 端點跑在 threadpool）。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max = max_requests
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """在視窗內未超過上限則記錄並回 True；否則回 False（不記錄）。"""
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self._max:
                return False
            q.append(now)
            return True
