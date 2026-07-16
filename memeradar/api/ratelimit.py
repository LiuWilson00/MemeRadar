"""公開昂貴端點的限流：每個 key（IP）滑動視窗計數。

單一 replica、in-memory 即可（見 docs/deployment-zeabur.md §9：多 replica 要換 Redis）。
時鐘可注入以利測試；並發下以 lock 保護（FastAPI sync 端點跑在 threadpool）。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 50_000,
    ):
        self._max = max_requests
        self._window = window_seconds
        self._clock = clock
        # OrderedDict 當 LRU：以 key 數硬上限防無界成長。原本 defaultdict 的 key 永不淘汰，
        # 攻擊者輪換 X-Forwarded-For 灌一堆假 key 就能把這張表撐爆 → OOM。
        self._max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """在視窗內未超過上限則記錄並回 True；否則回 False（不記錄）。"""
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                q = self._hits[key] = deque()
            self._hits.move_to_end(key)  # 剛用到 → 移到尾端（LRU）
            while q and q[0] <= cutoff:
                q.popleft()
            allowed = len(q) < self._max
            if allowed:
                q.append(now)
            # 硬上限：不同 key 數超標就淘汰最久未用的（被淘汰者多為過期/不活躍，重置無妨）
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
            return allowed
