"""Background bill text-tier coding suggestion tick (U-654b)."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Literal, Optional, Sequence

import config
from entities.attachment.business.extraction_service import extraction_payload_is_current
from entities.coding_suggestion.business.bill_coding_decision import (
    BillCodingCatalog,
    BillCodingDecision,
    BillCodingDeps,
    BillCodingInputs,
    RungAnswer,
    NO_LLM_LAST_RUNG_SOURCES,
    SimilarScope,
    _default_run_cascade,
    decide_bill_coding,
    neighbour_lookup_possible,
    rung_columns,
)
from entities.coding_suggestion.business.bill_suggestion_service import (
    _HISTORY_SOURCES,
    HISTORY_STRONG_RAW_SCORE,
    BillCodingSuggester,
    BillSuggestion,
)
from entities.coding_suggestion.business.bill_text_token_hash import (
    bill_text_token_hash_key,
    current_tokenizer_version,
    token_hashes_from_text_int,
)
from entities.coding_suggestion.business.decision_model.serve import (
    LABEL_COUNTS,
    M0_READ_BOUND_SECONDS,
    M0Priors,
)
from entities.coding_suggestion.business.similar_bills import bill_text_from_extraction_blob
from entities.coding_suggestion.business.similar_bills_service import SimilarBillsService
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
    BillCodingActor,
    BillTextCodingClaim,
    BillTextCodingSuggestionRepository,
)
from entities.coding_suggestion.persistence.decision_model_priors_repo import (
    DecisionModelPriorsRepository,
)
from intelligence.cascade import claude_budget
from intelligence.cascade.core import CascadeResult, attempt_was_skipped
from intelligence.observability.cascade_telemetry import cascade_usage_totals
from intelligence.cascade.bill_coding import (
    SUSPENSE_SCC_NUMBER,
    normalize_project_abbreviation,
    normalize_scc_number,
    run_bill_coding_suggestion,
    truncate_bill_text,
)
from intelligence.cascade.bill_coding_payload import (
    build_candidate_projects_for_bill,
    build_candidate_sccs_for_bill,
    history_evidence_from_scc_counts,
    narrow_candidate_sccs,
    resolve_text_prediction,
    vendor_history_scc_numbers_from_scc_counts,
)
from shared.access import accessible_project_ids_for_user
from shared.storage import AzureBlobStorage
from shared.text_blob_read import read_text_blob_with_retry

logger = logging.getLogger(__name__)

MODEL_VERSION = "u654b-text-v1"
MODEL_VERSION_V2 = "u701b-text-v2"


VENDOR_WIDE_SCC_HISTORY_TOP = 1000

_CATALOG_CACHE_TTL_SECONDS = 60.0
_catalog_lock = threading.Lock()
_catalog_cache: dict[datetime, tuple[float, CatalogSnapshot]] = {}
_catalog_now_fn: Callable[[], float] = time.monotonic
_catalog_loader_override: Optional[Callable[[datetime], CatalogSnapshot]] = None


def configure_catalog_cache_for_tests(
    *,
    now_fn: Optional[Callable[[], float]] = None,
    loader: Optional[Callable[[datetime], CatalogSnapshot]] = None,
) -> None:
    global _catalog_cache, _catalog_now_fn, _catalog_loader_override
    with _catalog_lock:
        _catalog_cache = {}
    _catalog_loader_override = loader
    if now_fn is not None:
        _catalog_now_fn = now_fn
    elif loader is None:
        _catalog_now_fn = time.monotonic


def _catalog_cache_key(as_of: datetime) -> datetime:
    return as_of.replace(second=0, microsecond=0)


def foundry_is_configured() -> bool:
    settings = config.Settings()
    return bool(
        (settings.foundry_api_key or "").strip()
        and (settings.foundry_endpoint or "").strip()
    )


@dataclass(frozen=True)
class CatalogSnapshot:
    scc_by_id: dict[int, Any]
    scc_number_to_id: dict[str, int]
    ambiguous_scc_numbers: frozenset[str]
    proj_abbrev_to_id: dict[str, int]
    ambiguous_project_abbrevs: frozenset[str]
    candidate_projects: list[dict[str, str]]
    vendor_name: str


def _unique_string_keys(rows: list[tuple[str, int]]) -> tuple[dict[str, int], frozenset[str]]:
    by_key: dict[str, list[int]] = {}
    for key, row_id in rows:
        by_key.setdefault(key, []).append(row_id)
    ambiguous = frozenset(k for k, ids in by_key.items() if len(ids) > 1)
    unique = {k: ids[0] for k, ids in by_key.items() if len(ids) == 1}
    return unique, ambiguous


def history_eligible_for_text(
    *,
    vendor_id: int,
    chosen_project_id: Optional[int],
    as_of: datetime,
    exclude_bill_id: int,
    suggester: Optional[BillCodingSuggester] = None,
    precomputed_suggestion: Optional[Any] = None,
) -> tuple[bool, Optional[str]]:
    """Return (eligible, skip_reason). eligible=True means run the cascade."""
    if precomputed_suggestion is not None:
        suggestion = precomputed_suggestion
    else:
        engine = suggester or BillCodingSuggester()
        suggestion = engine.suggest(
            vendor_id=vendor_id,
            project_id=chosen_project_id,
            memo=None,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        )
    if suggestion.source not in _HISTORY_SOURCES:
        return True, None
    if chosen_project_id is None:
        return True, None
    raw = suggestion.raw_score
    if raw is not None and raw < HISTORY_STRONG_RAW_SCORE:
        return True, None
    return False, "history_strong"


def _catalog_as_of(claim: BillTextCodingClaim) -> datetime:
    """Backtest parity (U-654a): as-of is Bill.CreatedDatetime, not BillDate."""
    return claim.created_datetime


def decode_bill_text_from_blob_bytes(content_bytes: bytes) -> str:
    """Match u654a read_blob: whole download as utf-8; truncate in payload build."""
    return content_bytes.decode("utf-8", errors="replace")


def blob_has_current_redaction(blob_text: str) -> bool:
    """True only for an extraction blob stamped by the current TIN-masking writer.

    The whole blob goes to Foundry, so a blob written before U-535g (no stamp, or an
    older one), one carrying keys the current writer never writes (a stamped legacy
    blob still holding pages / paragraphs / tables), or anything that is not the
    extraction JSON envelope fails closed.
    """
    try:
        envelope = json.loads(blob_text)
    except ValueError:
        return False
    return extraction_payload_is_current(envelope)


@dataclass(frozen=True)
class RuntimePayloadBuild:
    payload: dict[str, Any]
    mode: str
    candidate_scc_narrowed: bool


def bill_coding_candidate_sccs_mode(settings: config.Settings) -> str:
    raw = (settings.bill_coding_candidate_sccs or "").strip().lower()
    if raw == "narrowed":
        return "narrowed"
    if raw not in ("", "full"):
        claude_budget._warn_invalid_setting_once(
            "bill_coding_candidate_sccs",
            settings.bill_coding_candidate_sccs,
            "full",
        )
    return "full"


def load_narrowing_history_scc_numbers(
    settings: config.Settings,
    repository: BillTextCodingSuggestionRepository,
    *,
    vendor_id: int,
    as_of: datetime,
    exclude_bill_id: Optional[int],
) -> Optional[list[str]]:
    """Vendor-wide history for narrowing; None → use full catalog (off mode or failure)."""
    if bill_coding_candidate_sccs_mode(settings) != "narrowed":
        return None
    try:
        wide_rows = repository.read_vendor_scc_counts(
            vendor_id,
            project_id=None,
            as_of=as_of,
            top=VENDOR_WIDE_SCC_HISTORY_TOP,
            exclude_bill_id=exclude_bill_id,
        )
        return vendor_history_scc_numbers_from_scc_counts(wide_rows)
    except Exception as error:
        logger.warning(
            "bill_text_coding_narrowing_history_failed vendor_id=%s err=%s",
            vendor_id,
            type(error).__name__,
        )
        return None


def _log_bill_text_coding_suggestion_candidates(
    *,
    bill_id: Optional[int],
    vendor_id: int,
    built: RuntimePayloadBuild,
) -> None:
    logger.info(
        "bill_text_coding_suggestion bill_id=%s vendor_id=%s mode=%s "
        "candidate_scc_count=%s candidate_scc_narrowed=%s",
        bill_id,
        vendor_id,
        built.mode,
        len(built.payload["candidate_sccs"]),
        built.candidate_scc_narrowed,
    )


def build_runtime_payload(
    *,
    bill_text: str,
    catalog: CatalogSnapshot,
    history_rows: Any,
    chosen_project_id: Optional[int],
    as_of: datetime,
    narrowing_history_scc_numbers: Optional[Sequence[str]] = None,
) -> RuntimePayloadBuild:
    mode = "A" if chosen_project_id is not None else "B"
    full_candidates = build_candidate_sccs_for_bill(
        catalog.scc_by_id,
        as_of=as_of,
        ambiguous_numbers=catalog.ambiguous_scc_numbers,
    )
    if narrowing_history_scc_numbers is not None:
        candidate_sccs, candidate_scc_narrowed = narrow_candidate_sccs(
            full_candidates, narrowing_history_scc_numbers
        )
    else:
        candidate_sccs = full_candidates
        candidate_scc_narrowed = False
    candidate_projects: Optional[list[dict[str, str]]]
    if mode == "A":
        candidate_projects = None
    else:
        candidate_projects = build_candidate_projects_for_bill(
            catalog.candidate_projects,
            ambiguous_abbrevs=catalog.ambiguous_project_abbrevs,
        )
    payload = {
        "vendor_name": catalog.vendor_name,
        "bill_text": truncate_bill_text(bill_text),
        "history_evidence": history_evidence_from_scc_counts(history_rows),
        "candidate_sccs": candidate_sccs,
        "candidate_projects": candidate_projects,
    }
    return RuntimePayloadBuild(
        payload=payload,
        mode=mode,
        candidate_scc_narrowed=candidate_scc_narrowed,
    )


STALE_REDACTION = "stale_redaction"


def _decision_complete_kwargs(
    decision: BillCodingDecision,
    *,
    primary_from_intake: bool = False,
) -> dict[str, Any]:
    """Per-rung CompleteBillTextCodingSuggestion params from a V2 decision."""
    cost_usd = None
    if decision.cascades:
        _i, _o, cost_usd = cascade_usage_totals(decision.cascades[0])
    return {
        "DecisionSource": decision.source,
        **rung_columns("Primary", decision.primary),
        **rung_columns("Escalate", decision.escalate),
        **rung_columns("Fallback", decision.fallback, ("Model", "SubCostCodeId", "Confidence")),
        "SimilarVoteSubCostCodeId": decision.similar_vote_scc_id,
        "SimilarVoteConfidence": decision.similar_vote_confidence,
        "SimilarNeighbourCount": decision.similar_neighbour_count,
        "M0SubCostCodeId": decision.m0_scc_id,
        "M0Confidence": decision.m0_confidence,
        "PrimaryFromIntake": 1 if primary_from_intake else None,
        "CostUsd": cost_usd,
    }


def _query_token_hashes(bill_text: str, settings: config.Settings) -> frozenset[int]:
    """Best-effort: no key (feature off) or any hashing error -> no hashes, no neighbours."""
    try:
        key = bill_text_token_hash_key(settings)
        if key is None:
            return frozenset()
        return token_hashes_from_text_int(bill_text, key)
    except Exception as error:
        logger.warning(
            "bill_coding_decision.token_hash_failed err=%s",
            type(error).__name__,
        )
        return frozenset()


def _similar_service_for(company_id: Optional[int], settings: config.Settings) -> Optional[Any]:
    if company_id is None:
        return None
    try:
        return SimilarBillsService.for_company(int(company_id), settings=settings)
    except Exception as error:
        logger.warning(
            "bill_coding_decision.similar_service_failed err=%s",
            type(error).__name__,
        )
        return None


# U-745: bound on the two M0 reads (serve.M0_READ_BOUND_SECONDS).
M0_READ_TIMEOUT_SECONDS = M0_READ_BOUND_SECONDS


def m0_rung_enabled(settings: Any) -> bool:
    return getattr(settings, "bill_coding_m0_rung_enabled", False) is True


async def _read_m0_priors(
    m0_repository: DecisionModelPriorsRepository,
    *,
    company_id: int,
    vendor_id: int,
    as_of: datetime,
    exclude_bill_id: Optional[int],
    settings: config.Settings,
) -> Optional[M0Priors]:
    """M0's DB inputs, best-effort: None on ANY error or timeout (a WARNING with the
    exception type only). The company-wide label counts come from the in-process ~1h
    cache. Started only when a neighbour lookup is possible (else M0 abstains anyway)."""
    try:
        version = current_tokenizer_version(settings)
        priors, counts = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(
                    m0_repository.read_vendor_prior_bills,
                    company_id=int(company_id),
                    vendor_id=int(vendor_id),
                    as_of=as_of,
                    exclude_bill_id=exclude_bill_id,
                    tokenizer_version=version,
                ),
                asyncio.to_thread(
                    LABEL_COUNTS.get, int(company_id), m0_repository.read_label_counts
                ),
            ),
            timeout=M0_READ_TIMEOUT_SECONDS,
        )
        return M0Priors(vendor_priors=tuple(priors), label_counts=counts)
    except Exception as error:
        logger.warning(
            "bill_coding_decision.m0_priors_failed err=%s",
            type(error).__name__,
        )
        return None


async def run_bill_coding_decision(
    *,
    stage: Literal["drop", "tick_full", "tick_escalate"],
    bill_text: str,
    vendor_id: int,
    vendor_name: str,
    chosen_project_id: Optional[int],
    amount: Optional[Decimal],
    as_of: datetime,
    exclude_bill_id: Optional[int],
    company_id: Optional[int],
    actor_user_id: Optional[int],
    accessible_project_ids: Optional[frozenset[int]],
    prior_primary: Optional[RungAnswer] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
    settings: Optional[config.Settings] = None,
    repository: Optional[BillTextCodingSuggestionRepository] = None,
    suggester: Optional[BillCodingSuggester] = None,
    catalog: Optional[CatalogSnapshot] = None,
    similar_service: Optional[Any] = None,
    history_suggestion: Optional[BillSuggestion] = None,
    fallback_on_daily_cap: bool = False,
    m0_repository: Optional[DecisionModelPriorsRepository] = None,
) -> BillCodingDecision:
    """V2 ladder decision for the drop and the tick (U-701b3): the ONE place that builds
    the catalog, history, candidate SCCs and the scoped similar-bills service.

    bill_text is the extraction CONTENT. accessible_project_ids None = all projects; an
    empty frozenset = no neighbours; it is also the access check on the model's Mode B
    project. amount None (no total read) = no neighbours. The similar-bills part is best-effort (no key, no
    company or any lookup error -> no neighbours, a warning). The independent DB reads
    (catalog when not passed, vendor SCC counts, narrowing history, history) run
    concurrently off the loop. history_suggestion: a precomputed engine.suggest for
    (vendor, chosen_project_id, as_of, exclude_bill_id) - pass it only when computed with
    the SAME exclude_bill_id.

    fallback_on_daily_cap (U-701c): the Foundry fallback rungs run when Claude's daily
    cap is hit. It has no effect on "tick_escalate", whose ladder has no fallback rungs.
    An unknown stage raises ValueError.

    U-745: M0's two reads (vendor priors + cached company label counts) start beside
    these reads as a task the decision uses only at choose_final (drop: never waits -
    only if already done; tick: <= 5 s; late = no M0 for this decision); ``bill_coding_m0_rung_enabled`` switches
    the last rung from the vote to M0.
    """
    run_cascade = None
    if fallback_on_daily_cap:
        if stage not in ("drop", "tick_full", "tick_escalate"):
            raise ValueError(f"unknown bill coding stage: {stage!r}")
        run_cascade = functools.partial(_default_run_cascade, fallback_on_daily_cap=True)
    settings = settings or config.Settings()
    repository = repository or BillTextCodingSuggestionRepository()
    engine = suggester or BillCodingSuggester()

    def _history(project_id: Optional[int]) -> Optional[BillSuggestion]:
        return engine.suggest(
            vendor_id=vendor_id,
            project_id=project_id,
            memo=None,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        )

    async def _given(value: Any) -> Any:
        return value

    query_token_hashes = _query_token_hashes(bill_text, settings)

    # M0's reads start now but are NOT awaited here: the decision consults them only at
    # choose_final (after the cascade; the drop never waits), so a slow or cold read never
    # delays the model call. No neighbour lookup possible -> M0 abstains: no task.
    m0_priors_task: Optional[asyncio.Future] = None
    if neighbour_lookup_possible(
        query_token_hashes=query_token_hashes,
        amount=amount,
        company_id=company_id,
        accessible_project_ids=accessible_project_ids,
    ):
        m0_priors_task = asyncio.ensure_future(
            _read_m0_priors(
                m0_repository or DecisionModelPriorsRepository(),
                company_id=int(company_id),
                vendor_id=vendor_id,
                as_of=as_of,
                exclude_bill_id=exclude_bill_id,
                settings=settings,
            )
        )
    catalog, history_rows, narrowing_numbers, history_suggestion = await asyncio.gather(
        _given(catalog)
        if catalog is not None
        else asyncio.to_thread(load_catalog_from_db, as_of),
        asyncio.to_thread(
            repository.read_vendor_scc_counts,
            vendor_id,
            project_id=chosen_project_id,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        ),
        asyncio.to_thread(
            load_narrowing_history_scc_numbers,
            settings,
            repository,
            vendor_id=vendor_id,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        ),
        _given(history_suggestion)
        if history_suggestion is not None
        else asyncio.to_thread(_history, chosen_project_id),
    )
    # The FULL as-of catalog (holding codes already excluded by the loader), built once:
    # neighbours filter against it, and the payload's (possibly narrowed) list derives
    # from it.
    as_of_catalog = build_candidate_sccs_for_bill(
        catalog.scc_by_id,
        as_of=as_of,
        ambiguous_numbers=catalog.ambiguous_scc_numbers,
    )
    if narrowing_numbers is not None:
        candidate_sccs, _narrowed = narrow_candidate_sccs(as_of_catalog, narrowing_numbers)
    else:
        candidate_sccs = list(as_of_catalog)

    if similar_service is None:
        similar_service = _similar_service_for(company_id, settings)

    inputs = BillCodingInputs(
        stage=stage,
        vendor_id=vendor_id,
        vendor_name=vendor_name or catalog.vendor_name,
        chosen_project_id=chosen_project_id,
        bill_text=bill_text,
        query_token_hashes=query_token_hashes,
        amount=abs(amount) if amount is not None else None,
        as_of=as_of,
        exclude_bill_id=exclude_bill_id,
        scope=SimilarScope(
            company_id=company_id,
            actor_user_id=actor_user_id,
            accessible_project_ids=accessible_project_ids,
        ),
        prior_primary=prior_primary,
    )
    deps = BillCodingDeps(
        catalog=BillCodingCatalog(
            scc_by_id=catalog.scc_by_id,
            scc_number_to_id=catalog.scc_number_to_id,
            ambiguous_scc_numbers=catalog.ambiguous_scc_numbers,
            proj_abbrev_to_id=catalog.proj_abbrev_to_id,
            ambiguous_project_abbrevs=catalog.ambiguous_project_abbrevs,
            candidate_projects=catalog.candidate_projects,
            as_of_catalog=as_of_catalog,
        ),
        history_rows=history_rows,
        history_suggestion=history_suggestion,
        candidate_sccs=candidate_sccs,
        similar_service=similar_service,
        history_for_project=_history,
        run_cascade=run_cascade,
        transport_for=transport_for,
        m0_priors=m0_priors_task,
        m0_rung_enabled=m0_rung_enabled(settings),
    )
    return await decide_bill_coding(inputs, deps)


def _intake_prior_primary(intake: Any) -> RungAnswer:
    """The intake's Haiku answer (Primary*, falling back to the decided Pred*/Coding*)."""

    def first(primary_value: Any, decided_value: Any) -> Any:
        return primary_value if primary_value is not None else decided_value

    return RungAnswer(
        model=first(intake.primary_model, intake.coding_model),
        sub_cost_code_id=first(
            intake.primary_sub_cost_code_id, intake.pred_sub_cost_code_id
        ),
        project_id=first(intake.primary_project_id, intake.pred_project_id),
        confidence=first(intake.primary_confidence, intake.coding_confidence),
        error=intake.primary_error,
    )


