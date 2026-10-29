"""U-698 decision model — pure-Python learners (no numpy / scikit-learn; design Q1).

- :func:`fit_logistic`: L2-regularised logistic regression by Newton/IRLS with step halving,
  optional sign constraints (active set: a violating weight is pinned to 0 and the rest refit).
  Deterministic: no random initialisation, so ``seed`` is not needed for fitting.
- Features are SCALED, not centred (``z = x / sd``): zero stays zero, so rows stay sparse (the
  Hessian accumulation is the hot loop) and a feature that is absent contributes exactly 0 to a
  decision's explanation. The unpenalised bias absorbs the offset.
- :func:`fit_isotonic` (pool-adjacent-violators) and :func:`fit_platt`; :func:`fit_calibrator`
  picks isotonic at >= ``min_isotonic`` calibration points, else Platt (design §4.2).
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Optional, Sequence


def _sigmoid(t: float) -> float:
    if t >= 0:
        return 1.0 / (1.0 + math.exp(-t))
    e = math.exp(t)
    return e / (1.0 + e)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (small dense SPD systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise ArithmeticError("singular system")
        m[col], m[piv] = m[piv], m[col]
        inv = 1.0 / m[col][col]
        for r in range(col + 1, n):
            f = m[r][col] * inv
            if f:
                row_r, row_c = m[r], m[col]
                for k in range(col, n + 1):
                    row_r[k] -= f * row_c[k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


@dataclass
class LogisticModel:
    names: tuple[str, ...]
    weights: list[float]
    bias: float
    scales: list[float]
    l2: float
    iterations: int = 0
    converged: bool = False
    pinned: tuple[str, ...] = ()

    def _z(self, x: Sequence[float]) -> list[float]:
        return [xi / s for xi, s in zip(x, self.scales)]

    def logit(self, x: Sequence[float]) -> float:
        return self.bias + sum(w * z for w, z in zip(self.weights, self._z(x)) if z)

    def prob(self, x: Sequence[float]) -> float:
        return _sigmoid(self.logit(x))

    def contributions(self, x: Sequence[float]) -> list[tuple[str, float]]:
        return [(n, w * z) for n, w, z in zip(self.names, self.weights, self._z(x))]

    def to_json(self) -> dict:
        return {
            "features": list(self.names),
            "weights": [round(w, 6) for w in self.weights],
            "bias": round(self.bias, 6),
            "scales": [round(s, 6) for s in self.scales],
            "l2": self.l2,
            "iterations": self.iterations,
            "converged": self.converged,
            "pinned_to_zero": list(self.pinned),
        }


def _scales(rows: Sequence[Sequence[float]], d: int) -> list[float]:
    n = len(rows)
    out = []
    for j in range(d):
        s1 = s2 = 0.0
        for r in rows:
            v = r[j]
            s1 += v
            s2 += v * v
        var = s2 / n - (s1 / n) ** 2 if n else 0.0
        sd = math.sqrt(var) if var > 1e-12 else 0.0
        out.append(sd if sd > 0 else 1.0)
    return out


def _newton(
    zrows: list[list[tuple[int, float]]],
    y: Sequence[int],
    d: int,
    active: list[int],
    l2: float,
    max_iter: int,
    tol: float,
    weights: Optional[Sequence[float]],
) -> tuple[list[float], float, int, bool]:
    """Fit on the ``active`` feature columns; returns (w over all d, bias, iters, converged)."""
    pos = {j: i for i, j in enumerate(active)}
    k = len(active) + 1  # last slot = bias
    rows = [[(pos[j], v) for j, v in r if j in pos] for r in zrows]
    sw = list(weights) if weights is not None else [1.0] * len(rows)
    beta = [0.0] * k

    def loss(b: list[float]) -> float:
        tot = 0.0
        for r, yi, wi in zip(rows, y, sw):
            t = b[-1] + sum(b[i] * v for i, v in r)
            # log(1 + e^t) - y t, stable
            tot += wi * ((t if t > 0 else 0.0) + math.log1p(math.exp(-abs(t))) - yi * t)
        return tot + 0.5 * l2 * sum(bi * bi for bi in b[:-1])

    cur = loss(beta)
    converged = False
    it = 0
    for it in range(1, max_iter + 1):
        g = [0.0] * k
        h = [[0.0] * k for _ in range(k)]
        for r, yi, wi in zip(rows, y, sw):
            t = beta[-1] + sum(beta[i] * v for i, v in r)
            p = _sigmoid(t)
            e = wi * (p - yi)
            a = wi * p * (1.0 - p)
            for i, v in r:
                g[i] += e * v
            g[-1] += e
            hb = h[-1]
            for ii, (i, vi) in enumerate(r):
                av = a * vi
                hi = h[i]
                for jj in range(ii, len(r)):
                    j, vj = r[jj]
                    hi[j] += av * vj
                hi[-1] += av
            hb[-1] += a
        for i in range(k - 1):
            g[i] += l2 * beta[i]
            h[i][i] += l2
        h[-1][-1] += 1e-9
        # mirror the upper triangle (rows are accumulated with i<=j in r order; r is sorted by j)
        for i in range(k):
            for j in range(i + 1, k):
                v = h[i][j] + h[j][i]
                h[i][j] = h[j][i] = v
        step = _solve(h, g)
        lam = 1.0
        stalled = False
        while True:
            cand = [b - lam * s for b, s in zip(beta, step)]
            new = loss(cand)
            if new <= cur + 1e-12:
                break
            if lam < 1e-6:
                stalled = True
                break
            lam *= 0.5
        if stalled:
            # Step halving bottomed out without a decrease: NOT converged (surfaced in the report).
            converged = False
            break
        delta = max(abs(lam * s) for s in step)
        beta, prev, cur = cand, cur, new
        if delta < tol or abs(prev - cur) < tol * max(1.0, abs(cur)):
            converged = True
            break
    w = [0.0] * d
    for j, i in pos.items():
        w[j] = beta[i]
    return w, beta[-1], it, converged


def fit_logistic(
    x: Sequence[Sequence[float]],
    y: Sequence[int],
    names: Sequence[str],
    *,
    l2: float = 1.0,
    max_iter: int = 50,
    tol: float = 1e-7,
    signs: Optional[Sequence[int]] = None,
    sample_weight: Optional[Sequence[float]] = None,
) -> LogisticModel:
    if not x:
        raise ValueError("no training rows")
    if len(x) != len(y):
        raise ValueError("x / y length mismatch")
    d = len(names)
    scales = _scales(x, d)
    zrows = [[(j, v / scales[j]) for j, v in enumerate(r) if v] for r in x]
    active = list(range(d))
    pinned: list[int] = []
    while True:
        w, b, it, conv = _newton(zrows, y, d, active, l2, max_iter, tol, sample_weight)
        if signs is None:
            break
        bad = [j for j in active if signs[j] and w[j] * signs[j] < 0]
        if not bad:
            break
        worst = min(bad, key=lambda j: w[j] * signs[j])
        active.remove(worst)
        pinned.append(worst)
    return LogisticModel(
        names=tuple(names),
        weights=w,
        bias=b,
        scales=scales,
        l2=l2,
        iterations=it,
        converged=conv,
        pinned=tuple(names[j] for j in pinned),
    )


# ---------------------------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------------------------


@dataclass
class IsotonicMap:
    upper: list[float]   # block upper score bounds (ascending)
    values: list[float]  # calibrated value per block (non-decreasing)
    kind: str = "isotonic"

    def __call__(self, score: float) -> float:
        if not self.upper:
            return score
        i = bisect_left(self.upper, score)
        return self.values[min(i, len(self.values) - 1)]

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "upper": [round(u, 6) for u in self.upper],
            "values": [round(v, 6) for v in self.values],
        }


def fit_isotonic(scores: Sequence[float], y: Sequence[int]) -> IsotonicMap:
    """Pool-adjacent-violators; non-decreasing step function of the score."""
    pairs = sorted(zip(scores, y), key=lambda t: t[0])
    blocks: list[list[float]] = []  # [sum_y, count, max_score]
    for s, yi in pairs:
        blocks.append([float(yi), 1.0, s])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            sy, n, hi = blocks.pop()
            blocks[-1][0] += sy
            blocks[-1][1] += n
            blocks[-1][2] = hi
    return IsotonicMap([b[2] for b in blocks], [b[0] / b[1] for b in blocks])


@dataclass
class PlattMap:
    a: float
    b: float
    kind: str = "platt"

    def __call__(self, score: float) -> float:
        s = min(max(score, 1e-6), 1 - 1e-6)
        return _sigmoid(self.a * math.log(s / (1 - s)) + self.b)

    def to_json(self) -> dict:
        return {"kind": self.kind, "a": round(self.a, 6), "b": round(self.b, 6)}


def fit_platt(scores: Sequence[float], y: Sequence[int]) -> PlattMap:
    xs = []
    for s in scores:
        s = min(max(s, 1e-6), 1 - 1e-6)
        xs.append([math.log(s / (1 - s))])
    m = fit_logistic(xs, y, ("logit",), l2=1e-3)
    return PlattMap(m.weights[0] / m.scales[0], m.bias)


@dataclass
class IdentityMap:
    kind: str = "identity"

    def __call__(self, score: float) -> float:
        return score

    def to_json(self) -> dict:
        return {"kind": self.kind}


def fit_calibrator(scores: Sequence[float], y: Sequence[int], *, min_isotonic: int = 300):
    if len(scores) >= min_isotonic:
        return fit_isotonic(scores, y)
    if len(scores) >= 20 and 0 < sum(y) < len(y):
        return fit_platt(scores, y)
    return IdentityMap()


# ---------------------------------------------------------------------------------------------
# Ranking + explanation
# ---------------------------------------------------------------------------------------------


@dataclass
class Decision:
    scc_id: Optional[int]
    raw_prob: float
    prob: float
    runner_up: Optional[int]
    top_contributions: list[tuple[str, float]] = field(default_factory=list)

    @property
    def abstained(self) -> bool:
        return self.scc_id is None


def rank_candidates(
    candidates: Sequence[int],
    raw_probs: Sequence[float],
    *,
    calibrate=None,
    abstain_below: float = 0.0,
    contributions: Optional[Sequence[Sequence[tuple[str, float]]]] = None,
    top_k: int = 3,
    tiebreak: Optional[Sequence[float]] = None,
) -> Decision:
    """Arg-max over the candidate set; abstain below the calibrated bar.

    Ties on ``raw_probs`` fall back to ``tiebreak`` (the RAW M0 score), then to candidate-set
    order (history rank, then vote rank, then LLM) - never to the scc id."""
    if not candidates:
        return Decision(None, 0.0, 0.0, None)
    tb = tiebreak if tiebreak is not None else raw_probs
    order = sorted(range(len(candidates)), key=lambda i: (-raw_probs[i], -tb[i], i))
    best = order[0]
    raw = raw_probs[best]
    prob = calibrate(raw) if calibrate is not None else raw
    runner = candidates[order[1]] if len(order) > 1 else None
    contrib = top_contributions(contributions[best], k=top_k) if contributions else []
    if prob < abstain_below:
        return Decision(None, raw, prob, candidates[best], contrib)
    return Decision(candidates[best], raw, prob, runner, contrib)


def top_contributions(contribs: Sequence[tuple[str, float]], *, k: int = 3) -> list[tuple[str, float]]:
    nz = [(n, v) for n, v in contribs if v]
    return sorted(nz, key=lambda t: (-abs(t[1]), t[0]))[:k]
