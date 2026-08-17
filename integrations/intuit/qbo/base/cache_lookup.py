"""Shared cache-vs-DB read primitive for QBO pull connectors (U-247)."""


def cached_or_read(caches_preloaded: bool, cache: dict, key, read_fn):
    """
    Return `cache.get(key)` when `caches_preloaded` is True, else `read_fn(key)`.

    `caches_preloaded` must be an explicit flag, never cache-dict truthiness — a
    non-preloaded caller's cache can still be non-empty-but-incomplete mid-pass
    (see U-247), so testing the dict itself is a correctness bug, not just style.
    """
    if caches_preloaded:
        return cache.get(key)
    return read_fn(key)