def _intake_reuse_kwargs(intake: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "DecisionSource": "reused_intake",
        "PrimaryFromIntake": 1,
        "PrimaryModel": intake.primary_model,
        "PrimarySubCostCodeId": intake.primary_sub_cost_code_id,
        "PrimaryProjectId": intake.primary_project_id,
        "PrimaryConfidence": intake.primary_confidence,
        "PrimaryError": intake.primary_error,
    }
    # Only a V2-written intake row holds the FILTERED, creator-scoped vote; a legacy
    # row's similar columns are the raw, unscoped U-701a shadow - never surface those.
    if intake.coding_source is not None:
        kwargs.update(
            SimilarVoteSubCostCodeId=intake.similar_pred_sub_cost_code_id,
            SimilarVoteConfidence=intake.similar_confidence,
            SimilarNeighbourCount=intake.similar_neighbour_count,
            M0SubCostCodeId=intake.m0_sub_cost_code_id,
            M0Confidence=intake.m0_confidence,
        )
    return kwargs


def _creator_scope(
    actor: Optional[BillCodingActor],
) -> tuple[Optional[int], Optional[frozenset[int]]]:
    """(actor_user_id, accessible projects) of the bill's creator. No creator -> fail
    closed (no actor, empty set = no neighbours); never the system user's scope and
    never resolve_accessible_project_ids() (that is "all" in system context)."""
    if actor is None or actor.created_by_user_id is None:
        return None, frozenset()
    return actor.created_by_user_id, accessible_project_ids_for_user(
        actor.created_by_user_id,
        is_system_admin=actor.is_system_admin,
    )


