"""
U-697: read-only backtest — vector-embedding similar-bill neighbours vs word neighbours.

Reuses the U-691/U-694 harness (``--text-source pdf``) so ONLY the similarity measure changes.
Three arms are scored in one process over the same bills:

- **W** (words): ``u691.score_all_rolling`` + ``u691.pick_variants_on_calibration`` on the PDF
  ``TokenSourceConfig`` — re-establishes the U-694 baseline.
- **V** (vectors): cosine of unit-normalised ``text-embedding-3-small`` vectors x amount band;
  same four variants, ``_top_k_scored`` + ``vote_from_scored_neighbours`` (K=10), one rolling pass,
  calibration-only variant selection with u691's helpers. Optional **V'** (``--vector-prime``):
  weight ``max(0, (cos - c0) / (1 - c0))``, c0 picked on calibration only from a fixed grid.
- **H** (hybrid): reciprocal-rank fusion per variant of the W and V neighbour rankings.

Cosine is pure Python (``array('f')`` + ``sum(map(operator.mul, ...))``); numpy is not in the
venv, so there is no vectorised path.

Privacy: raw PDF text lives in memory only until embedded. The embedding cache
(``.cache/u697/``, gitignored, kept for U-698) stores bill id + text sha256 + vector only.
Outputs carry numeric ids / scores only and pass ``assert_outputs_privacy_safe``.

Spend: the embedder meters ``usage.prompt_tokens`` x ``EMBEDDING_USD_PER_MILLION_TOKENS`` and
aborts BEFORE any request that could push spend past ``--max-cost-usd``. This module does not
use the Claude daily-budget ledger or any Anthropic transport (embeddings are Azure OpenAI).

Usage (repo root):
  .venv/bin/python -m scripts.u697_vector_similar_bills_backtest          # dry-run (default)
  .venv/bin/python -m scripts.u697_vector_similar_bills_backtest --run    # paid embedding run
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import operator
import re
import sys
import threading
import time
from array import array
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import httpx

from entities.coding_suggestion.business.similar_bills import (
    _pdf_text_from_raw_blob,
    _top_k_scored,
    amount_band_factor_logs,
    idf_weighted_jaccard,
    is_prior_eligible,
    tokenize_bill_text,
    vote_from_scored_neighbours,
)
from entities.coding_suggestion.business.similar_bills import _log_abs_amount
from intelligence.transport.foundry import foundry_resource_root
from scripts import u691_similar_bills_backtest as u691
from scripts.sync_helper import assert_cli_system_admin
from scripts.u653b_backtest import (
    DEFAULT_CAL_SINCE,
    DEFAULT_CAL_UNTIL,
    DEFAULT_EVAL_SINCE,
    DEFAULT_EVAL_UNTIL,
    MIN_PRIOR_BILLS,
    _date_only,
    validate_windows,
)
from scripts.u654a_text_backtest import _json_default, assert_outputs_privacy_safe

MODEL_VERSION = "u697-vector-similar-bills-v1"

# --- embedding constants -------------------------------------------------------------------
DEFAULT_EMBEDDING_DEPLOYMENT = "text-embedding-3-small"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_FULL_DIMENSIONS = 1536
# text-embedding-3-small list price: $0.02 per 1M input tokens.
EMBEDDING_USD_PER_MILLION_TOKENS = Decimal("0.02")
URL_STYLE_DEPLOYMENTS = "deployments"
URL_STYLE_MODELS = "models"
URL_STYLES = (URL_STYLE_DEPLOYMENTS, URL_STYLE_MODELS)
DEFAULT_API_VERSION_DEPLOYMENTS = "2024-10-21"
DEFAULT_API_VERSION_MODELS = "2024-05-01-preview"
DEFAULT_BATCH_SIZE = 64
MAX_CONCURRENCY = 2
MAX_ATTEMPTS = 5
MAX_RETRY_SLEEP_SECONDS = 60.0
DEFAULT_MAX_CHARS = 24000
MIN_RETRUNCATE_CHARS = 256
DEFAULT_MAX_COST_USD = Decimal("1.00")
CHARS_PER_TOKEN_ESTIMATE = 3
DEFAULT_CACHE_DIR = Path(".cache/u697")
CACHE_FILENAME = "embeddings.jsonl"
DEFAULT_OUT_DIR = Path("docs/design/u697")
DEFAULT_BILL_IDS_FROM = Path(
    "docs/design/u701b/u654a_predictions_claude-haiku_20261028T150518Z.csv"
)

# --- arm constants ---------------------------------------------------------------------------
ARM_W = "W"
ARM_V = "V"
ARM_V_PRIME = "V_prime"
ARM_H = "H"
ARM_W_GENERIC = "W_generic"
RRF_K = 60
RRF_DEPTH = 100
V_PRIME_C0_GRID = (0.1, 0.2, 0.3, 0.4, 0.5)

# U-694 PDF baseline (docs/design/u691/u694_report_20261021T123148Z_pdf.md).
U694_BASELINE = {
    "pdf_bills": 3757,
    "eval_pdf_population": 2374,
    "cal_pdf_population": 1338,
    "top1_bill": {
        "V_any_vendor": 0.610,
        "V_other_vendor": 0.276,
        "V_same_vendor": 0.605,
        "V_any_vendor_same_project": 0.512,
        "combined": 0.626,
        "combined_mode_b": 0.611,
    },
}

_CONTEXT_LENGTH_RE = re.compile(
    r"context[_ ]length|maximum context|too many tokens|max(imum)? (input )?tokens"
    # Azure text-embedding-3-small (2026-10-29): "Invalid 'input[0]': maximum input length is 8192 tokens."
    r"|max(imum)? input length",
    re.IGNORECASE,
)
# Azure/OpenAI ``error.code`` values meaning "too many tokens for this request / input":
# handled like a context-length 400 (split the batch, then halve the single input).
_TOO_MANY_TOKENS_CODE_RE = re.compile(
    r"context_length_exceeded|max_tokens_per_request|too_many_tokens|tokens?_limit"
    r"|tokens_exceeded|string_above_max_length",
    re.IGNORECASE,
)
_SAFE_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")


def azure_error_code(resp: httpx.Response) -> Optional[str]:
    """``error.code`` from Azure's JSON error envelope — never the message or body text."""
    try:
        payload = resp.json()
    except Exception:
        return None
    err = payload.get("error") if isinstance(payload, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    if not isinstance(code, str):
        return None
    return code if _SAFE_ERROR_CODE_RE.fullmatch(code) else "unparseable"


def truncation_policy_version(max_chars: int) -> str:
    return f"head{int(max_chars)}-bisect-then-halve-v2"


# =============================================================================================
# Embedder
# =============================================================================================


class EmbeddingError(RuntimeError):
    pass


class ParityError(RuntimeError):
    """The arms do not see the same bills / the generic word scorer drifted from u691."""


class SpendCapExceeded(RuntimeError):
    """Raised BEFORE a request whose estimated cost would push spend past the cap."""


def estimate_tokens(texts: Iterable[str]) -> int:
    return sum(max(1, math.ceil(len(t) / CHARS_PER_TOKEN_ESTIMATE)) for t in texts)


def usd_for_tokens(tokens: int) -> Decimal:
    return Decimal(int(tokens)) * EMBEDDING_USD_PER_MILLION_TOKENS / Decimal(1_000_000)


def normalise(vec: Sequence[float]) -> array:
    out = array("f", vec)
    norm = math.sqrt(math.fsum(x * x for x in out))
    if norm > 0:
        out = array("f", (x / norm for x in out))
    return out


def dot(a: array, b: array) -> float:
    return sum(map(operator.mul, a, b))


@dataclass
class EmbeddingSpend:
    model: str
    deployment: str
    dimensions: Optional[int]
    cap_usd: Decimal
    requests: int = 0
    prompt_tokens: int = 0
    cache_hits: int = 0
    truncated: int = 0
    retruncated: int = 0
    embedded: int = 0
    # Sends that ended in a transport error (e.g. ReadTimeout): Azure may have billed them,
    # so their estimate is counted in ``prompt_tokens`` (worst case) and against the cap.
    unconfirmed_attempts: int = 0
    unconfirmed_tokens: int = 0

    @property
    def usd(self) -> Decimal:
        return usd_for_tokens(self.prompt_tokens)

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "deployment": self.deployment,
            "dimensions": self.dimensions or EMBEDDING_FULL_DIMENSIONS,
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "usd": float(self.usd),
            "usd_per_million_tokens": float(EMBEDDING_USD_PER_MILLION_TOKENS),
            "cap": float(self.cap_usd),
            "cache_hits": self.cache_hits,
            "embedded": self.embedded,
            "truncated": self.truncated,
            "retruncated": self.retruncated,
            "unconfirmed_attempts": self.unconfirmed_attempts,
            "unconfirmed_tokens": self.unconfirmed_tokens,
        }


