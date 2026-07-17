"""
In-process, per-worker token-bucket rate limiting for auth endpoints.

Per-worker (not shared across the -w 2 gunicorn workers), so the effective
ceiling is ~2x nominal — a deliberate, documented constant factor, chosen
over a DB-backed counter to avoid a DB write per login attempt on the auth path.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

LOGIN_BUCKET_CAPACITY = 5
LOGIN_REFILL_INTERVAL_SECONDS = 180
MAX_TRACKED_KEYS = 10_000
IDLE_TTL_SECONDS = 3600


def normalize_username(username: str | None) -> str:
    return (username or "").strip().lower()


@dataclass
class _Bucket:
    tokens: float
    last_updated: float
    last_touch: float


class TokenBucketStore:
    def __init__(
        self,
        *,
        capacity: float,
        refill_interval_seconds: float,
        max_entries: int = MAX_TRACKED_KEYS,
        idle_ttl_seconds: float = IDLE_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self.refill_interval_seconds = refill_interval_seconds
        self.max_entries = max_entries
        self.idle_ttl_seconds = idle_ttl_seconds
        self._time_fn = time_fn
        self._entries: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def try_acquire(self, key: str) -> Optional[float]:
        with self._lock:
            now = self._time_fn()
            bucket = self._entries.get(key)
            if bucket is None:
                # Fresh key: inserting into the OrderedDict already appends at the
                # tail (most-recently-used), so no move_to_end is needed here.
                bucket = _Bucket(
                    tokens=self.capacity - 1,
                    last_updated=now,
                    last_touch=now,
                )
                self._entries[key] = bucket
                self._evict(now)
                return None
            self._refill(bucket, now)
            bucket.last_touch = now
            self._entries.move_to_end(key)
            self._evict(now)
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return None
            return self._retry_after(bucket, now)

    def refund(self, key: str) -> None:
        with self._lock:
            now = self._time_fn()
            bucket = self._entries.get(key)
            if bucket is None:
                return
            self._refill(bucket, now)
            bucket.tokens = min(self.capacity, bucket.tokens + 1)
            bucket.last_touch = now
            self._entries.move_to_end(key)
            self._evict(now)

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = now - bucket.last_updated
        if elapsed < self.refill_interval_seconds:
            return
        refill_count = int(elapsed // self.refill_interval_seconds)
        if refill_count <= 0:
            return
        new_tokens = bucket.tokens + refill_count
        if new_tokens >= self.capacity:
            bucket.tokens = self.capacity
            bucket.last_updated = now
        else:
            bucket.tokens = new_tokens
            bucket.last_updated += refill_count * self.refill_interval_seconds

    def _retry_after(self, bucket: _Bucket, now: float) -> float:
        elapsed_since_update = now - bucket.last_updated
        remaining = self.refill_interval_seconds - elapsed_since_update
        return max(remaining, 0.0)

    def _evict(self, now: float) -> None:
        # OrderedDict is in last_touch order (move_to_end on every touch); expired keys sit at the front.
        while self._entries:
            key, bucket = next(iter(self._entries.items()))
            if now - bucket.last_touch <= self.idle_ttl_seconds:
                break
            del self._entries[key]
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)


login_rate_limiter = TokenBucketStore(
    capacity=LOGIN_BUCKET_CAPACITY,
    refill_interval_seconds=LOGIN_REFILL_INTERVAL_SECONDS,
)