async def _process_one_claim_v2(
    claim: BillTextCodingClaim,
    *,
    settings: config.Settings,
    repository: BillTextCodingSuggestionRepository,
    storage: AzureBlobStorage,
    suggester: Optional[BillCodingSuggester],
    catalog_loader: Optional[Callable[[datetime], CatalogSnapshot]],
    transport_for: Optional[Callable[[str], Any]],
    foundry_on_cap: bool = False,
) -> dict[str, Any]:
    from entities.bill_intake_prediction.business.service import (
        BillIntakePredictionService,
        tick_decision_for_intake,
    )

    model_version = MODEL_VERSION_V2
    cascade_ran = False
    as_of = _catalog_as_of(claim)

    def _finalize_complete(*, record_call: bool = False, **kwargs: Any) -> bool:
        row = repository.complete(
            bill_id=claim.bill_id,
            attachment_id=claim.attachment_id,
            extracted_datetime=claim.extracted_datetime,
            model_version=model_version,
            record_call=record_call,
            **kwargs,
        )
        return row is not None

    def _refused() -> dict[str, Any]:
        return _tick_result(
            status="failed",
            bill_id=claim.bill_id,
            failure_reason="complete_refused",
        )

    def _finish_skipped(reason: Optional[str]) -> dict[str, Any]:
        if not _finalize_complete(status="skipped", failure_reason=reason):
            return _refused()
        return _tick_result(status="skipped", bill_id=claim.bill_id, skipped_reason=reason)

    def _read_intake() -> Any:
        try:
            return BillIntakePredictionService().read_by_attachment_id(claim.attachment_id)
        except Exception as error:
            logger.warning(
                "bill_text_coding_tick intake_prediction_lookup_failed bill_id=%s err=%s",
                claim.bill_id,
                type(error).__name__,
            )
            return None

    def _route(intake_row: Any) -> str:
        if intake_row is None:
            return "full"
        return tick_decision_for_intake(
            intake_row,
            extracted_datetime=claim.extracted_datetime,
            chosen_project_id=claim.chosen_project_id,
            vendor_id=claim.vendor_id,
            ladder_v2_enabled=True,
        )

    try:
        intake = None
        tick_action: Optional[str] = None
        if foundry_on_cap:
            # U-701c: route first, so a claim held for the Claude reset spends nothing
            # (no history, blob, actor or catalog read). The Sonnet-only escalation
            # waits for the reset; a drop already coded by Foundry / the vote is reused,
            # never paid for twice.
            intake = _read_intake()
            tick_action = _route(intake)
            if await claude_budget.is_claude_day_stopped_async():
                if tick_action == "escalate":
                    return _tick_result(status="capped", bill_id=claim.bill_id)
                if tick_action == "full" and _drop_coded_without_claude(intake, claim):
                    tick_action = "reuse"

        engine = suggester or BillCodingSuggester()
        # Computed once: the eligibility check and the ladder use the same history
        # (same vendor / project / as_of / exclude_bill_id).
        tick_history = engine.suggest(
            vendor_id=claim.vendor_id,
            project_id=claim.chosen_project_id,
            memo=None,
            as_of=as_of,
            exclude_bill_id=claim.bill_id,
        )
        eligible, skip_reason = history_eligible_for_text(
            vendor_id=claim.vendor_id,
            chosen_project_id=claim.chosen_project_id,
            as_of=as_of,
            exclude_bill_id=claim.bill_id,
            suggester=engine,
            precomputed_suggestion=tick_history,
        )
        if not eligible:
            return _finish_skipped(skip_reason)

        if tick_action is None:
            intake = _read_intake()
            tick_action = _route(intake)

        mode = "A" if claim.chosen_project_id is not None else "B"
        if tick_action == "reuse":
            if not _finalize_complete(
                status="done",
                sub_cost_code_id=intake.pred_sub_cost_code_id,
                project_id=intake.pred_project_id if mode == "B" else None,
                confidence=intake.coding_confidence,
                mode=mode,
                winning_model=intake.coding_model,
                record_call=False,
                **_intake_reuse_kwargs(intake),
            ):
                return _refused()
            return _tick_result(status="done", bill_id=claim.bill_id)

        def _read_blob(url: str) -> str:
            content_bytes, _meta = storage.download_file(url)
            return content_bytes.decode("utf-8", errors="replace")

        try:
            blob_text = read_text_blob_with_retry(_read_blob, claim.extracted_text_blob_url)
        except Exception as error:
            _finalize_complete(status="failed", failure_reason="blob_read")
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="blob_read",
                error=type(error).__name__,
            )

        if not blob_has_current_redaction(blob_text):
            return _finish_skipped(STALE_REDACTION)
        bill_text = bill_text_from_extraction_blob(blob_text) or ""
        if not bill_text.strip():
            return _finish_skipped(STALE_REDACTION)

        try:
            actor: Optional[BillCodingActor] = repository.read_bill_coding_actor(claim.bill_id)
        except Exception as error:
            # A transient read error must not finish the row with an empty scope (that
            # would permanently drop the Mode B project): leave it pending, nothing paid
            # or finalized, so a later tick re-claims it. A MISSING creator (None) still
            # fails closed below.
            logger.warning(
                "bill_text_coding_tick actor_read_failed bill_id=%s err=%s",
                claim.bill_id,
                type(error).__name__,
            )
            return _tick_result(
                status="deferred",
                bill_id=claim.bill_id,
                failure_reason="actor_read",
                error=type(error).__name__,
            )
        actor_user_id, accessible = _creator_scope(actor)
        amount = actor.total_amount if actor is not None else None
        catalog = (catalog_loader or _load_catalog_from_db)(as_of)

        stage = "tick_full"
        prior_primary: Optional[RungAnswer] = None
        if tick_action == "escalate":
            stage = "tick_escalate"
            prior_primary = _intake_prior_primary(intake)

        cascade_ran = True
        decision = await run_bill_coding_decision(
            stage=stage,
            bill_text=bill_text,
            vendor_id=claim.vendor_id,
            vendor_name=_read_vendor_name(claim.vendor_id) or catalog.vendor_name,
            chosen_project_id=claim.chosen_project_id,
            amount=amount,
            as_of=as_of,
            exclude_bill_id=claim.bill_id,
            company_id=actor.company_id if actor else None,
            actor_user_id=actor_user_id,
            accessible_project_ids=accessible,
            prior_primary=prior_primary,
            transport_for=transport_for,
            settings=settings,
            repository=repository,
            suggester=engine,
            catalog=catalog,
            history_suggestion=tick_history,
            fallback_on_daily_cap=foundry_on_cap,
        )
        made_call = bool(decision.cascades)
        cascade_ran = made_call

        if decision.stopped_reason == "daily_cap":
            # A paid Haiku answer from this tick is kept (owner rule); otherwise the
            # row stays pending and is re-claimed after the cap resets.
            keep_haiku = (
                stage == "tick_full"
                and decision.primary is not None
                and decision.primary.sub_cost_code_id is not None
                and decision.source != "none"
            )
            # U-701c: a tick_full ladder whose Foundry fallback ran under the cap is
            # finalized like any other decision (fallback / similar_vote / none).
            foundry_decided = foundry_on_cap and _fallback_rung_ran(decision)
            if not keep_haiku and not foundry_decided:
                return _tick_result(status="capped", bill_id=claim.bill_id)

        complete_kwargs = _decision_complete_kwargs(
            decision,
            primary_from_intake=stage == "tick_escalate",
        )
        if decision.source == "none":
            _finalize_complete(
                status="failed",
                failure_reason="not_accepted",
                record_call=made_call,
                **complete_kwargs,
            )
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="not_accepted",
            )

        if not _finalize_complete(
            status="done",
            sub_cost_code_id=decision.sub_cost_code_id,
            project_id=decision.project_id if decision.mode == "B" else None,
            confidence=decision.confidence,
            mode=decision.mode,
            winning_model=decision.model,
            record_call=made_call,
            **complete_kwargs,
        ):
            return _refused()
        return _tick_result(status="done", bill_id=claim.bill_id)
    except Exception as error:
        logger.warning(
            "bill_text_coding_tick failed bill_id=%s err=%s",
            claim.bill_id,
            type(error).__name__,
        )
        try:
            _finalize_complete(status="failed", failure_reason="error", record_call=cascade_ran)
        except Exception:
            pass
        return _tick_result(
            status="failed",
            bill_id=claim.bill_id,
            error=type(error).__name__,
            failure_reason="error",
        )


