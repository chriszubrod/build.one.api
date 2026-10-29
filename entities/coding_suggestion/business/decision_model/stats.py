"""U-698 decision model — evaluation statistics (pure).

``sign_test_p`` is the same exact two-sided binomial test U-697 uses
(``scripts/u697_vector_similar_bills_backtest.sign_test_p``; parity pinned by a test).
"""
from __future__ import annotations

import math
import random
from fractions import Fraction
from typing import Optional, Sequence


def sign_test_p(fixed: int, broke: int) -> Optional[float]:
    n = fixed + broke
    if n == 0:
        return None
    k = min(fixed, broke)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, float(Fraction(2 * tail, 2**n)))


def paired(base_ok: Sequence[bool], other_ok: Sequence[bool]) -> dict:
    """Discordant counts of ``other`` vs ``base`` on the same bills, plus the sign test."""
    if len(base_ok) != len(other_ok):
        raise ValueError("paired arrays differ in length")
    fixed = sum(1 for a, b in zip(base_ok, other_ok) if b and not a)
    broke = sum(1 for a, b in zip(base_ok, other_ok) if a and not b)
    n = len(base_ok)
    return {
        "n": n,
        "base_top1": (sum(base_ok) / n) if n else None,
        "other_top1": (sum(other_ok) / n) if n else None,
        "diff_points": (100.0 * (sum(other_ok) - sum(base_ok)) / n) if n else None,
        "fixed": fixed,
        "broke": broke,
        "sign_test_p": sign_test_p(fixed, broke),
    }


def bootstrap_diff_ci(
    base_ok: Sequence[bool],
    other_ok: Sequence[bool],
    *,
    resamples: int = 1000,
    seed: int = 698,
    alpha: float = 0.05,
) -> Optional[tuple[float, float]]:
    """Percentile CI (in points) of mean(other) - mean(base), resampling bills with replacement."""
    n = len(base_ok)
    if n == 0 or n != len(other_ok):
        return None
    d = [(1 if b else 0) - (1 if a else 0) for a, b in zip(base_ok, other_ok)]
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        s = 0
        for _i in range(n):
            s += d[rng.randrange(n)]
        stats.append(100.0 * s / n)
    stats.sort()
    lo = stats[int(math.floor((alpha / 2) * (resamples - 1)))]
    hi = stats[int(math.ceil((1 - alpha / 2) * (resamples - 1)))]
    return lo, hi


def top1(ok: Sequence[bool], amounts: Optional[Sequence[float]] = None) -> dict:
    n = len(ok)
    out = {"n": n, "bill": (sum(ok) / n) if n else None}
    if amounts is not None:
        tot = sum(amounts)
        out["dollar"] = (sum(a for a, o in zip(amounts, ok) if o) / tot) if tot > 0 else None
    return out


def ece(probs: Sequence[float], correct: Sequence[bool], *, bins: int = 10) -> Optional[float]:
    n = len(probs)
    if n == 0:
        return None
    tot = 0.0
    for row in reliability(probs, correct, bins=bins):
        if row["n"]:
            tot += row["n"] / n * abs(row["mean_prob"] - row["accuracy"])
    return tot


def reliability(probs: Sequence[float], correct: Sequence[bool], *, bins: int = 10) -> list[dict]:
    acc: list[list[float]] = [[0, 0.0, 0.0] for _ in range(bins)]
    for p, c in zip(probs, correct):
        i = min(int(p * bins), bins - 1) if p >= 0 else 0
        acc[i][0] += 1
        acc[i][1] += p
        acc[i][2] += 1.0 if c else 0.0
    return [
        {
            "bin_lo": i / bins,
            "n": int(a[0]),
            "mean_prob": (a[1] / a[0]) if a[0] else None,
            "accuracy": (a[2] / a[0]) if a[0] else None,
        }
        for i, a in enumerate(acc)
    ]


def coverage_at_bars(
    probs: Sequence[Optional[float]],
    correct: Sequence[bool],
    bars: Sequence[float] = (0.5, 0.7, 0.8, 0.9),
) -> list[dict]:
    """Coverage (share of bills with a pick at p >= bar) and precision of those picks.

    A ``None`` prob = the model had no candidate (abstained regardless of bar)."""
    n = len(probs)
    out = []
    for bar in bars:
        kept = [c for p, c in zip(probs, correct) if p is not None and p >= bar]
        out.append(
            {
                "bar": bar,
                "coverage": (len(kept) / n) if n else None,
                "precision": (sum(kept) / len(kept)) if kept else None,
                "n_kept": len(kept),
            }
        )
    return out
