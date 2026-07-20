import math
import threading

from starlette.datastructures import Headers

from shared.rate_limit import (
    IDLE_TTL_SECONDS,
    LOGIN_BUCKET_CAPACITY,
    LOGIN_REFILL_INTERVAL_SECONDS,
    MAX_TRACKED_KEYS,
    TokenBucketStore,
    client_ip_rate_key,
    normalize_client_ip,
    normalize_username,
)


def _headers(x_client_ip: str | None = None, x_forwarded_for: str | None = None) -> Headers:
    raw = []
    if x_client_ip is not None:
        raw.append((b"x-client-ip", x_client_ip.encode()))
    if x_forwarded_for is not None:
        raw.append((b"x-forwarded-for", x_forwarded_for.encode()))
    return Headers(raw=raw)


def _fake_clock(start: float = 0.0):
    current = [start]

    def time_fn() -> float:
        return current[0]

    def advance(seconds: float) -> None:
        current[0] += seconds

    return time_fn, advance


def test_try_acquire_allows_capacity_then_throttles():
    time_fn, _advance = _fake_clock()
    store = TokenBucketStore(
        capacity=5,
        refill_interval_seconds=180,
        time_fn=time_fn,
    )
    key = "user@example.com"
    for _ in range(5):
        assert store.try_acquire(key) is None
    retry_after = store.try_acquire(key)
    assert retry_after is not None
    assert retry_after > 0


def test_refill_proven_by_consumption():
    time_fn, advance = _fake_clock()
    store = TokenBucketStore(
        capacity=5,
        refill_interval_seconds=180,
        time_fn=time_fn,
    )
    key = "user@example.com"
    for _ in range(5):
        assert store.try_acquire(key) is None
    assert store.try_acquire(key) is not None
    advance(180)
    assert store.try_acquire(key) is None
    assert store.try_acquire(key) is not None


def test_no_banked_credit_when_full():
    time_fn, advance = _fake_clock()
    capacity = 5
    interval = 180
    store = TokenBucketStore(
        capacity=capacity,
        refill_interval_seconds=interval,
        time_fn=time_fn,
    )
    key = "user@example.com"
    for _ in range(capacity):
        assert store.try_acquire(key) is None
    advance(capacity * interval + 179)
    for _ in range(capacity):
        assert store.try_acquire(key) is None
    retry_after = store.try_acquire(key)
    assert retry_after is not None
    assert retry_after >= 179
    assert retry_after <= 181


def test_refund_returns_token_capped_at_capacity():
    time_fn, _advance = _fake_clock()
    capacity = 5
    store = TokenBucketStore(
        capacity=capacity,
        refill_interval_seconds=180,
        time_fn=time_fn,
    )
    key = "user@example.com"
    for _ in range(capacity):
        store.try_acquire(key)
    assert store.try_acquire(key) is not None
    store.refund(key)
    assert store.try_acquire(key) is None
    for _ in range(10):
        store.refund(key)
    for _ in range(capacity):
        assert store.try_acquire(key) is None
    assert store.try_acquire(key) is not None


def test_success_is_free_try_acquire_then_refund():
    time_fn, _advance = _fake_clock()
    capacity = 5
    store = TokenBucketStore(
        capacity=capacity,
        refill_interval_seconds=180,
        time_fn=time_fn,
    )
    key = "user@example.com"
    for _ in range(capacity + 100):
        assert store.try_acquire(key) is None
        store.refund(key)


def test_atomicity_under_concurrency():
    time_fn, _advance = _fake_clock()
    capacity = 5
    store = TokenBucketStore(
        capacity=capacity,
        refill_interval_seconds=180,
        time_fn=time_fn,
    )
    key = "user@example.com"
    thread_count = 50
    barrier = threading.Barrier(thread_count)
    allowed = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        result = store.try_acquire(key)
        with lock:
            allowed.append(result is None)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(allowed) == capacity


def test_memory_cap_evicts_beyond_max_tracked_keys():
    time_fn, _advance = _fake_clock()
    store = TokenBucketStore(
        capacity=5,
        refill_interval_seconds=180,
        max_entries=MAX_TRACKED_KEYS,
        time_fn=time_fn,
    )
    for i in range(20_000):
        store.try_acquire(f"user{i}@example.com")
    assert len(store) <= MAX_TRACKED_KEYS