def _fallback_rung_ran(decision: BillCodingDecision) -> bool:
    """True when a fallback_only (Foundry) rung actually ran (was not skipped)."""
    return any(
        attempt.rung.fallback_only and not attempt_was_skipped(attempt)
        for cascade in decision.cascades or ()
        for attempt in cascade.attempts
    )


def _drop_coded_without_claude(intake: Any, claim: BillTextCodingClaim) -> bool:
    """A V2 drop row that Claude could not code but Foundry / the vote did (U-701c)."""
    from entities.bill_intake_prediction.business.service import _intake_reuse_checks_pass

    return (
        intake is not None
        and intake.model_version == MODEL_VERSION_V2
        and intake.coding_escalation == "claude_unavailable"
        and intake.coding_source in ({"fallback"} | NO_LLM_LAST_RUNG_SOURCES)
        and _intake_reuse_checks_pass(
            intake,
            extracted_datetime=claim.extracted_datetime,
            chosen_project_id=claim.chosen_project_id,
            vendor_id=claim.vendor_id,
        )
    )


def _foundry_on_cap_kwargs(foundry_on_cap: bool) -> dict[str, Any]:
    """Passed only when on, so flag-off calls are exactly today's."""
    return {"foundry_on_cap": True} if foundry_on_cap else {}


