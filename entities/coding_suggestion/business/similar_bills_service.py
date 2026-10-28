"""Live similar-past-bills cache and matcher (U-701a).

Incremental hash reads use a ModifiedDatetime watermark with a 5-minute overlap so
late commits still land in the next refresh. A re-hash that commits more than ~5 minutes
after the refresh that should have picked it up is deferred until the next full hash
re-read (every 6th scheduled refresh, about 3 hours at the default 30-minute TTL).
"""
from __future__ import annotations

import logging
import threading
import time
from array import array as array_type
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional

from entities.attachment.persistence.text_token_hash_repo import AttachmentTextTokenHashRepository
from entities.coding_suggestion.business.bill_text_token_hash import (
    bill_text_token_hash_key,
    current_tokenizer_version,
    feature_enabled,
    token_hashes_from_storage_int,
    token_hashes_from_text_int,
    warn_once_if_disabled,
)
from entities.coding_suggestion.business.similar_bills import (
    DEFAULT_AMOUNT_BAND_CAP_RATIO,
    DEFAULT_AMOUNT_BAND_PCT,
    DEFAULT_NEIGHBOUR_DISPLAY_K,
    DEFAULT_TOP_K,
    BillTokenProfile,
    SimilarBillsMatchResult,
    SimilarNeighbourResult,
    VisibilityFilter,
    _log_abs_amount,
    cap_tokens_by_idf,
    compute_idf_from_doc_token_sets,
    prior_bill_visible,
    top_neighbours_any_vendor,
    vote_from_scored_neighbours,
)
from shared.api.money import to_decimal_or_none
from shared.db_datetime import parse_db_datetime

logger = logging.getLogger(__name__)


def log_similar_bills_shadow_none(
    reason: str,
    *,
    attachment_id: Optional[int] = None,
    **fields: object,
) -> None:
    parts = [f"similar_bills.shadow_none reason={reason}"]
    if attachment_id is not None:
        parts.append(f"attachment_id={attachment_id}")
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


_REFRESH_BACKOFF_SECONDS = 30.0
_HASH_READ_OVERLAP = timedelta(minutes=5)
_FULL_HASH_REREAD_EVERY_N = 6
_EPOCH = datetime(2000, 1, 1)  # naive UTC floor: "read every hash row"


def _parse_project_ids_csv(raw: object) -> tuple[int, ...]:
    if raw is None or not str(raw).strip():
        return ()
    return tuple(sorted(int(p) for p in str(raw).split(",") if p.strip()))


def _compact_token_storage(tokens: frozenset[int]) -> array_type:
    # Unsigned: HMAC truncations are 64-bit and may exceed signed int64 max.
    return array_type("Q", sorted(tokens))


def _tokens_frozenset(compact: array_type) -> frozenset[int]:
    return frozenset(compact)


@dataclass(frozen=True)
class _CorpusBill:
    bill_id: int
    attachment_id: int
    vendor_id: int
    created: datetime
    label_scc_id: int
    total_abs_amount: Decimal
    created_by_user_id: int = 0
    project_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class _EnrichedStub:
    bill: _CorpusBill


@dataclass(frozen=True)
class _CorpusSnapshot:
    idf: dict[int, float]
    profiles_by_bill: dict[int, BillTokenProfile]
    token_index: dict[int, tuple[int, ...]]
    creator_by_bill: dict[int, int]
    projects_by_bill: dict[int, tuple[int, ...]]