def test_ttl_evicts_idle_and_preserves_live_bucket_behind_expired():
    time_fn, advance = _fake_clock()
    store = TokenBucketStore(
        capacity=5,
        refill_interval_seconds=180,
        idle_ttl_seconds=IDLE_TTL_SECONDS,
        time_fn=time_fn,
    )
    expired_key = "expired@example.com"
    live_key = "live@example.com"
    store.try_acquire(expired_key)
    assert len(store) == 1
    advance(IDLE_TTL_SECONDS + 1)
    store.try_acquire(live_key)
    store.try_acquire("trigger@example.com")
    assert len(store) == 2
    for _ in range(4):
        assert store.try_acquire(live_key) is None
    assert store.try_acquire(live_key) is not None


def test_normalization_maps_variants_to_one_bucket():
    time_fn, _advance = _fake_clock()
    store = TokenBucketStore(
        capacity=5,
        refill_interval_seconds=180,
        time_fn=time_fn,
    )
    variants = ["User@X ", " user@x", "USER@X"]
    normalized = {normalize_username(v) for v in variants}
    assert len(normalized) == 1
    key = normalized.pop()
    for _ in range(5):
        store.try_acquire(key)
    retry_after = store.try_acquire(normalize_username(variants[0]))
    assert retry_after is not None
    assert retry_after > 0


def test_retry_after_never_zero_in_router_header():
    time_fn, _advance = _fake_clock()
    store = TokenBucketStore(
        capacity=1,
        refill_interval_seconds=180,
        time_fn=time_fn,
    )
    key = "user@example.com"
    assert store.try_acquire(key) is None
    retry_after = store.try_acquire(key)
    assert retry_after is not None
    header_value = max(1, math.ceil(retry_after))
    assert header_value >= 1


def test_extract_client_ip_spoof_prepend_ignored():
    assert client_ip_rate_key(_headers(x_client_ip="6.6.6.6, 1.2.3.4")) == "1.2.3.4"


def test_extract_client_ip_xff_fallback_port_stripped():
    assert client_ip_rate_key(_headers(x_forwarded_for="6.6.6.6, 1.2.3.4:2996")) == "1.2.3.4"


def test_extract_client_ip_prefers_x_client_ip():
    assert client_ip_rate_key(_headers(x_client_ip="9.9.9.9", x_forwarded_for="1.2.3.4:80")) == "9.9.9.9"


def test_extract_client_ip_ipv6_bracket_form_collapses_to_slash64():
    assert client_ip_rate_key(_headers(x_forwarded_for="[2001:db8::1]:443")) == "2001:db8::/64"


def test_normalize_client_ip_same_slash64_maps_to_same_key():
    assert normalize_client_ip("2001:db8::1") == "2001:db8::/64"
    assert normalize_client_ip("2001:db8::ffff") == "2001:db8::/64"
    assert normalize_client_ip("2001:db8::1") == normalize_client_ip("2001:db8::ffff")


def test_normalize_client_ip_different_slash64_maps_to_different_keys():
    assert normalize_client_ip("2001:db8:1::1") == "2001:db8:1::/64"
    assert normalize_client_ip("2001:db8:2::1") == "2001:db8:2::/64"
    assert normalize_client_ip("2001:db8:1::1") != normalize_client_ip("2001:db8:2::1")


def test_extract_client_ip_invalid_and_missing():
    assert client_ip_rate_key(_headers(x_client_ip="not-an-ip")) is None
    assert client_ip_rate_key(_headers()) is None
    assert client_ip_rate_key(_headers(x_client_ip="", x_forwarded_for="")) is None


def test_extract_client_ip_rightmost_invalid_does_not_fallback():
    assert client_ip_rate_key(_headers(x_client_ip="6.6.6.6, junk")) is None


def test_normalize_client_ip_ipv4_mapped_distinct_keys():
    assert normalize_client_ip("::ffff:192.0.2.128") == "192.0.2.128"
    assert normalize_client_ip("::ffff:203.0.113.9") == "203.0.113.9"


def test_normalize_client_ip_rejects_invalid_port_and_zone():
    assert normalize_client_ip("1.2.3.4:bad") is None
    assert normalize_client_ip("1.2.3.4:") is None
    assert normalize_client_ip("[2001:db8::1]junk") is None
    assert normalize_client_ip("fe80::1%eth0") is None


def test_normalize_client_ip_bracket_port_still_collapses_to_slash64():
    assert normalize_client_ip("[2001:db8::1]:443") == "2001:db8::/64"


def test_extract_client_ip_merged_duplicate_line_shape():
    assert client_ip_rate_key(_headers(x_client_ip="6.6.6.6, 7.7.7.7, 1.2.3.4")) == "1.2.3.4"


def test_client_ip_rate_key_joins_duplicate_physical_lines():
    headers = Headers(
        raw=[(b"x-client-ip", b"6.6.6.6"), (b"x-client-ip", b"7.7.7.7, 1.2.3.4")]
    )
    assert client_ip_rate_key(headers) == "1.2.3.4"