def _tick_result(**fields: Any) -> dict[str, Any]:
    base = {
        "status": "idle",
        "bill_id": None,
        "error": None,
        "failure_reason": None,
        "skipped_reason": None,
    }
    base.update(fields)
    return base


async def _process_one_claim(
    claim: BillTextCodingClaim,
    *,
    settings: config.Settings,
    repository: BillTextCodingSuggestionRepository,
    storage: AzureBlobStorage,
    suggester: Optional[BillCodingSuggester],
    catalog_loader: Optional[Callable[[datetime], CatalogSnapshot]],
    transport_for: Optional[Callable[[str], Any]],
    foundry_on_cap: bool = False,
) -> dict[str, Any]:
    if settings.bill_coding_ladder_v2_enabled is True:
        return await _process_one_claim_v2(
            claim,
            settings=settings,
            repository=repository,
            storage=storage,
            suggester=suggester,
            catalog_loader=catalog_loader,
            transport_for=transport_for,
            **_foundry_on_cap_kwargs(foundry_on_cap),
        )
    def _finalize_complete(*, record_call: bool = False, **kwargs: Any) -> bool:
        row = repository.complete(
            bill_id=claim.bill_id,
            attachment_id=claim.attachment_id,
            extracted_datetime=claim.extracted_datetime,
            model_version=MODEL_VERSION,
            record_call=record_call,
            **kwargs,
        )
        return row is not None

    def _finish_skipped(reason: Optional[str]) -> dict[str, Any]:
        if not _finalize_complete(status="skipped", failure_reason=reason):
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="complete_refused",
            )
        return _tick_result(status="skipped", bill_id=claim.bill_id, skipped_reason=reason)

    # Set once the model has been called, so every exit after that counts toward the cap.
    cascade_ran = False
    as_of = _catalog_as_of(claim)
    try:
        eligible, skip_reason = history_eligible_for_text(
            vendor_id=claim.vendor_id,
            chosen_project_id=claim.chosen_project_id,
            as_of=as_of,
            exclude_bill_id=claim.bill_id,
            suggester=suggester,
        )
        if not eligible:
            return _finish_skipped(skip_reason)

        from entities.bill_intake_prediction.business.service import (
            BillIntakePredictionService,
            tick_can_reuse_prediction,
        )

        intake_prediction = None
        try:
            intake_prediction = BillIntakePredictionService().read_by_attachment_id(
                claim.attachment_id
            )
        except Exception as error:
            logger.warning(
                "bill_text_coding_tick intake_prediction_lookup_failed bill_id=%s err=%s",
                claim.bill_id,
                type(error).__name__,
            )
        tick_mode = "A" if claim.chosen_project_id is not None else "B"
        if intake_prediction is not None and tick_can_reuse_prediction(
            intake_prediction,
            extracted_datetime=claim.extracted_datetime,
            chosen_project_id=claim.chosen_project_id,
            vendor_id=claim.vendor_id,
        ):
            mode = tick_mode
            out_project = (
                intake_prediction.pred_project_id if mode == "B" else None
            )
            if not _finalize_complete(
                status="done",
                sub_cost_code_id=intake_prediction.pred_sub_cost_code_id,
                project_id=out_project,
                confidence=intake_prediction.coding_confidence,
                mode=mode,
                winning_model=intake_prediction.coding_model,
                record_call=False,
            ):
                return _tick_result(
                    status="failed",
                    bill_id=claim.bill_id,
                    failure_reason="complete_refused",
                )
            return _tick_result(status="done", bill_id=claim.bill_id)

        def _read_blob(url: str) -> str:
            content_bytes, _meta = storage.download_file(url)
            return decode_bill_text_from_blob_bytes(content_bytes)

        try:
            bill_text = read_text_blob_with_retry(_read_blob, claim.extracted_text_blob_url)
        except Exception as error:
            logger.warning(
                "bill_text_coding_tick blob read failed bill_id=%s err=%s",
                claim.bill_id,
                type(error).__name__,
            )
            _finalize_complete(status="failed", failure_reason="blob_read")
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="blob_read",
                error=type(error).__name__,
            )

        if not blob_has_current_redaction(bill_text):
            # Never send text the current masking has not been applied to. A U-535h
            # re-mask keeps the same ExtractedDatetime, so these rows are not re-claimed
            # on their own; delete the row to retry once the blob is re-masked.
            return _finish_skipped(STALE_REDACTION)

        catalog = (catalog_loader or _load_catalog_from_db)(as_of)
        catalog = replace(
            catalog, vendor_name=_read_vendor_name(claim.vendor_id) or catalog.vendor_name
        )
        history_rows = repository.read_vendor_scc_counts(
            claim.vendor_id,
            project_id=claim.chosen_project_id,
            as_of=as_of,
            exclude_bill_id=claim.bill_id,
        )
        narrowing_numbers = load_narrowing_history_scc_numbers(
            settings,
            repository,
            vendor_id=claim.vendor_id,
            as_of=as_of,
            exclude_bill_id=claim.bill_id,
        )
        built = build_runtime_payload(
            bill_text=bill_text,
            catalog=catalog,
            history_rows=history_rows,
            chosen_project_id=claim.chosen_project_id,
            as_of=as_of,
            narrowing_history_scc_numbers=narrowing_numbers,
        )
        _log_bill_text_coding_suggestion_candidates(
            bill_id=claim.bill_id,
            vendor_id=claim.vendor_id,
            built=built,
        )
        payload = built.payload
        mode = built.mode

        cascade_ran = True
        cascade = await run_bill_coding_suggestion(payload, transport_for=transport_for)
        if cascade.stopped_reason == "daily_cap":
            return _tick_result(status="capped", bill_id=claim.bill_id)
        if not cascade.accepted or cascade.result is None:
            _finalize_complete(
                status="failed",
                failure_reason="not_accepted",
                record_call=True,
            )
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="not_accepted",
            )

        scc_id, proj_id = resolve_text_prediction(
            cascade,
            scc_number_to_id=catalog.scc_number_to_id,
            proj_abbrev_to_id=catalog.proj_abbrev_to_id,
        )
        if scc_id is None:
            _finalize_complete(
                status="failed",
                failure_reason="unresolved_scc",
                record_call=True,
            )
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="unresolved_scc",
            )

        # The cascade's coerced confidence (a float clamped to [0, 1]), never the raw
        # model value — that can be a bool or out of range and overflow DECIMAL(5,4).
        confidence = (
            Decimal(str(cascade.confidence)) if cascade.confidence is not None else None
        )
        winning_model = (
            cascade.winning_rung.model if cascade.winning_rung else None
        )
        out_project = proj_id if mode == "B" else None

        if not _finalize_complete(
            status="done",
            sub_cost_code_id=scc_id,
            project_id=out_project,
            confidence=confidence,
            mode=mode,
            winning_model=winning_model,
            record_call=True,
        ):
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="complete_refused",
            )
        return _tick_result(status="done", bill_id=claim.bill_id)
    except Exception as error:
        logger.warning(
            "bill_text_coding_tick failed bill_id=%s err=%s",
            claim.bill_id,
            type(error).__name__,
        )
        try:
            _finalize_complete(status="failed", failure_reason="error", record_call=cascade_ran)
        except Exception:
            logger.warning(
                "bill_text_coding_tick complete-after-error failed bill_id=%s",
                claim.bill_id,
            )
        return _tick_result(
            status="failed",
            bill_id=claim.bill_id,
            error=type(error).__name__,
            failure_reason="error",
        )