class FoundryEmbedder:
    """Sync Azure OpenAI embeddings client on the Foundry resource (injectable transport)."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str = DEFAULT_EMBEDDING_DEPLOYMENT,
        model: str = DEFAULT_EMBEDDING_MODEL,
        url_style: str = URL_STYLE_DEPLOYMENTS,
        api_version: Optional[str] = None,
        dimensions: Optional[int] = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_cost_usd: Decimal = DEFAULT_MAX_COST_USD,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 120.0,
    ) -> None:
        if url_style not in URL_STYLES:
            raise ValueError(f"url_style must be one of {URL_STYLES}")
        if not api_key:
            raise EmbeddingError("foundry_api_key is not configured")
        self.root = foundry_resource_root(endpoint)
        if not self.root:
            raise EmbeddingError("foundry_endpoint is not configured")
        self.deployment = deployment
        self.model = model
        self.url_style = url_style
        self.api_version = api_version or (
            DEFAULT_API_VERSION_DEPLOYMENTS
            if url_style == URL_STYLE_DEPLOYMENTS
            else DEFAULT_API_VERSION_MODELS
        )
        self.dimensions = dimensions
        self.max_chars = int(max_chars)
        self.policy_version = truncation_policy_version(self.max_chars)
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"api-key": api_key, "Content-Type": "application/json"},
        )
        self._lock = threading.Lock()
        self._reserved_tokens = 0
        self.spend = EmbeddingSpend(
            model=model,
            deployment=deployment,
            dimensions=dimensions,
            cap_usd=Decimal(str(max_cost_usd)),
        )

    # -- url / body ----------------------------------------------------------------------
    @property
    def url(self) -> str:
        if self.url_style == URL_STYLE_DEPLOYMENTS:
            return (
                f"{self.root}/openai/deployments/{self.deployment}/embeddings"
                f"?api-version={self.api_version}"
            )
        return f"{self.root}/models/embeddings?api-version={self.api_version}"

    def _body(self, inputs: Sequence[str]) -> dict[str, Any]:
        body: dict[str, Any] = {"input": list(inputs)}
        if self.url_style == URL_STYLE_MODELS:
            body["model"] = self.model
        if self.dimensions:
            body["dimensions"] = int(self.dimensions)
        return body

    def truncate(self, text: str) -> str:
        return text[: self.max_chars]

    # -- spend ---------------------------------------------------------------------------
    def _reserve(self, est_tokens: int) -> None:
        with self._lock:
            projected = usd_for_tokens(
                self.spend.prompt_tokens + self._reserved_tokens + est_tokens
            )
            if projected > self.spend.cap_usd:
                raise SpendCapExceeded(
                    "U-697 embedding spend cap: "
                    f"spent ${float(self.spend.usd):.6f} + in-flight/estimate "
                    f"{self._reserved_tokens + est_tokens} tokens would exceed cap "
                    f"${float(self.spend.cap_usd):.2f}; aborting before the request"
                )
            self._reserved_tokens += est_tokens

    def _release(self, est_tokens: int) -> None:
        with self._lock:
            self._reserved_tokens -= est_tokens

    def _charge_unconfirmed(self, est_tokens: int) -> None:
        """A send whose outcome is unknown (transport error): assume it was billed."""
        with self._lock:
            self._reserved_tokens -= est_tokens
            self.spend.prompt_tokens += est_tokens
            self.spend.unconfirmed_attempts += 1
            self.spend.unconfirmed_tokens += est_tokens

    def _settle(self, est_tokens: int, used_tokens: Optional[int]) -> None:
        with self._lock:
            self._reserved_tokens -= est_tokens
            self.spend.requests += 1
            self.spend.prompt_tokens += (
                int(used_tokens) if used_tokens is not None else est_tokens
            )

    # -- http ----------------------------------------------------------------------------
    @staticmethod
    def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
        ms = resp.headers.get("retry-after-ms")
        if ms:
            try:
                return min(MAX_RETRY_SLEEP_SECONDS, float(ms) / 1000.0)
            except ValueError:
                pass
        ra = resp.headers.get("retry-after")
        if ra:
            try:
                return min(MAX_RETRY_SLEEP_SECONDS, float(ra))
            except ValueError:
                pass
        return min(MAX_RETRY_SLEEP_SECONDS, float(2**attempt))

    def _post(self, inputs: Sequence[str]) -> list[array]:
        """One logical request; EVERY (re-)send is reserved against the cap first.

        Raises ``_ContextLength`` on a 400 that means "too many tokens" (by ``error.code`` or
        the context-length wording), ``EmbeddingError`` (with ``error.code`` only) otherwise.
        """
        est = estimate_tokens(inputs)
        last_err: Optional[str] = None
        for attempt in range(MAX_ATTEMPTS):
            self._reserve(est)
            reserved = True
            try:
                try:
                    resp = self._client.post(self.url, json=self._body(inputs))
                except httpx.TransportError as exc:
                    self._charge_unconfirmed(est)
                    reserved = False
                    last_err = f"transport error {type(exc).__name__}"
                    if attempt + 1 < MAX_ATTEMPTS:
                        self._sleep(min(MAX_RETRY_SLEEP_SECONDS, float(2**attempt)))
                    continue
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = (
                        f"HTTP {resp.status_code} error.code={azure_error_code(resp) or 'n/a'}"
                    )
                    if attempt + 1 < MAX_ATTEMPTS:
                        self._sleep(self._retry_after_seconds(resp, attempt))
                    continue
                code = azure_error_code(resp)
                if resp.status_code == 400 and (
                    (code is not None and _TOO_MANY_TOKENS_CODE_RE.search(code))
                    or _CONTEXT_LENGTH_RE.search(resp.text or "")
                ):
                    raise _ContextLength()
                if resp.status_code != 200:
                    raise EmbeddingError(
                        f"embedding request failed: HTTP {resp.status_code} "
                        f"error.code={code or 'n/a'}"
                    )
                try:
                    payload = resp.json()
                    items = sorted(
                        payload.get("data") or [], key=lambda d: d.get("index", 0)
                    )
                    if len(items) != len(inputs):
                        raise EmbeddingError(
                            f"embedding response has {len(items)} vectors for "
                            f"{len(inputs)} inputs"
                        )
                    usage = (payload.get("usage") or {}).get("prompt_tokens")
                except Exception as exc:
                    # A 200 is billed even when its body is unusable: count it as spent.
                    self._charge_unconfirmed(est)
                    reserved = False
                    if isinstance(exc, EmbeddingError):
                        raise
                    raise EmbeddingError(
                        f"embedding response unparseable ({type(exc).__name__})"
                    ) from None
                self._settle(est, usage)
                reserved = False
                vectors = [normalise(item["embedding"]) for item in items]
                if self.dimensions:
                    for v in vectors:
                        if len(v) != self.dimensions:
                            raise EmbeddingError("embedding dimension mismatch")
                return vectors
            finally:
                if reserved:
                    self._release(est)
        raise EmbeddingError(
            f"embedding request failed after {MAX_ATTEMPTS} attempts ({last_err})"
        )

    def _embed_single_halving(self, text: str) -> array:
        """The single input was rejected as too long: halve it until accepted."""
        with self._lock:
            self.spend.retruncated += 1
        current = text
        while True:
            if len(current) <= MIN_RETRUNCATE_CHARS:
                raise EmbeddingError(
                    f"too-many-tokens 400 persists after truncating to {len(current)} chars"
                )
            current = current[: max(MIN_RETRUNCATE_CHARS, len(current) // 2)]
            try:
                return self._post([current])[0]
            except _ContextLength:
                continue

    def _embed_prepared(self, prepared: Sequence[str]) -> list[array]:
        try:
            return self._post(prepared)
        except _ContextLength:
            if len(prepared) == 1:
                return [self._embed_single_halving(prepared[0])]
            mid = len(prepared) // 2
            return self._embed_prepared(prepared[:mid]) + self._embed_prepared(prepared[mid:])

    def embed_texts(self, texts: Sequence[str]) -> list[array]:
        """Embed one batch (already a batch: caller splits). Head-truncates to max_chars.

        A too-many-tokens 400 bisects the batch; a rejected single input is halved.
        """
        prepared: list[str] = []
        n_trunc = 0
        for t in texts:
            if len(t) > self.max_chars:
                n_trunc += 1
            prepared.append(self.truncate(t))
        with self._lock:
            self.spend.truncated += n_trunc
        vectors = self._embed_prepared(prepared)
        with self._lock:
            self.spend.embedded += len(vectors)
        return vectors

    def close(self) -> None:
        self._client.close()


class _ContextLength(Exception):
    pass


# =============================================================================================
# Cache (JSONL, no raw text)
# =============================================================================================


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encode_vector(vec: array) -> str:
    a = array("f", vec)
    if sys.byteorder == "big":
        a.byteswap()
    return base64.b64encode(a.tobytes()).decode("ascii")


def decode_vector(data: str) -> array:
    a = array("f")
    a.frombytes(base64.b64decode(data))
    if sys.byteorder == "big":
        a.byteswap()
    return a


@dataclass(frozen=True)
class CacheKey:
    model: str
    deployment: str
    dimensions: Optional[int]
    truncation_policy_version: str


class EmbeddingCache:
    """Append-only JSONL: one row per (bill, model, deployment, dimensions, policy)."""

    def __init__(self, cache_dir: Path) -> None:
        self.path = Path(cache_dir) / CACHE_FILENAME
        self._rows: dict[tuple, dict[str, Any]] = {}
        self._lock = threading.Lock()
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    self._rows[self._row_key(row)] = row

    @staticmethod
    def _row_key(row: Mapping[str, Any]) -> tuple:
        return (
            int(row["bill_id"]),
            row.get("model"),
            row.get("deployment"),
            row.get("dimensions"),
            row.get("truncation_policy_version"),
        )

    @staticmethod
    def _key(bill_id: int, key: CacheKey) -> tuple:
        return (
            int(bill_id),
            key.model,
            key.deployment,
            key.dimensions,
            key.truncation_policy_version,
        )

    def get(self, bill_id: int, sha: str, key: CacheKey) -> Optional[array]:
        row = self._rows.get(self._key(bill_id, key))
        if row is None or row.get("text_sha256") != sha:
            return None
        return decode_vector(row["vector"])

    def put(self, bill_id: int, sha: str, key: CacheKey, vector: array) -> None:
        row = {
            "bill_id": int(bill_id),
            "text_sha256": sha,
            "model": key.model,
            "deployment": key.deployment,
            "dimensions": key.dimensions,
            "truncation_policy_version": key.truncation_policy_version,
            "vector": encode_vector(vector),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            self._rows[self._row_key(row)] = row


@dataclass(frozen=True)
class CachePlan:
    hits: dict[int, array]
    misses: list[int]
    sha_by_bill: dict[int, str]
    estimated_miss_tokens: int

    @property
    def projected_usd(self) -> Decimal:
        return usd_for_tokens(self.estimated_miss_tokens)


def plan_embeddings(
    texts_by_bill: Mapping[int, str],
    cache: EmbeddingCache,
    key: CacheKey,
    *,
    max_chars: int,
) -> CachePlan:
    hits: dict[int, array] = {}
    misses: list[int] = []
    shas: dict[int, str] = {}
    est = 0
    for bid in sorted(texts_by_bill):
        text = texts_by_bill[bid]
        sha = text_sha256(text)
        shas[bid] = sha
        vec = cache.get(bid, sha, key)
        if vec is not None:
            hits[bid] = vec
        else:
            misses.append(bid)
            est += estimate_tokens([text[:max_chars]])
    return CachePlan(hits, misses, shas, est)


def embed_population(
    texts_by_bill: Mapping[int, str],
    embedder: FoundryEmbedder,
    cache: EmbeddingCache,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    concurrency: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> dict[int, array]:
    """Cache hits never call; misses go one-input smoke call first, then batches."""
    if concurrency < 1 or concurrency > MAX_CONCURRENCY:
        raise ValueError(f"concurrency must be 1..{MAX_CONCURRENCY}")
    key = CacheKey(
        embedder.model, embedder.deployment, embedder.dimensions, embedder.policy_version
    )
    plan = plan_embeddings(texts_by_bill, cache, key, max_chars=embedder.max_chars)
    vectors: dict[int, array] = dict(plan.hits)
    embedder.spend.cache_hits += len(plan.hits)
    misses = list(plan.misses)
    if not misses:
        return vectors

    def _do(batch_ids: list[int]) -> None:
        vecs = embedder.embed_texts([texts_by_bill[b] for b in batch_ids])
        for bid, vec in zip(batch_ids, vecs):
            cache.put(bid, plan.sha_by_bill[bid], key, vec)
            vectors[bid] = vec

    total = len(misses)
    _do(misses[:1])  # smoke call: one input, counted like any other request
    done = 1
    if progress is not None:
        progress(done, total)
    rest = misses[1:]
    batches = [rest[i : i + batch_size] for i in range(0, len(rest), batch_size)]
    if concurrency == 1:
        for batch in batches:
            _do(batch)
            done += len(batch)
            if progress is not None:
                progress(done, total)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for i in range(0, len(batches), concurrency):
                group = batches[i : i + concurrency]
                futures = [pool.submit(_do, b) for b in group]
                errors = []
                for fut in futures:
                    try:
                        fut.result()
                    except BaseException as exc:  # surface after the group settles
                        errors.append(exc)
                if errors:
                    raise errors[0]
                done += sum(len(b) for b in group)
                if progress is not None:
                    progress(done, total)
    return vectors


# =============================================================================================
# Loading (PDF tokens AND text, same population as the word arm)
# =============================================================================================


@dataclass
class Population:
    enriched: list[u691.EnrichedBill]
    pdf_tokens_by_bill_id: dict[int, frozenset[str]]
    pdf_text_by_bill_id: dict[int, str]
    load_stats: Optional[u691.PdfTextLoadStats] = None


def _read_one_pdf_tokens_and_text(
    bill_id: int,
    url: str,
    read_blob: Callable[[str], str],
    timeout_seconds: float,
) -> tuple[int, Optional[frozenset[str]], Optional[str], str]:
    try:
        raw = u691._read_blob_with_timeout(read_blob, url, timeout_seconds)
    except Exception:
        return bill_id, None, None, "error"
    text, outcome = _pdf_text_from_raw_blob(raw)
    if outcome != "ok" or text is None:
        return bill_id, None, None, outcome
    return bill_id, tokenize_bill_text(text), text, "ok"


def load_pdf_tokens_and_text(
    blob_urls: Mapping[int, str],
    *,
    read_blob: Callable[[str], str],
    max_workers: int = u691.DEFAULT_PDF_READ_WORKERS,
    timeout_seconds: float = u691.DEFAULT_PDF_READ_TIMEOUT_SECONDS,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[int, frozenset[str]], dict[int, str], u691.PdfTextLoadStats]:
    """Like ``u691.load_pdf_text_by_bill_id`` but also returns the (TIN-masked) text."""
    counts = {"ok": 0, "stale": 0, "empty": 0, "error": 0}
    tokens: dict[int, frozenset[str]] = {}
    texts: dict[int, str] = {}
    total = len(blob_urls)
    if blob_urls:
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(_read_one_pdf_tokens_and_text, bid, url, read_blob, timeout_seconds)
                for bid, url in blob_urls.items()
            ]
            for fut in futures:
                done += 1
                if progress is not None:
                    progress(done, total)
                try:
                    bid, toks, text, outcome = fut.result()
                except Exception:
                    counts["error"] += 1
                    continue
                if outcome == "ok" and toks is not None and text is not None:
                    tokens[bid] = toks
                    texts[bid] = text
                    counts["ok"] += 1
                else:
                    counts[outcome if outcome in counts else "error"] += 1
    stats = u691.PdfTextLoadStats(
        linked=total,
        read_ok=counts["ok"],
        stale_redaction=counts["stale"],
        read_error=counts["error"],
        empty_content=counts["empty"],
    )
    return tokens, texts, stats


def load_population_from_db(
    *, max_workers: int, timeout_seconds: float
) -> Population:  # pragma: no cover - reads production; never run in tests
    from scripts.u654a_text_backtest import load_blob_urls_from_db
    from shared.text_blob_read import make_text_blob_reader

    enriched = u691.load_enriched_bills_from_db()
    blob_urls = u691.filter_blob_urls_to_coded_bills(load_blob_urls_from_db(), enriched)

    def _progress(done: int, total: int) -> None:
        if done == total or done % max(1, total // 20) == 0:
            print(f"U-697: PDF blob reads {done}/{total}", flush=True)

    tokens, texts, stats = load_pdf_tokens_and_text(
        blob_urls,
        read_blob=make_text_blob_reader(),
        max_workers=max_workers,
        timeout_seconds=timeout_seconds,
        progress=_progress,
    )
    return Population(enriched, tokens, texts, stats)


# =============================================================================================
# Vector arm
# =============================================================================================


@dataclass(frozen=True)
class VectorProfile:
    enriched: u691.EnrichedBill
    vector: Optional[array]
    log_abs_amount: float


def vector_profile(eb: u691.EnrichedBill, vector: Optional[array]) -> VectorProfile:
    return VectorProfile(eb, vector, _log_abs_amount(eb.bill.total_abs_amount))


def cosine_similarity(query: Any, prior: Any) -> float:
    if query.vector is None or prior.vector is None:
        return 0.0
    return dot(query.vector, prior.vector)


def shifted_cosine_weight(c0: float) -> Callable[[float], float]:
    def _w(cos: float) -> float:
        return max(0.0, (cos - c0) / (1.0 - c0))

    return _w


def identity_weight(cos: float) -> float:
    return cos


def eligible_banded_priors(
    query: Any,
    priors: Sequence[Any],
    *,
    band_pct: float,
    cap_ratio: float,
) -> list[tuple[Any, float]]:
    """Strictly-earlier, non-self priors whose amount band factor is > 0 (filter BEFORE scoring)."""
    qbill = query.enriched.bill
    as_of = qbill.created
    out: list[tuple[Any, float]] = []
    for prior in priors:
        if not is_prior_eligible(prior.enriched.bill, qbill, as_of=as_of):
            continue
        band = amount_band_factor_logs(
            query.log_abs_amount,
            prior.log_abs_amount,
            band_pct=band_pct,
            cap_ratio=cap_ratio,
        )
        if band <= 0:
            continue
        out.append((prior, band))
    return out


def partition_by_variant(
    scored: Sequence[tuple[float, Any]],
    query_bill: Any,
) -> dict[str, list[tuple[float, int, int]]]:
    """Same four-way split as ``u691.predict_all_variants``; only sim > 0 rows are kept."""
    q_vendor = query_bill.vendor_id
    q_project = query_bill.label_project_id
    parts: dict[str, list[tuple[float, int, int]]] = {v: [] for v in u691.VARIANTS}
    for sim, prior in scored:
        if sim <= 0:
            continue
        pb = prior.enriched.bill
        row = (sim, pb.bill_id, pb.label_scc_id)
        parts["V_any_vendor"].append(row)
        if pb.vendor_id == q_vendor:
            parts["V_same_vendor"].append(row)
        else:
            parts["V_other_vendor"].append(row)
        pp = pb.label_project_id
        if q_project is not None and pp is not None and pp == q_project:
            parts["V_any_vendor_same_project"].append(row)
    return parts


def votes_from_partition(
    parts: Mapping[str, list[tuple[float, int, int]]], top_k: int
) -> tuple[dict[str, u691.SimilarPrediction], dict[str, list[tuple[float, int, int]]]]:
    preds: dict[str, u691.SimilarPrediction] = {}
    tops: dict[str, list[tuple[float, int, int]]] = {}
    for v in u691.VARIANTS:
        top = _top_k_scored(list(parts[v]), top_k)
        tops[v] = top
        preds[v] = u691._vote_from_scored(top, top_k)
    return preds, tops


def predict_all_variants_vector(
    query: Any,
    priors: Sequence[Any],
    *,
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    similarity: Callable[[Any, Any], float] = cosine_similarity,
    weight: Callable[[float], float] = identity_weight,
) -> dict[str, u691.SimilarPrediction]:
    """sim = weight(similarity(q, p)) x band; candidates are band-filtered first."""
    scored = [
        (weight(similarity(query, prior)) * band, prior)
        for prior, band in eligible_banded_priors(
            query, priors, band_pct=band_pct, cap_ratio=cap_ratio
        )
    ]
    preds, _ = votes_from_partition(partition_by_variant(scored, query.enriched.bill), top_k)
    return preds


def rrf_fuse(
    ranked_a: Sequence[tuple[float, int, int]],
    ranked_b: Sequence[tuple[float, int, int]],
    *,
    k: int = RRF_K,
    depth: int = RRF_DEPTH,
) -> list[tuple[float, int, int]]:
    """Reciprocal-rank fusion: 1/(k+rank_a) + 1/(k+rank_b) (1-based ranks, top ``depth``)."""
    fused: dict[int, float] = defaultdict(float)
    scc: dict[int, int] = {}
    for ranked in (ranked_a, ranked_b):
        ordered = sorted(ranked, key=lambda x: (-x[0], x[1]))[:depth]
        for rank, (_sim, bid, scc_id) in enumerate(ordered, start=1):
            fused[bid] += 1.0 / (k + rank)
            scc[bid] = scc_id
    out = [(score, bid, scc[bid]) for bid, score in fused.items()]
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


@dataclass
class _RollingPrior:
    enriched: u691.EnrichedBill
    log_abs_amount: float
    vector: Optional[array]
    token_profile: Any


@dataclass
class VectorPassResult:
    arms: dict[str, list[u691.FullyScoredBill]]
    # arm -> bill_id -> (same-vendor neighbours, total) among any-vendor top-K
    reidentification: dict[str, dict[int, tuple[int, int]]]
    missing_vectors: int
    runtime_seconds: float
    word_parity_mismatches: Optional[int] = None


def score_vector_rolling(
    enriched: Sequence[u691.EnrichedBill],
    *,
    vectors: Mapping[int, array],
    history_source: Sequence[u691.FullyScoredBill],
    token_config: u691.TokenSourceConfig,
    idf: Optional[dict[str, float]],
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    v_prime_grid: Sequence[float] = (),
    include_word: bool = True,
    include_hybrid: bool = True,
    rrf_k: int = RRF_K,
    rrf_depth: int = RRF_DEPTH,
) -> VectorPassResult:
    """
    ONE rolling pass. Per (query, prior) the band, cosine and word score are computed once and
    shared by V, every V' c0, W_generic and H. ``W_generic`` (the word neighbours, used for the
    W parity check and W's re-identification rate) does not depend on the hybrid; H needs it.
    History modes are copied from ``history_source`` (the W arm).
    """
    if include_hybrid and not include_word:
        raise ValueError("the hybrid arm needs the word scores (include_word=True)")
    t0 = time.perf_counter()
    history_by_id = {row.enriched.bill.bill_id: row for row in history_source}
    ordered = sorted(enriched, key=lambda eb: (eb.bill.created, eb.bill.bill_id))
    cos_arms: dict[str, Callable[[float], float]] = {ARM_V: identity_weight}
    for c0 in v_prime_grid:
        cos_arms[f"{ARM_V_PRIME}_c{c0:g}"] = shifted_cosine_weight(c0)
    arm_names = list(cos_arms)
    if include_word:
        arm_names.append(ARM_W_GENERIC)
    if include_hybrid:
        arm_names.append(ARM_H)
    arms: dict[str, list[u691.FullyScoredBill]] = {a: [] for a in arm_names}
    reid: dict[str, dict[int, tuple[int, int]]] = {a: {} for a in arm_names}
    priors: list[_RollingPrior] = []
    missing = 0

    for eb in ordered:
        bid = eb.bill.bill_id
        hs = history_by_id[bid]
        eligible = u691.similarity_eligible(eb, token_config)
        if not eligible:
            for a in arm_names:
                arms[a].append(
                    u691.FullyScoredBill(
                        eb, hs.history_a, hs.history_b, u691.empty_similar_predictions()
                    )
                )
            continue
        qvec = vectors.get(bid)
        if qvec is None:
            missing += 1
        qtok = (
            u691.token_profile(eb, idf, config=token_config)
            if include_word and idf is not None
            else None
        )
        query = _RollingPrior(eb, _log_abs_amount(eb.bill.total_abs_amount), qvec, qtok)
        banded = eligible_banded_priors(
            query, priors, band_pct=band_pct, cap_ratio=cap_ratio
        )
        cos_rows: list[tuple[float, _RollingPrior, float]] = []
        for prior, band in banded:
            if qvec is None or prior.vector is None:
                continue
            cos_rows.append((dot(qvec, prior.vector), prior, band))

        tops_by_arm: dict[str, dict[str, list[tuple[float, int, int]]]] = {}
        parts_by_arm: dict[str, dict[str, list[tuple[float, int, int]]]] = {}
        for arm, wfn in cos_arms.items():
            scored = [(wfn(c) * band, p) for c, p, band in cos_rows]
            parts = partition_by_variant(scored, eb.bill)
            preds, tops = votes_from_partition(parts, top_k)
            parts_by_arm[arm] = parts
            tops_by_arm[arm] = tops
            arms[arm].append(u691.FullyScoredBill(eb, hs.history_a, hs.history_b, preds))

        if include_word:
            word_scored: list[tuple[float, _RollingPrior]] = []
            if qtok is not None:
                for prior, band in banded:
                    pt = prior.token_profile
                    if pt is None:
                        continue
                    jac = idf_weighted_jaccard(
                        qtok.tokens, qtok.idf_weight_sum, pt.tokens, pt.idf_weight_sum, idf
                    )
                    if jac <= 0:
                        continue
                    word_scored.append((jac * band, prior))
            wparts = partition_by_variant(word_scored, eb.bill)
            wpreds, wtops = votes_from_partition(wparts, top_k)
            parts_by_arm[ARM_W_GENERIC] = wparts
            tops_by_arm[ARM_W_GENERIC] = wtops
            arms[ARM_W_GENERIC].append(
                u691.FullyScoredBill(eb, hs.history_a, hs.history_b, wpreds)
            )
        if include_hybrid:
            wparts = parts_by_arm[ARM_W_GENERIC]
            hparts = {
                v: rrf_fuse(wparts[v], parts_by_arm[ARM_V][v], k=rrf_k, depth=rrf_depth)
                for v in u691.VARIANTS
            }
            hpreds, htops = votes_from_partition(hparts, top_k)
            tops_by_arm[ARM_H] = htops
            arms[ARM_H].append(u691.FullyScoredBill(eb, hs.history_a, hs.history_b, hpreds))

        vendor_by_bid = {p.enriched.bill.bill_id: p.enriched.bill.vendor_id for p, _ in banded}
        for arm, tops in tops_by_arm.items():
            top_any = tops["V_any_vendor"]
            same = sum(1 for _s, nb, _c in top_any if vendor_by_bid.get(nb) == eb.bill.vendor_id)
            reid[arm][bid] = (same, len(top_any))

        priors.append(query)

    return VectorPassResult(
        arms=arms,
        reidentification=reid,
        missing_vectors=missing,
        runtime_seconds=time.perf_counter() - t0,
    )


# =============================================================================================
# Calibration-only selection, diagnostics
# =============================================================================================


def select_variants_from_scored(
    scored: Sequence[u691.FullyScoredBill],
    *,
    cal_since: date,
    cal_until: date,
    token_config: u691.TokenSourceConfig,
) -> u691.CalibrationSelection:
    """Same rule as ``u691.pick_variants_on_calibration``, from rows already scored.

    Only calibration-window rows in the similarity population are read.
    """
    records: dict[str, list[tuple[bool, bool, Decimal]]] = {v: [] for v in u691.VARIANTS}
    for row in scored:
        d = _date_only(row.enriched.bill.created)
        if not (cal_since <= d <= cal_until):
            continue
        if not u691.similarity_eligible(row.enriched, token_config):
            continue
        label = row.enriched.bill.label_scc_id
        amt = row.enriched.bill.total_abs_amount
        for v in u691.VARIANTS:
            sp = row.similar[v]
            if sp.predicted_scc_id is None:
                records[v].append((False, False, amt))
            else:
                records[v].append((True, sp.predicted_scc_id == label, amt))
    metrics = {v: u691._metrics_from_cal_records(records[v]) for v in u691.VARIANTS}
    return u691.CalibrationSelection(
        best_variant=u691._pick_best_by_cal_bill_acc(metrics, u691.VARIANTS),
        best_variant_mode_b=u691._pick_best_by_cal_bill_acc(metrics, u691.VARIANTS_NO_PROJECT),
        calibration_metrics=metrics,
        warnings=tuple(u691._selection_warnings(metrics, u691.VARIANTS)),
    )


def pick_v_prime_c0(
    selections: Mapping[float, u691.CalibrationSelection],
) -> float:
    """Highest calibration top-1-over-all of the selected Mode A variant; ties -> smaller c0."""
    best_c0: Optional[float] = None
    best_acc = -1.0
    for c0 in sorted(selections):
        sel = selections[c0]
        acc = sel.calibration_metrics.get(sel.best_variant, {}).get("bill")
        acc = -1.0 if acc is None else acc
        if acc > best_acc:
            best_acc = acc
            best_c0 = c0
    if best_c0 is None:
        raise ValueError("empty V' grid")
    return best_c0


def no_earlier_same_vendor_ids(
    enriched: Sequence[u691.EnrichedBill],
    token_config: u691.TokenSourceConfig,
) -> frozenset[int]:
    """Stratum (c): bills with NO strictly-earlier same-vendor bill in the PDF population."""
    pop = [eb for eb in enriched if u691.similarity_eligible(eb, token_config)]
    earliest: dict[int, datetime] = {}
    for eb in pop:
        v = eb.bill.vendor_id
        if v not in earliest or eb.bill.created < earliest[v]:
            earliest[v] = eb.bill.created
    return frozenset(
        eb.bill.bill_id for eb in pop if eb.bill.created <= earliest[eb.bill.vendor_id]
    )


def reidentification_rate(
    reid: Mapping[int, tuple[int, int]], bill_ids: Iterable[int]
) -> dict[str, Optional[float]]:
    same = total = 0
    per_bill: list[float] = []
    for bid in bill_ids:
        s, t = reid.get(bid, (0, 0))
        same += s
        total += t
        if t:
            per_bill.append(s / t)
    return {
        "neighbour_share": (same / total) if total else None,
        "mean_per_bill": (sum(per_bill) / len(per_bill)) if per_bill else None,
        "neighbours": total,
    }


def sign_test_p(fixed: int, broke: int) -> Optional[float]:
    """Two-sided exact binomial sign test (p = 0.5) on discordant pairs."""
    n = fixed + broke
    if n == 0:
        return None
    k = min(fixed, broke)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, float(Fraction(2 * tail, 2**n)))


def paired_compare(
    rows_base: Sequence[u691.FullyScoredBill],
    rows_other: Sequence[u691.FullyScoredBill],
    pred_base: Callable[[u691.FullyScoredBill], Optional[int]],
    pred_other: Callable[[u691.FullyScoredBill], Optional[int]],
) -> dict[str, Any]:
    other_by_id = {r.enriched.bill.bill_id: r for r in rows_other}
    fixed = broke = both = neither = 0
    for rb in rows_base:
        ro = other_by_id.get(rb.enriched.bill.bill_id)
        if ro is None:
            continue
        label = rb.enriched.bill.label_scc_id
        a = pred_base(rb) == label
        b = pred_other(ro) == label
        if a and b:
            both += 1
        elif b:
            fixed += 1
        elif a:
            broke += 1
        else:
            neither += 1
    return {
        "fixed": fixed,
        "broke": broke,
        "both_right": both,
        "both_wrong": neither,
        "sign_test_p": sign_test_p(fixed, broke),
    }


def _sim_pred(variant: str) -> Callable[[u691.FullyScoredBill], Optional[int]]:
    return lambda r: r.similar[variant].predicted_scc_id


def _top1(rows: Sequence[u691.FullyScoredBill], fn: Callable[[Any], Any]) -> dict[str, Any]:
    block = u691.head_to_head_block(rows, fn)
    block["n"] = len(rows)
    return block


def load_bill_ids_from_csv(path: Path) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    with Path(path).open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("BillId") or "").strip()
            if not raw.isdigit():
                continue
            bid = int(raw)
            if bid not in seen:
                seen.add(bid)
                ids.append(bid)
    return ids


@dataclass
class ArmResult:
    name: str
    scored: list[u691.FullyScoredBill]
    selection: u691.CalibrationSelection
    reidentification: dict[int, tuple[int, int]] = field(default_factory=dict)
    note: str = ""


def partition_sample_ids(
    bill_ids: Sequence[int],
    *,
    enriched: Sequence[u691.EnrichedBill],
    token_config: u691.TokenSourceConfig,
    cal_since: date,
    cal_until: date,
    eval_since: date,
    eval_until: date,
) -> tuple[frozenset[int], dict[str, int]]:
    """Keep only evaluation-window bills in the PDF population; count why the rest dropped."""
    by_id = {eb.bill.bill_id: eb for eb in enriched}
    kept: set[int] = set()
    dropped = {
        "not_loaded": 0,
        "not_in_pdf_population": 0,
        "calibration_window": 0,
        "outside_both_windows": 0,
    }
    for bid in dict.fromkeys(bill_ids):
        eb = by_id.get(bid)
        if eb is None:
            dropped["not_loaded"] += 1
            continue
        if not u691.similarity_eligible(eb, token_config):
            dropped["not_in_pdf_population"] += 1
            continue
        d = _date_only(eb.bill.created)
        if eval_since <= d <= eval_until:
            kept.add(bid)
        elif cal_since <= d <= cal_until:
            dropped["calibration_window"] += 1
        else:
            dropped["outside_both_windows"] += 1
    return frozenset(kept), dropped


def sample_500_block(
    arms: Mapping[str, ArmResult],
    bill_ids: Sequence[int],
    *,
    source: Optional[str],
    enriched: Sequence[u691.EnrichedBill],
    token_config: u691.TokenSourceConfig,
    cal_since: date,
    cal_until: date,
    eval_since: date,
    eval_until: date,
) -> dict[str, Any]:
    kept, dropped = partition_sample_ids(
        bill_ids,
        enriched=enriched,
        token_config=token_config,
        cal_since=cal_since,
        cal_until=cal_until,
        eval_since=eval_since,
        eval_until=eval_until,
    )
    out: dict[str, Any] = {
        "source": source,
        "ids_in_file": len(set(bill_ids)),
        "evaluated": len(kept),
        "dropped": dropped,
        "arms": {},
    }
    for name, arm in arms.items():
        rows = [r for r in arm.scored if r.enriched.bill.bill_id in kept]
        sel = arm.selection
        out["arms"][name] = {
            "n": len(rows),
            "history_strong_else_vote_a": _top1(
                rows,
                lambda r, s=sel: u691._PredView(
                    u691.combined_prediction(r, best_variant=s.best_variant)
                ),
            )["bill"],
            "history_strong_else_vote_b": _top1(
                rows,
                lambda r, s=sel: u691._PredView(
                    u691.combined_prediction_mode_b(r, best_variant=s.best_variant_mode_b)
                ),
            )["bill"],
            "best_variant": sel.best_variant,
            "best_variant_mode_b": sel.best_variant_mode_b,
        }
    return out


def build_comparison(
    arms: Mapping[str, ArmResult],
    *,
    enriched: Sequence[u691.EnrichedBill],
    token_config: u691.TokenSourceConfig,
    population: u691.PopulationCounts,
    cal_since: date,
    cal_until: date,
    eval_since: date,
    eval_until: date,
    bill_ids_500: Optional[Sequence[int]],
    bill_ids_500_source: Optional[str],
    spend: Optional[EmbeddingSpend],
    runtimes: Mapping[str, float],
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    eval_by_arm = {
        name: u691.eval_window_rows(
            arm.scored, eval_since=eval_since, eval_until=eval_until, token_config=token_config
        )
        for name, arm in arms.items()
    }
    stratum_c = no_earlier_same_vendor_ids(enriched, token_config)

    main: dict[str, Any] = {}
    calibration: dict[str, Any] = {}
    cold: dict[str, Any] = {}
    reid_out: dict[str, Any] = {}
    combined: dict[str, Any] = {}
    for name, arm in arms.items():
        rows = eval_by_arm[name]
        sel = arm.selection
        block = {v: _top1(rows, lambda r, v=v: r.similar[v]) for v in u691.VARIANTS}
        block["combined"] = _top1(
            rows,
            lambda r, s=sel: u691._PredView(
                u691.combined_prediction(r, best_variant=s.best_variant)
            ),
        )
        block["combined_mode_b"] = _top1(
            rows,
            lambda r, s=sel: u691._PredView(
                u691.combined_prediction_mode_b(r, best_variant=s.best_variant_mode_b)
            ),
        )
        block["history_a"] = _top1(rows, lambda r: r.history_a)
        block["history_b"] = _top1(rows, lambda r: r.history_b)
        main[name] = block
        calibration[name] = {
            "best_variant": sel.best_variant,
            "best_variant_mode_b": sel.best_variant_mode_b,
            "metrics": sel.calibration_metrics,
            "warnings": list(sel.warnings),
        }
        hist_a = block["history_a"]["bill"]
        hist_b = block["history_b"]["bill"]
        combined[name] = {
            "combined_a": block["combined"]["bill"],
            "lift_a": (
                block["combined"]["bill"] - hist_a
                if block["combined"]["bill"] is not None and hist_a is not None
                else None
            ),
            "combined_b": block["combined_mode_b"]["bill"],
            "lift_b": (
                block["combined_mode_b"]["bill"] - hist_b
                if block["combined_mode_b"]["bill"] is not None and hist_b is not None
                else None
            ),
        }
        fill = u691.compute_fill_in_metrics(rows)
        abstain_rows = [r for r in rows if u691.history_abstained(r.history_a)]
        stratum_rows = [r for r in rows if r.enriched.bill.bill_id in stratum_c]
        no_prior = {}
        for v in u691.VARIANTS:
            buckets = u691.breakdown_by_vendor_consistency(
                rows, [r.enriched for r in arm.scored], v
            )
            no_prior[v] = buckets.get("no_prior")
        cold[name] = {
            "history_abstain": {
                "n": fill.abstain_count,
                "accuracy": {
                    v: (fill.abstain_correct[v] / fill.abstain_count)
                    if fill.abstain_count
                    else None
                    for v in u691.VARIANTS
                },
                "top1_any_vendor": _top1(abstain_rows, _sim_pred("V_any_vendor"))["bill"],
                "top1_other_vendor": _top1(abstain_rows, _sim_pred("V_other_vendor"))["bill"],
            },
            "no_prior_bucket": no_prior,
            "no_earlier_same_vendor_pdf_bill": {
                "n": len(stratum_rows),
                "any_vendor": _top1(stratum_rows, lambda r: r.similar["V_any_vendor"]),
                "other_vendor": _top1(stratum_rows, lambda r: r.similar["V_other_vendor"]),
            },
        }
        if arm.reidentification:
            reid_out[name] = reidentification_rate(
                arm.reidentification, [r.enriched.bill.bill_id for r in rows]
            )

    paired: dict[str, Any] = {}
    if ARM_W in arms:
        w_rows = eval_by_arm[ARM_W]
        w_sel = arms[ARM_W].selection
        for other in [a for a in arms if a != ARM_W]:
            o_rows = eval_by_arm[other]
            o_sel = arms[other].selection
            pairs: dict[str, Any] = {
                v: paired_compare(w_rows, o_rows, _sim_pred(v), _sim_pred(v))
                for v in u691.VARIANTS
            }
            pairs["combined"] = paired_compare(
                w_rows,
                o_rows,
                lambda r: u691.combined_prediction(r, best_variant=w_sel.best_variant),
                lambda r, s=o_sel: u691.combined_prediction(r, best_variant=s.best_variant),
            )
            pairs["combined_mode_b"] = paired_compare(
                w_rows,
                o_rows,
                lambda r: u691.combined_prediction_mode_b(
                    r, best_variant=w_sel.best_variant_mode_b
                ),
                lambda r, s=o_sel: u691.combined_prediction_mode_b(
                    r, best_variant=s.best_variant_mode_b
                ),
            )
            paired[f"W_vs_{other}"] = pairs

    sample: Optional[dict[str, Any]] = None
    if bill_ids_500 is not None:
        sample = sample_500_block(
            arms,
            bill_ids_500,
            source=bill_ids_500_source,
            enriched=enriched,
            token_config=token_config,
            cal_since=cal_since,
            cal_until=cal_until,
            eval_since=eval_since,
            eval_until=eval_until,
        )

    return {
        "model_version": MODEL_VERSION,
        "windows": {
            "cal_since": cal_since.isoformat(),
            "cal_until": cal_until.isoformat(),
            "eval_since": eval_since.isoformat(),
            "eval_until": eval_until.isoformat(),
        },
        "population": {
            "all_coded_bills": population.all_coded_bills,
            "with_usable_pdf_text": population.with_usable_pdf_text,
            "eval_pdf_population": population.eval_pdf_population,
            "cal_pdf_population": population.cal_pdf_population,
            "delta_vs_u694": {
                "pdf_bills": population.with_usable_pdf_text - U694_BASELINE["pdf_bills"],
                "eval_pdf_population": population.eval_pdf_population
                - U694_BASELINE["eval_pdf_population"],
                "cal_pdf_population": population.cal_pdf_population
                - U694_BASELINE["cal_pdf_population"],
            },
            "no_earlier_same_vendor_pdf_bill_eval": sum(
                1
                for r in eval_by_arm[next(iter(arms))]
                if r.enriched.bill.bill_id in stratum_c
            ),
        },
        "u694_baseline": U694_BASELINE,
        "head_to_head": main,
        "calibration_selection": calibration,
        "combined_lift": combined,
        "cold_start": cold,
        "vendor_reidentification": reid_out,
        "paired": paired,
        "sample_500": sample,
        "embedding_spend": spend.to_json() if spend is not None else None,
        "runtime_seconds": dict(runtimes),
        "extra": dict(extra),
    }


def assert_parity(extra: Mapping[str, Any]) -> None:
    """Stop the run (before any output is written) unless the arms are comparable."""
    problems = []
    mism = extra.get("word_generic_parity_mismatches")
    if mism is None:
        problems.append("word parity was not computed")
    elif mism > 0:
        problems.append(
            f"{mism} word-arm predictions differ between the generic scorer and "
            "u691.score_all_rolling"
        )
    if extra.get("missing_vectors", 0) > 0:
        problems.append(f"{extra['missing_vectors']} PDF-population bills have no vector")
    if problems:
        raise ParityError("U-697 parity check failed: " + "; ".join(problems))


def _pct(v: Any) -> str:
    if v is None:
        return "n/a"
    return f"{float(v) * 100:.1f}"


def render_comparison_md(comp: Mapping[str, Any], stamp: str) -> str:
    arms = list(comp["head_to_head"])
    lines = [
        f"# U-697 vector vs word similar-bills comparison ({stamp})",
        "",
        f"Harness `{comp['model_version']}`. Windows: cal {comp['windows']['cal_since']}"
        f"..{comp['windows']['cal_until']}, eval {comp['windows']['eval_since']}"
        f"..{comp['windows']['eval_until']}. Top-1 over all evaluated bills (abstain = wrong), "
        "percent.",
        "",
        "## Population",
        "",
        "| item | value | delta_vs_u694 |",
        "|---|---|---|",
    ]
    pop = comp["population"]
    delta = pop["delta_vs_u694"]
    lines += [
        f"| pdf_bills | {pop['with_usable_pdf_text']} | {delta['pdf_bills']} |",
        f"| eval | {pop['eval_pdf_population']} | {delta['eval_pdf_population']} |",
        f"| cal | {pop['cal_pdf_population']} | {delta['cal_pdf_population']} |",
        f"| stratum_c_eval | {pop['no_earlier_same_vendor_pdf_bill_eval']} | |",
        "",
        "## Head to head (bill / dollar)",
        "",
        "| method | " + " | ".join(arms) + " |",
        "|---|" + "---|" * len(arms),
    ]
    keys = list(u691.VARIANTS) + ["combined", "combined_mode_b", "history_a", "history_b"]
    for key in keys:
        cells = []
        for a in arms:
            b = comp["head_to_head"][a][key]
            cells.append(f"{_pct(b['bill'])} / {_pct(b['dollar'])}")
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    lines += ["", "## Calibration selection", "", "| arm | mode_a | mode_b |", "|---|---|---|"]
    for a in arms:
        c = comp["calibration_selection"][a]
        lines.append(f"| {a} | {c['best_variant']} | {c['best_variant_mode_b']} |")
    lines += [
        "",
        "## Combined lift over history (points)",
        "",
        "| arm | combined_a | lift_a | combined_b | lift_b |",
        "|---|---|---|---|---|",
    ]
    for a in arms:
        c = comp["combined_lift"][a]
        lines.append(
            f"| {a} | {_pct(c['combined_a'])} | {_pct(c['lift_a'])} | "
            f"{_pct(c['combined_b'])} | {_pct(c['lift_b'])} |"
        )
    lines += [
        "",
        "## Cold start",
        "",
        "| arm | abstain_n | abstain_any | abstain_other | noprior_n | noprior_any "
        "| stratum_c_n | stratum_c_any | stratum_c_other |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for a in arms:
        c = comp["cold_start"][a]
        np_any = c["no_prior_bucket"].get("V_any_vendor") or {}
        st = c["no_earlier_same_vendor_pdf_bill"]
        lines.append(
            f"| {a} | {c['history_abstain']['n']} | "
            f"{_pct(c['history_abstain']['top1_any_vendor'])} | "
            f"{_pct(c['history_abstain']['top1_other_vendor'])} | "
            f"{np_any.get('n', 0)} | {_pct(np_any.get('bill'))} | {st['n']} | "
            f"{_pct(st['any_vendor']['bill'])} | {_pct(st['other_vendor']['bill'])} |"
        )
    if comp["vendor_reidentification"]:
        lines += [
            "",
            "## Vendor reidentification (any-vendor top-K)",
            "",
            "| arm | neighbour_share | mean_per_bill | neighbours |",
            "|---|---|---|---|",
        ]
        for a, r in comp["vendor_reidentification"].items():
            lines.append(
                f"| {a} | {_pct(r['neighbour_share'])} | {_pct(r['mean_per_bill'])} | "
                f"{r['neighbours']} |"
            )
    for pair, block in comp["paired"].items():
        lines += [
            "",
            f"## Paired {pair}",
            "",
            "| method | fixed | broke | p |",
            "|---|---|---|---|",
        ]
        for key, p in block.items():
            pv = "n/a" if p["sign_test_p"] is None else f"{p['sign_test_p']:.4f}"
            lines.append(f"| {key} | {p['fixed']} | {p['broke']} | {pv} |")
    sample = comp.get("sample_500")
    if sample:
        lines += [
            "",
            "## U-701b sample (history if strong, else vote)",
            "",
            f"Ids in file: {sample['ids_in_file']}; evaluated (eval window, PDF population): "
            f"{sample['evaluated']}.",
            "",
            "| dropped | n |",
            "|---|---|",
            *[f"| {k} | {v} |" for k, v in sample["dropped"].items()],
            "",
            "| arm | mode_a | mode_b |",
            "|---|---|---|",
        ]
        for a, s in sample["arms"].items():
            lines.append(
                f"| {a} | {_pct(s['history_strong_else_vote_a'])} | "
                f"{_pct(s['history_strong_else_vote_b'])} |"
            )
    spend = comp.get("embedding_spend")
    if spend:
        lines += [
            "",
            "## Embedding spend and truncation",
            "",
            "| item | value |",
            "|---|---|",
        ]
        for k in (
            "deployment",
            "dimensions",
            "requests",
            "prompt_tokens",
            "usd",
            "cap",
            "cache_hits",
            "embedded",
            "truncated",
            "retruncated",
            "unconfirmed_attempts",
            "unconfirmed_tokens",
        ):
            lines.append(f"| {k} | {spend[k]} |")
    extra = comp.get("extra") or {}
    if extra:
        lines += ["", "## Checks and settings", "", "| item | value |", "|---|---|"]
        for k, v in extra.items():
            if isinstance(v, Mapping):
                for kk, vv in v.items():
                    lines.append(f"| {k}_c{kk} | {_pct(vv)} |")
            else:
                lines.append(f"| {k} | {v} |")
    lines += ["", "## Runtime (seconds)", "", "| step | seconds |", "|---|---|"]
    for k, v in comp["runtime_seconds"].items():
        lines.append(f"| {k} | {v:.1f} |")
    return "\n".join(lines) + "\n"


def write_comparison(out_dir: Path, stamp: str, comp: Mapping[str, Any]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"u697_comparison_{stamp}.md"
    js = out_dir / f"u697_comparison_{stamp}.json"
    js.write_text(
        json.dumps(comp, indent=2, sort_keys=True, default=_json_default), encoding="utf-8"
    )
    md.write_text(render_comparison_md(comp, stamp), encoding="utf-8")
    assert_outputs_privacy_safe(js)
    assert_outputs_privacy_safe(md)
    return md, js


# =============================================================================================
# Orchestration
# =============================================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="U-697 read-only vector vs word similar-bills backtest."
    )
    ap.add_argument("--run", action="store_true", help="Embed, score and write outputs")
    ap.add_argument("--cal-since", type=date.fromisoformat, default=DEFAULT_CAL_SINCE)
    ap.add_argument("--cal-until", type=date.fromisoformat, default=DEFAULT_CAL_UNTIL)
    ap.add_argument("--eval-since", type=date.fromisoformat, default=DEFAULT_EVAL_SINCE)
    ap.add_argument("--eval-until", type=date.fromisoformat, default=DEFAULT_EVAL_UNTIL)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--top-k", type=int, default=u691.DEFAULT_TOP_K)
    ap.add_argument(
        "--amount-band", type=float, default=u691.DEFAULT_AMOUNT_BAND_PCT, dest="amount_band_pct"
    )
    ap.add_argument(
        "--amount-cap-ratio", type=float, default=u691.DEFAULT_AMOUNT_BAND_CAP_RATIO
    )
    ap.add_argument("--pdf-read-workers", type=int, default=u691.DEFAULT_PDF_READ_WORKERS)
    ap.add_argument(
        "--pdf-read-timeout", type=float, default=u691.DEFAULT_PDF_READ_TIMEOUT_SECONDS
    )
    ap.add_argument("--deployment", default=DEFAULT_EMBEDDING_DEPLOYMENT)
    ap.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument(
        "--embedding-url-style", choices=URL_STYLES, default=URL_STYLE_DEPLOYMENTS
    )
    ap.add_argument("--api-version", default=None)
    ap.add_argument(
        "--dimensions", type=int, default=None, help="Omit for the full 1536 dimensions"
    )
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    ap.add_argument(
        "--max-cost-usd", type=lambda s: Decimal(s), default=DEFAULT_MAX_COST_USD
    )
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--concurrency", type=int, choices=(1, 2), default=1)
    ap.add_argument(
        "--vector-prime",
        action="store_true",
        help="Also score V' (shifted cosine weight, c0 picked on calibration)",
    )
    ap.add_argument("--no-hybrid", action="store_true", help="Skip the H (RRF) arm")
    ap.add_argument("--bill-ids-from", type=Path, default=DEFAULT_BILL_IDS_FROM)
    ap.add_argument(
        "--no-bill-ids-from", action="store_true", help="Skip the U-701b sample table"
    )
    return ap


def _make_real_embedder(args: argparse.Namespace) -> FoundryEmbedder:  # pragma: no cover
    import config

    settings = config.Settings()
    return FoundryEmbedder(
        endpoint=settings.foundry_endpoint or "",
        api_key=settings.foundry_api_key or "",
        deployment=args.deployment,
        model=args.model,
        url_style=args.embedding_url_style,
        api_version=args.api_version,
        dimensions=args.dimensions,
        max_chars=args.max_chars,
        max_cost_usd=args.max_cost_usd,
    )


def run_backtest(
    args: argparse.Namespace,
    *,
    load_population: Callable[[], Population],
    embedder_factory: Callable[[], FoundryEmbedder],
    stamp: Optional[str] = None,
    log: Callable[[str], None] = lambda m: print(m, flush=True),
) -> int:
    validate_windows(args.cal_since, args.cal_until, args.eval_since, args.eval_until)
    t_all = time.perf_counter()
    runtimes: dict[str, float] = {}

    t0 = time.perf_counter()
    pop = load_population()
    runtimes["load"] = time.perf_counter() - t0
    token_config = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_PDF, pdf_tokens_by_bill_id=pop.pdf_tokens_by_bill_id
    )
    population = u691.compute_population_counts(
        pop.enriched,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        token_config=token_config,
    )
    texts = {
        bid: text
        for bid, text in pop.pdf_text_by_bill_id.items()
        if bid in token_config.usable_pdf_bill_ids
    }
    key = CacheKey(
        args.model, args.deployment, args.dimensions, truncation_policy_version(args.max_chars)
    )
    cache = EmbeddingCache(args.cache_dir)
    plan = plan_embeddings(texts, cache, key, max_chars=args.max_chars)
    log(
        f"U-697: {population.all_coded_bills} coded bills; PDF population "
        f"{population.with_usable_pdf_text} (U-694: {U694_BASELINE['pdf_bills']}); eval "
        f"{population.eval_pdf_population} (U-694: {U694_BASELINE['eval_pdf_population']}); "
        f"cal {population.cal_pdf_population} (U-694: {U694_BASELINE['cal_pdf_population']})"
    )
    log(
        f"U-697: embedding cache hits {len(plan.hits)}, misses {len(plan.misses)}; "
        f"estimated tokens {plan.estimated_miss_tokens} (chars/{CHARS_PER_TOKEN_ESTIMATE}); "
        f"projected ${float(plan.projected_usd):.4f} (cap ${float(args.max_cost_usd):.2f})"
    )
    if not args.run:
        log("U-697: dry-run only (zero embedding calls). Pass --run to embed and score.")
        return 0
    if plan.projected_usd > Decimal(str(args.max_cost_usd)):
        raise SpendCapExceeded(
            f"U-697: projected ${float(plan.projected_usd):.4f} exceeds cap "
            f"${float(args.max_cost_usd):.2f}; aborting before any embedding call"
        )

    embedder = embedder_factory()
    t0 = time.perf_counter()

    def _progress(done: int, total: int) -> None:
        if done == total or done % max(1, total // 20) == 0 or done == 1:
            log(
                f"U-697: embedded {done}/{total} "
                f"(spent ${float(embedder.spend.usd):.4f})"
            )

    try:
        vectors = embed_population(
            texts,
            embedder,
            cache,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            progress=_progress,
        )
    finally:
        embedder.close()
        log(f"U-697: embedding spend {json.dumps(embedder.spend.to_json(), sort_keys=True)}")
    del texts  # raw text no longer needed past this point
    runtimes["embed"] = time.perf_counter() - t0

    # --- W arm: the U-694 path, unchanged ------------------------------------------------
    t0 = time.perf_counter()
    idf = u691.compute_idf_from_corpus(
        pop.enriched, since=args.cal_since, until=args.cal_until, config=token_config
    )
    w_selection = u691.pick_variants_on_calibration(
        pop.enriched,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        idf_for_selection=idf,
        top_k=args.top_k,
        band_pct=args.amount_band_pct,
        cap_ratio=args.amount_cap_ratio,
        token_config=token_config,
    )
    w_scored = u691.score_all_rolling(
        pop.enriched,
        idf,
        top_k=args.top_k,
        band_pct=args.amount_band_pct,
        cap_ratio=args.amount_cap_ratio,
        token_config=token_config,
    )
    runtimes["word_arm"] = time.perf_counter() - t0

    # --- V / V' / H: one rolling pass -------------------------------------------------------
    grid = V_PRIME_C0_GRID if args.vector_prime else ()
    vp = score_vector_rolling(
        pop.enriched,
        vectors=vectors,
        history_source=w_scored,
        token_config=token_config,
        idf=idf,
        top_k=args.top_k,
        band_pct=args.amount_band_pct,
        cap_ratio=args.amount_cap_ratio,
        v_prime_grid=grid,
        include_word=True,
        include_hybrid=not args.no_hybrid,
    )
    runtimes["vector_pass"] = vp.runtime_seconds

    arms: dict[str, ArmResult] = {}
    w_reid = vp.reidentification[ARM_W_GENERIC]
    arms[ARM_W] = ArmResult(ARM_W, w_scored, w_selection, w_reid)

    def _sel(rows: Sequence[u691.FullyScoredBill]) -> u691.CalibrationSelection:
        return select_variants_from_scored(
            rows, cal_since=args.cal_since, cal_until=args.cal_until, token_config=token_config
        )

    arms[ARM_V] = ArmResult(
        ARM_V, vp.arms[ARM_V], _sel(vp.arms[ARM_V]), vp.reidentification[ARM_V]
    )
    extra: dict[str, Any] = {"missing_vectors": vp.missing_vectors, "rrf_k": RRF_K,
                             "rrf_depth": RRF_DEPTH}
    if grid:
        sels = {c0: _sel(vp.arms[f"{ARM_V_PRIME}_c{c0:g}"]) for c0 in grid}
        c0 = pick_v_prime_c0(sels)
        arm_key = f"{ARM_V_PRIME}_c{c0:g}"
        arms[ARM_V_PRIME] = ArmResult(
            ARM_V_PRIME, vp.arms[arm_key], sels[c0], vp.reidentification[arm_key]
        )
        extra["v_prime_c0"] = c0
        extra["v_prime_grid_cal_bill"] = {
            f"{g:g}": sels[g].calibration_metrics.get(sels[g].best_variant, {}).get("bill")
            for g in grid
        }
    if not args.no_hybrid:
        arms[ARM_H] = ArmResult(
            ARM_H, vp.arms[ARM_H], _sel(vp.arms[ARM_H]), vp.reidentification[ARM_H]
        )
    # Parity: the generic word scorer must reproduce u691's W predictions exactly.
    w_by_id = {r.enriched.bill.bill_id: r for r in w_scored}
    mismatches = 0
    for r in vp.arms[ARM_W_GENERIC]:
        w = w_by_id[r.enriched.bill.bill_id]
        for v in u691.VARIANTS:
            if r.similar[v].predicted_scc_id != w.similar[v].predicted_scc_id:
                mismatches += 1
    extra["word_generic_parity_mismatches"] = mismatches
    assert_parity(extra)  # stops the run before ANY output is written

    bill_ids_500: Optional[list[int]] = None
    src: Optional[str] = None
    if not args.no_bill_ids_from and args.bill_ids_from is not None:
        if Path(args.bill_ids_from).exists():
            bill_ids_500 = load_bill_ids_from_csv(args.bill_ids_from)
            src = Path(args.bill_ids_from).name
        else:
            log("U-697 WARNING: --bill-ids-from file not found; sample table skipped")

    stamp = stamp or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    runtimes["total"] = time.perf_counter() - t_all

    constants = {
        "top_k": args.top_k,
        "amount_band_pct": args.amount_band_pct,
        "amount_cap_ratio": args.amount_cap_ratio,
        "history_strong_raw_score": u691.HISTORY_STRONG_RAW_SCORE,
        "min_prior_bills": MIN_PRIOR_BILLS,
        "text_source": u691.TEXT_SOURCE_PDF,
    }
    for name, arm in arms.items():
        eval_rows = u691.eval_window_rows(
            arm.scored,
            eval_since=args.eval_since,
            eval_until=args.eval_until,
            token_config=token_config,
        )
        summary = u691.build_summary(
            arm.scored,
            cal_since=args.cal_since,
            cal_until=args.cal_until,
            eval_since=args.eval_since,
            eval_until=args.eval_until,
            best_variant=arm.selection.best_variant,
            best_variant_mode_b=arm.selection.best_variant_mode_b,
            calibration_selection=arm.selection,
            runtime_seconds=(
                runtimes["word_arm"] if name == ARM_W else runtimes["vector_pass"]
            ),
            bill_count=len(pop.enriched),
            constants={**constants, "u697_arm": name},
            token_config=token_config,
            population=population,
            pdf_load_stats=pop.load_stats,
        )
        summary["u697_arm"] = name
        summary["u697_model_version"] = MODEL_VERSION
        if name != ARM_W:
            summary["embedding_spend"] = embedder.spend.to_json()
        arm_paths = u691.write_outputs(
            out_dir=args.out_dir,
            stamp=f"{stamp}_u697_{name}",
            scored=arm.scored,
            summary=summary,
            token_config=token_config,
            eval_rows=eval_rows,
        )
        for path in arm_paths:  # our own guard on every arm output, not only u691's
            assert_outputs_privacy_safe(path)

    comp = build_comparison(
        arms,
        enriched=pop.enriched,
        token_config=token_config,
        population=population,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        bill_ids_500=bill_ids_500,
        bill_ids_500_source=src,
        spend=embedder.spend,
        runtimes=runtimes,
        extra=extra,
    )
    md, js = write_comparison(args.out_dir, stamp, comp)
    log(f"U-697: wrote {md.name} and {js.name}; runtime {runtimes['total']:.1f}s")
    return 0


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI entry
    args = build_arg_parser().parse_args(argv)
    return run_backtest(
        args,
        load_population=lambda: load_population_from_db(
            max_workers=args.pdf_read_workers, timeout_seconds=args.pdf_read_timeout
        ),
        embedder_factory=lambda: _make_real_embedder(args),
    )


if __name__ == "__main__":  # pragma: no cover
    assert_cli_system_admin()
    raise SystemExit(main())