class SimilarBillsService:
    _instances: dict[int, "SimilarBillsService"] = {}
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        company_id: int = 1,
        token_repo: Optional[AttachmentTextTokenHashRepository] = None,
        settings: Optional[object] = None,
    ) -> None:
        import config

        self._company_id = int(company_id)
        self._settings = settings or config.Settings()
        self._token_repo = token_repo or AttachmentTextTokenHashRepository()
        self._state_lock = threading.RLock()
        self._flight_lock = threading.Lock()
        self._refresh_running = False
        self._snapshot: Optional[_CorpusSnapshot] = None
        self._hash_by_attachment: dict[int, array_type] = {}
        self._skipped_malformed_attachment_ids: set[int] = set()
        self._since_watermark: datetime = _EPOCH
        self._last_refresh_mono: Optional[float] = None
        self._last_refresh_attempt_mono: Optional[float] = None
        self._refresh_count: int = 0

    @classmethod
    def for_company(cls, company_id: int, **kwargs: object) -> "SimilarBillsService":
        cid = int(company_id)
        with cls._instance_lock:
            existing = cls._instances.get(cid)
            if existing is None:
                existing = cls(company_id=cid, **kwargs)  # type: ignore[arg-type]
                cls._instances[cid] = existing
            return existing

    @classmethod
    def shared(cls) -> "SimilarBillsService":
        # Deprecated: process-global alias for company 1; prefer for_company(tenant id).
        return cls.for_company(1)

    @classmethod
    def _reset_shared_for_tests(cls) -> None:
        with cls._instance_lock:
            cls._instances.clear()

    def _cache_ttl_seconds(self) -> float:
        ttl = getattr(self._settings, "similar_bills_refresh_seconds", 1800)
        try:
            return float(max(1, int(ttl)))
        except (TypeError, ValueError):
            return 1800.0

    def _max_tokens_per_bill(self) -> Optional[int]:
        cap = getattr(self._settings, "similar_bills_max_tokens_per_bill", 50)
        if cap is None:
            return None
        try:
            n = int(cap)
        except (TypeError, ValueError):
            return 50
        return None if n <= 0 else n

    def _shadow_budget_ms(self) -> int:
        budget = getattr(self._settings, "similar_bills_shadow_budget_ms", 2000)
        try:
            return max(0, int(budget))
        except (TypeError, ValueError):
            return 2000

    @property
    def cache_warm(self) -> bool:
        with self._state_lock:
            return self._snapshot is not None and bool(self._snapshot.profiles_by_bill)

    @property
    def snapshot_ready(self) -> bool:
        """True once a refresh has completed (including warm-but-empty corpus)."""
        with self._state_lock:
            return self._snapshot is not None

    def _schedule_refresh_if_needed(self, *, force: bool = False) -> None:
        if not feature_enabled(self._settings):
            warn_once_if_disabled()
            return
        now = time.monotonic()
        with self._state_lock:
            if (
                not force
                and self._last_refresh_mono is not None
                and (now - self._last_refresh_mono) < self._cache_ttl_seconds()
            ):
                return
            if self._last_refresh_attempt_mono is not None and (
                now - self._last_refresh_attempt_mono
            ) < _REFRESH_BACKOFF_SECONDS:
                return
        self._kick_background_refresh()

    def _kick_background_refresh(self) -> None:
        with self._flight_lock:
            if self._refresh_running:
                return
            self._refresh_running = True
        threading.Thread(target=self._refresh_worker, name="similar-bills-refresh", daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            try:
                self._refresh_incremental_sync()
            except Exception as error:
                logger.warning("similar_bills.refresh_worker_failed err=%s", type(error).__name__)
        finally:
            with self._flight_lock:
                self._refresh_running = False

    def refresh_incremental(self) -> None:
        """Synchronous refresh (tests); production uses background refresh."""
        self._refresh_incremental_sync()

    def _apply_hash_rows(
        self,
        hash_rows: list,
        *,
        version: str,
    ) -> datetime:
        """Merge hash rows into hash_by_attachment; return max ModifiedDatetime seen.

        AttachmentId is the table's key, so each read returns at most one row per attachment."""
        max_modified = self._since_watermark
        skipped_hash_rows = 0
        for row in hash_rows:
            if str(row.TokenizerVersion) != version:
                continue
            att_id = int(row.AttachmentId)
            try:
                tokens = token_hashes_from_storage_int(str(row.TokenHashes))
                # OverflowError: a negative or >64-bit hex value parses but does not fit
                # the unsigned compact array - malformed like any other bad row.
                compact = _compact_token_storage(tokens)
            except (ValueError, OverflowError):
                skipped_hash_rows += 1
                self._skipped_malformed_attachment_ids.add(att_id)
                continue
            self._hash_by_attachment[att_id] = compact
            mod = parse_db_datetime(row.ModifiedDatetime) or datetime.min
            if mod >= max_modified:
                max_modified = mod
        if skipped_hash_rows:
            logger.warning(
                "similar_bills.skipped_malformed_hash_rows count=%s",
                skipped_hash_rows,
            )
        return max_modified

    def _fill_missing_corpus_hashes(self, corpus_rows: list, *, version: str) -> None:
        missing = {
            int(row.AttachmentId)
            for row in corpus_rows
            if int(row.AttachmentId) not in self._hash_by_attachment
            and int(row.AttachmentId) not in self._skipped_malformed_attachment_ids
        }
        if not missing:
            return
        try:
            extra = self._token_repo.read_for_attachment_ids(
                sorted(missing),
                tokenizer_version=version,
            )
        except Exception as error:
            logger.warning(
                "similar_bills.missing_hash_read_failed err=%s", type(error).__name__
            )
            return
        self._apply_hash_rows(extra, version=version)

    def _refresh_incremental_sync(self) -> None:
        if not feature_enabled(self._settings):
            warn_once_if_disabled()
            return
        version = current_tokenizer_version(self._settings)
        if not version:
            return
        self._last_refresh_attempt_mono = time.monotonic()
        self._refresh_count += 1
        full_hash_reread = self._refresh_count % _FULL_HASH_REREAD_EVERY_N == 0
        try:
            corpus_rows = list(
                self._token_repo.read_corpus_rows(
                    tokenizer_version=version,
                    company_id=self._company_id,
                )
            )
            since = self._since_watermark - _HASH_READ_OVERLAP
            if full_hash_reread or since < _EPOCH:
                since = _EPOCH
            hash_rows = list(self._token_repo.read_since(since))
        except Exception as error:
            logger.warning("similar_bills.refresh_failed err=%s", type(error).__name__)
            return

        try:
            max_modified = self._apply_hash_rows(hash_rows, version=version)
            self._fill_missing_corpus_hashes(corpus_rows, version=version)
            # Hash rows for attachments outside the corpus (drafts, non-bill mail) are dropped so the map
            # stays corpus-sized; the gap fill re-reads one once its bill completes.
            corpus_attachment_ids = {int(row.AttachmentId) for row in corpus_rows}
            self._hash_by_attachment = {
                att_id: toks
                for att_id, toks in self._hash_by_attachment.items()
                if att_id in corpus_attachment_ids
            }

            max_cap = self._max_tokens_per_bill()
            raw_profiles: dict[int, tuple[_CorpusBill, array_type]] = {}
            skipped_corpus_rows = 0
            for row in corpus_rows:
                att_id = int(row.AttachmentId)
                compact = self._hash_by_attachment.get(att_id)
                if not compact:
                    continue
                try:
                    bill = _CorpusBill(
                        bill_id=int(row.BillId),
                        attachment_id=att_id,
                        vendor_id=int(row.VendorId),
                        created=parse_db_datetime(row.CreatedDatetime) or datetime.min,
                        label_scc_id=int(row.DominantSubCostCodeId),
                        total_abs_amount=to_decimal_or_none(row.TotalAbsAmount) or Decimal("0"),
                        created_by_user_id=int(getattr(row, "CreatedByUserId", 0) or 0),
                        project_ids=_parse_project_ids_csv(getattr(row, "ProjectIdsCsv", None)),
                    )
                except (TypeError, ValueError, ArithmeticError):
                    skipped_corpus_rows += 1
                    continue
                raw_profiles[bill.bill_id] = (bill, compact)

            if skipped_corpus_rows:
                logger.warning(
                    "similar_bills.skipped_malformed_corpus_rows count=%s",
                    skipped_corpus_rows,
                )

            doc_tokens = [_tokens_frozenset(toks) for _bill, toks in raw_profiles.values()]
            idf = compute_idf_from_doc_token_sets(doc_tokens)

            profiles: dict[int, BillTokenProfile] = {}
            creator_by_bill: dict[int, int] = {}
            projects_by_bill: dict[int, tuple[int, ...]] = {}
            token_index_builder: dict[int, list[int]] = {}
            for bid, (bill, compact) in raw_profiles.items():
                tokens = _tokens_frozenset(compact)
                capped = cap_tokens_by_idf(tokens, idf, max_cap)
                profile = self._profile_for_bill(bill, capped, idf_weights=idf)
                profiles[bid] = profile
                creator_by_bill[bid] = bill.created_by_user_id
                projects_by_bill[bid] = bill.project_ids
                for tok in capped:
                    token_index_builder.setdefault(tok, []).append(bid)

            frozen_index = {
                tok: tuple(sorted(bids)) for tok, bids in token_index_builder.items()
            }
            snapshot = _CorpusSnapshot(
                idf=idf,
                profiles_by_bill=profiles,
                token_index=frozen_index,
                creator_by_bill=creator_by_bill,
                projects_by_bill=projects_by_bill,
            )

            with self._state_lock:
                self._snapshot = snapshot
                if max_modified >= self._since_watermark:
                    self._since_watermark = max_modified
                self._last_refresh_mono = time.monotonic()
        except Exception as error:
            logger.warning("similar_bills.snapshot_build_failed err=%s", type(error).__name__)

    def _profile_for_bill(
        self,
        bill: _CorpusBill,
        tokens: frozenset[int],
        *,
        idf_weights: dict[int, float],
    ) -> BillTokenProfile:
        return BillTokenProfile(
            enriched=_EnrichedStub(bill=bill),
            tokens=tokens,
            idf_weight_sum=sum(idf_weights.get(t, 0.0) for t in tokens),
            log_abs_amount=_log_abs_amount(bill.total_abs_amount),
        )

    def similar_bills_for(
        self,
        *,
        bill_text_hashes: frozenset[int],
        amount: Decimal,
        vendor_id: Optional[int] = None,
        exclude_bill_id: Optional[int] = None,
        as_of: datetime,
        visible: Optional[VisibilityFilter] = None,
    ) -> Optional[SimilarBillsMatchResult]:
        if not feature_enabled(self._settings):
            warn_once_if_disabled()
            return None
        self._schedule_refresh_if_needed()
        with self._state_lock:
            snapshot = self._snapshot
        if snapshot is None or not snapshot.profiles_by_bill:
            return None

        max_cap = self._max_tokens_per_bill()
        query_tokens = cap_tokens_by_idf(bill_text_hashes, snapshot.idf, max_cap)

        amount_abs = abs(amount)
        query_bill = _CorpusBill(
            bill_id=exclude_bill_id or -1,
            attachment_id=0,
            vendor_id=int(vendor_id) if vendor_id is not None else -1,
            created=as_of,
            label_scc_id=0,
            total_abs_amount=amount_abs,
        )
        query_prof = BillTokenProfile(
            enriched=_EnrichedStub(bill=query_bill),
            tokens=query_tokens,
            idf_weight_sum=sum(snapshot.idf.get(t, 0.0) for t in query_tokens),
            log_abs_amount=_log_abs_amount(amount_abs),
        )

        prior_filter: Optional[Callable[[object], bool]] = None
        if visible is not None:
            creator_by = snapshot.creator_by_bill
            projects_by = snapshot.projects_by_bill
            vf = visible

            def prior_filter(prior_bill: object, _vf: VisibilityFilter = vf) -> bool:
                bid = int(prior_bill.bill_id)
                return prior_bill_visible(
                    created_by_user_id=creator_by.get(bid, 0),
                    project_ids=projects_by.get(bid, ()),
                    visible=_vf,
                )

        neighbours_all = top_neighbours_any_vendor(
            query_prof,
            snapshot.idf,
            top_k=DEFAULT_TOP_K,
            band_pct=DEFAULT_AMOUNT_BAND_PCT,
            cap_ratio=DEFAULT_AMOUNT_BAND_CAP_RATIO,
            token_index=snapshot.token_index,
            prior_by_id=snapshot.profiles_by_bill,
            prior_filter=prior_filter,
        )
        vote = vote_from_scored_neighbours(
            [
                (n.combined, n.prior.enriched.bill.bill_id, n.scc_id)
                for n in neighbours_all
            ],
            DEFAULT_TOP_K,
        )
        neighbours = neighbours_all[:DEFAULT_NEIGHBOUR_DISPLAY_K]

        display = tuple(
            SimilarNeighbourResult(
                scc_id=n.scc_id,
                match=n.match,
                amount_ratio=n.amount_ratio,
                same_vendor=n.same_vendor,
                combined=n.combined,
            )
            for n in neighbours
        )
        return SimilarBillsMatchResult(
            neighbours=display,
            predicted_scc_id=vote.predicted_scc_id,
            confidence=vote.confidence,
        )

    def shadow_predict_with_budget(
        self,
        *,
        extraction_content: str,
        amount: Decimal,
        vendor_id: Optional[int] = None,
        as_of: datetime,
        attachment_id: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[Decimal]]:
        """Bounded shadow call for intake logging; never raises."""
        if not feature_enabled(self._settings):
            logger.debug("similar_bills.shadow_none reason=disabled attachment_id=%s", attachment_id)
            return None, None
        start = time.monotonic()
        budget_ms = self._shadow_budget_ms()
        try:
            key = bill_text_token_hash_key(self._settings)
            with self._state_lock:
                snapshot = self._snapshot
            if snapshot is None:
                # Cold: start the first refresh, but never hash the PDF for a query that cannot match.
                self._schedule_refresh_if_needed()
                log_similar_bills_shadow_none("cold", attachment_id=attachment_id)
                return None, None
            if not snapshot.profiles_by_bill:
                log_similar_bills_shadow_none("empty_corpus", attachment_id=attachment_id)
                return None, None
            tokens = token_hashes_from_text_int(extraction_content, key)
            result = self.similar_bills_for(
                bill_text_hashes=tokens,
                amount=abs(amount),
                vendor_id=vendor_id,
                exclude_bill_id=None,
                as_of=as_of,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms > budget_ms:
                log_similar_bills_shadow_none(
                    "over_budget",
                    attachment_id=attachment_id,
                    elapsed_ms=elapsed_ms,
                    budget_ms=budget_ms,
                )
                return None, None
            if result is None or result.predicted_scc_id is None:
                log_similar_bills_shadow_none("no_neighbours", attachment_id=attachment_id)
                return None, None
            corpus_bills = len(snapshot.profiles_by_bill)
            confidence = result.confidence
            conf_str = f"{float(confidence):.2f}" if confidence is not None else "0.00"
            ok_parts = [
                f"similar_bills.shadow_ok scc={result.predicted_scc_id}",
                f"confidence={conf_str}",
                f"elapsed_ms={elapsed_ms}",
                f"corpus_bills={corpus_bills}",
            ]
            if attachment_id is not None:
                ok_parts.append(f"attachment_id={attachment_id}")
            logger.info(" ".join(ok_parts))
            return result.predicted_scc_id, result.confidence
        except Exception as error:
            logger.warning("similar_bills.shadow_failed err=%s", type(error).__name__)
            return None, None