async def run_bill_text_coding_tick(
    *,
    limit: int = 1,
    repo: Optional[BillTextCodingSuggestionRepository] = None,
    storage: Optional[AzureBlobStorage] = None,
    suggester: Optional[BillCodingSuggester] = None,
    catalog_loader: Optional[Callable[[datetime], CatalogSnapshot]] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
) -> dict[str, Any]:
    settings = config.Settings()
    if not settings.bill_coding_text_enabled or not foundry_is_configured():
        return _tick_result(status="disabled")

    repository = repo or BillTextCodingSuggestionRepository()
    cap = int(settings.bill_coding_text_daily_call_cap)
    if cap <= 0:
        return _tick_result(status="cap_reached")

    # U-701c: with the opt-in (ladder V2 only; Foundry is configured or the tick is
    # disabled above) the Claude day cap no longer stops the tick before claiming -
    # tick_full falls back to Foundry; tick_escalate is left pending inside.
    foundry_on_cap = (
        getattr(settings, "bill_coding_foundry_on_claude_cap_enabled", False) is True
        and settings.bill_coding_ladder_v2_enabled is True
    )
    blob_store = storage
    last = _tick_result(status="idle")
    for _ in range(max(1, int(limit))):
        if repository.count_calls_today() >= cap:
            return _tick_result(status="cap_reached", bill_id=last.get("bill_id"))
        if not foundry_on_cap and await claude_budget.is_claude_day_stopped_async():
            return _tick_result(status="capped", bill_id=last.get("bill_id"))
        claim = repository.claim_next()
        if claim is None:
            return last
        blob_store = blob_store or AzureBlobStorage()
        last = await _process_one_claim(
            claim,
            settings=settings,
            repository=repository,
            storage=blob_store,
            suggester=suggester,
            catalog_loader=catalog_loader,
            transport_for=transport_for,
            **_foundry_on_cap_kwargs(foundry_on_cap),
        )
        # A capped claim stays pending; stop so this tick does not re-claim it.
        if foundry_on_cap and last.get("status") == "capped":
            return last
    return last


