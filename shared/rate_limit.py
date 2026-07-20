"""
In-process, per-worker token-bucket rate limiting for auth endpoints.

Per-worker (not shared across the -w 2 gunicorn workers), so the effective
ceiling is ~2x nominal — a deliberate, documented constant factor, chosen
over a DB-backed counter to avoid a DB write per login attempt on the auth path.

Login endpoints enforce two independent buckets: per-username and per-client-IP.
The IP key is derived from ``x-client-ip`` or ``x-forwarded-for`` (in that
order). On Azure App Service (direct, no Front Door/App Gateway as of
2026-07-20 prod verification) the front end **appends** the real client IP to
both headers rather than overwriting them, so a client-forged value appears as
``'6.6.6.6, <real-ip>'``. Only the **rightmost** comma-separated token of
either header may be trusted; tokens to its left are client-spoofable.
``x-azure-clientip`` / ``x-azure-socketip`` pass through unchanged and must
never be consulted; ``request.client.host`` is the internal LB (169.254.x).
If neither header yields a valid IP the per-IP bucket is skipped (fail open);
username limiting still applies. Re-tune the rightmost-token rule if Front
Door or App Gateway is added — both headers would then end with the proxy IP,
not the client's.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

LOGIN_BUCKET_CAPACITY = 5
LOGIN_REFILL_INTERVAL_SECONDS = 180
IP_BUCKET_CAPACITY = 20
IP_REFILL_INTERVAL_SECONDS = 60
MAX_TRACKED_KEYS = 10_000
IDLE_TTL_SECONDS = 3600
# IPv6 clients are keyed by this prefix so an attacker can't rotate freely
# inside one subscriber allocation (a /64 is the standard end-user delegation).
IPV6_KEY_PREFIX_BITS = 64
# Precedence order of the headers the Azure App Service front end appends the
# real client IP to (prod-verified 2026-07-20). Only their rightmost token is
# trusted — see the module docstring.
TRUSTED_CLIENT_IP_HEADERS = ("x-client-ip", "x-forwarded-for")


def normalize_username(username: str | None) -> str:
    return (username or "").strip().lower()


def normalize_client_ip(token: str | None) -> str | None:
    if not token or not token.strip():
        return None
    token = token.strip()
    if token.startswith("["):
        closing = token.find("]")
        if closing == -1:
            return None
        remainder = token[closing + 1 :]
        if remainder and not (remainder.startswith(":") and remainder[1:].isdigit()):
            return None
        token = token[1:closing]
    elif token.count(":") == 1:
        host, port = token.rsplit(":", 1)
        if not port.isdigit():
            return None
        token = host
    if "%" in token:
        # Zone-IDs (fe80::1%eth0) are host-local and never legitimate in a
        # forwarded header; ipaddress.ip_address() would accept them.
        return None
    try:
        addr = ipaddress.ip_address(token)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv4Address):
        return str(addr)
    if addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return str(ipaddress.ip_network(f"{addr}/{IPV6_KEY_PREFIX_BITS}", strict=False))


def client_ip_rate_key(headers) -> str | None:
    """Derive the per-IP rate-limit key from a request's headers, or None.

    ``headers`` is any mapping with ``getlist(name)`` (e.g. Starlette Headers).
    Duplicate physical header lines are comma-joined before applying the
    rightmost-token rule — Azure already merges duplicates and appends the real
    client IP last (prod-verified 2026-07-20); the join keeps the rule correct
    even if duplicate lines ever survive to the app. Returns a *key* (IPv6
    collapses to its /64 network), not necessarily a literal client IP.
    """
    for name in TRUSTED_CLIENT_IP_HEADERS:
        header_value = ",".join(headers.getlist(name))
        if not header_value:
            continue
        for part in reversed(header_value.split(",")):
            part = part.strip()
            if part:
                normalized = normalize_client_ip(part)
                if normalized is not None:
                    return normalized
                break
    return None


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

login_ip_rate_limiter = TokenBucketStore(
    capacity=IP_BUCKET_CAPACITY,
    refill_interval_seconds=IP_REFILL_INTERVAL_SECONDS,
)
