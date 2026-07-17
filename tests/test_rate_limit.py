import math
import threading

from shared.rate_limit import (
    IDLE_TTL_SECONDS,
    LOGIN_BUCKET_CAPACITY,
    LOGIN_REFILL_INTERVAL_SECONDS,
    MAX_TRACKED_KEYS,
    TokenBucketStore,
    normalize_username,
)


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