def _read_vendor_name(vendor_id: int) -> str:
    from shared.database import get_connection

    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT [Name] FROM dbo.[Vendor] WHERE [Id] = ?",
                vendor_id,
            )
            row = cur.fetchone()
            return str(row.Name) if row and row.Name else ""
        finally:
            conn.rollback()


def _load_catalog_from_db(as_of: datetime) -> CatalogSnapshot:
    from shared.database import get_connection

    suspense_norm = normalize_scc_number(SUSPENSE_SCC_NUMBER)
    scc_by_id: dict[int, Any] = {}
    scc_key_rows: list[tuple[str, int]] = []
    proj_key_rows: list[tuple[str, int]] = []
    candidate_projects: list[dict[str, str]] = []
    vendor_name = ""

    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT [Id], [Number], [Name], [CreatedDatetime]
                FROM dbo.[SubCostCode]
                WHERE [Number] IS NOT NULL
                  AND ISNULL([IsHoldingCode], 0) = 0
                ORDER BY [Number]
                """
            )
            for row in cur.fetchall():
                number = normalize_scc_number(str(row.Number))
                ref = SimpleNamespace(
                    scc_id=int(row.Id),
                    number=number,
                    name=str(row.Name or ""),
                    created=row.CreatedDatetime,
                )
                scc_by_id[ref.scc_id] = ref
                if number and number != suspense_norm:
                    scc_key_rows.append((number, ref.scc_id))

            cur.execute(
                """
                SELECT DISTINCT p.[Id], p.[Abbreviation], p.[Name]
                FROM dbo.[Project] p
                INNER JOIN dbo.[BillLineItem] bli ON bli.[ProjectId] = p.[Id]
                INNER JOIN dbo.[Bill] b ON b.[Id] = bli.[BillId]
                WHERE p.[Abbreviation] IS NOT NULL
                  AND b.[IsDraft] = 0
                  AND b.[CreatedDatetime] < ?
                ORDER BY p.[Id]
                """,
                as_of,
            )
            for row in cur.fetchall():
                abbrev = normalize_project_abbreviation(str(row.Abbreviation))
                if abbrev:
                    proj_key_rows.append((abbrev, int(row.Id)))
                    candidate_projects.append(
                        {"abbreviation": abbrev, "name": str(row.Name or "")}
                    )
        finally:
            conn.rollback()

    scc_number_to_id, amb_scc = _unique_string_keys(scc_key_rows)
    proj_abbrev_to_id, amb_proj = _unique_string_keys(proj_key_rows)

    return CatalogSnapshot(
        scc_by_id=scc_by_id,
        scc_number_to_id=scc_number_to_id,
        ambiguous_scc_numbers=amb_scc,
        proj_abbrev_to_id=proj_abbrev_to_id,
        ambiguous_project_abbrevs=amb_proj,
        candidate_projects=candidate_projects,
        vendor_name=vendor_name,
    )


def load_catalog_from_db(as_of: datetime) -> CatalogSnapshot:
    key = _catalog_cache_key(as_of)
    now = _catalog_now_fn()
    with _catalog_lock:
        hit = _catalog_cache.get(key)
        if hit is not None and (now - hit[0]) < _CATALOG_CACHE_TTL_SECONDS:
            return hit[1]
    loader = _catalog_loader_override or _load_catalog_from_db
    snap = loader(as_of)
    with _catalog_lock:
        _catalog_cache.clear()
        _catalog_cache[key] = (now, snap)
    return snap


async def predict_text_coding(
    *,
    bill_text: str,
    vendor_id: int,
    chosen_project_id: Optional[int],
    as_of: datetime,
    exclude_bill_id: Optional[int] = None,
    text_repo: Optional[BillTextCodingSuggestionRepository] = None,
    suggester: Optional[BillCodingSuggester] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
    catalog: Optional[CatalogSnapshot] = None,
    vendor_name: Optional[str] = None,
    retain_accessible_project: bool = False,
) -> tuple[Optional[Any], Optional[str], Optional[CascadeResult]]:
    """Run the text-tier cascade; return (BillSuggestion|None, stopped_reason, coding cascade)."""
    from entities.coding_suggestion.business.bill_suggestion_service import (
        BillSuggestion,
        bill_suggestion_from_text_cascade,
    )

    settings = config.Settings()
    cap = int(settings.bill_coding_text_daily_call_cap)
    if cap <= 0:
        return None, None, None
    repository = text_repo or BillTextCodingSuggestionRepository()
    # Every DB read below runs off the event loop (U-676l): this coroutine serves live
    # requests, and a sync pyodbc call here would stall every request on the worker.
    if await asyncio.to_thread(repository.count_calls_today) >= cap:
        return None, "daily_cap", None

    if catalog is None:
        catalog = await asyncio.to_thread(load_catalog_from_db, as_of)
    if vendor_name:
        catalog = replace(catalog, vendor_name=vendor_name)
    elif not catalog.vendor_name:
        loaded_name = await asyncio.to_thread(_read_vendor_name, vendor_id)
        if loaded_name:
            catalog = replace(catalog, vendor_name=loaded_name)
    if bill_coding_candidate_sccs_mode(settings) == "narrowed":
        history_rows, narrowing_numbers = await asyncio.gather(
            asyncio.to_thread(
                repository.read_vendor_scc_counts,
                vendor_id,
                project_id=chosen_project_id,
                as_of=as_of,
                exclude_bill_id=exclude_bill_id,
            ),
            asyncio.to_thread(
                load_narrowing_history_scc_numbers,
                settings,
                repository,
                vendor_id=vendor_id,
                as_of=as_of,
                exclude_bill_id=exclude_bill_id,
            ),
        )
    else:
        history_rows = await asyncio.to_thread(
            repository.read_vendor_scc_counts,
            vendor_id,
            project_id=chosen_project_id,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        )
        narrowing_numbers = None
    built = build_runtime_payload(
        bill_text=bill_text,
        catalog=catalog,
        history_rows=history_rows,
        chosen_project_id=chosen_project_id,
        as_of=as_of,
        narrowing_history_scc_numbers=narrowing_numbers,
    )
    _log_bill_text_coding_suggestion_candidates(
        bill_id=exclude_bill_id,
        vendor_id=vendor_id,
        built=built,
    )
    payload = built.payload
    mode = built.mode
    cascade = await run_bill_coding_suggestion(payload, transport_for=transport_for)
    if cascade.stopped_reason == "daily_cap":
        return None, "daily_cap", cascade
    if not cascade.accepted or cascade.result is None:
        return None, None, cascade
    confidence = (
        Decimal(str(cascade.confidence)) if cascade.confidence is not None else None
    )
    winning_model = cascade.winning_rung.model if cascade.winning_rung else None
    engine = suggester or BillCodingSuggester()
    # Any failure after the cascade ran carries it, so the caller can still record its
    # cost/tokens (U-676m) — the caller's error handling is otherwise unchanged.
    try:
        suggestion = await asyncio.to_thread(
            bill_suggestion_from_text_cascade,
            cascade,
            catalog=catalog,
            mode=mode,
            chosen_project_id=chosen_project_id,
            project_service=engine.project_service,
            sub_cost_code_service=engine.sub_cost_code_service,
        )
        if (
            retain_accessible_project
            and suggestion is None
            and mode == "B"
            and cascade.accepted
            and cascade.result is not None
        ):
            from entities.coding_suggestion.business.bill_suggestion_service import (
                BillSuggestion,
                TEXT_LLM_SUGGESTION_REASON,
                resolve_accessible_text_project_id,
            )

            _scc_id, proj_id = resolve_text_prediction(
                cascade,
                scc_number_to_id=catalog.scc_number_to_id,
                proj_abbrev_to_id=catalog.proj_abbrev_to_id,
            )
            out_project = await asyncio.to_thread(
                resolve_accessible_text_project_id,
                proj_id,
                project_service=engine.project_service,
            )
            if out_project is not None:
                suggestion = BillSuggestion(
                    project_id=out_project,
                    sub_cost_code_id=None,
                    sub_cost_code_public_id=None,
                    sub_cost_code_number=None,
                    sub_cost_code_name=None,
                    source="llm",
                    reason=TEXT_LLM_SUGGESTION_REASON,
                    confidence=confidence,
                    raw_score=None,
                    top_count=None,
                    second_count=None,
                    total_count=None,
                    model_version=MODEL_VERSION,
                    predicted_project=True,
                    text_used=True,
                    text_mode=mode,
                    text_model=winning_model,
                )
    except Exception as error:
        error.coding_cascade = cascade  # type: ignore[attr-defined]
        raise
    return suggestion, None, cascade


def run_bill_text_coding_tick_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_bill_text_coding_tick(**kwargs))
