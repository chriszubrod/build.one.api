"""
Strict gate for pull-side delete-reconciliation (U-212).

The pull scripts arm `reconcile_deletes=True` on every scheduled tick; on a
full sync (watermark None) the entity services diff local staging against
QBO and HARD-DELETE anything absent — cascading into the mapped dbo entity,
its line items, attachments, and blobs. Before U-212 that diff trusted the
lenient `query_all_<entity>s` pagers, whose loops read a missing
QueryResponse as end-of-pages — so one degraded page mid-pagination made a
partial list indistinguishable from a complete one and armed a mass delete
(2026-08-07 audit, P1-05/06/12).

This module is the single home for the safe discipline, mirroring the
reconciliation void detectors (layer-for-layer):

  1. Live ids come from the STRICT id pager (complete-or-abort;
     `base/paging.py`). Any anomaly aborts the whole reconcile — nothing is
     deleted off a doubtful id set.
  2. A candidate ceiling: more absentees than `QBO_PULL_DELETE_MAX_CANDIDATES`
     (default 50) is far more likely an id-fetch artifact or environment
     mixup than a real mass deletion — abort loudly, delete nothing.
  3. Every candidate is confirmed with an individual GET; only a
     QboNotFoundError (HTTP 404, or HTTP 400 fault 610 — Intuit's actual
     signal for hard-deleted transactions) confirms. A systemic error
     (budget breaker, rate limit, auth) aborts the loop; anything else
     skips that record.

Both sides of the diff key through `normalize_qbo_id` (`base/ids.py`), per
that module's contract.
"""

# Python Standard Library Imports
import logging
from shared.env_flags import _env_positive_int
from typing import Callable, Iterable, List, Optional, Set

# Local Imports
from integrations.intuit.qbo.base.errors import (
    QboAuthError,
    QboBudgetExceededError,
    QboNotFoundError,
    QboRateLimitError,
)
from integrations.intuit.qbo.base.ids import normalize_qbo_id

logger = logging.getLogger(__name__)


DEFAULT_DELETE_MAX_CANDIDATES = 50


def _delete_max_candidates() -> int:
    return _env_positive_int(
        "QBO_PULL_DELETE_MAX_CANDIDATES", DEFAULT_DELETE_MAX_CANDIDATES, minimum=1, warn=False
    )


def strict_confirmed_deleted_ids(
    *,
    entity_type: str,
    realm_id: str,
    fetch_live_ids: Callable[[], List[str]],
    confirm_get: Callable[[str], object],
    local_qbo_ids: Iterable[str],
) -> Optional[Set[str]]:
    """
    Return the set of local qbo_ids (normalized) CONFIRMED deleted in QBO,
    or None when the reconcile must be aborted (delete nothing).

    `fetch_live_ids` must be a strict pager (query_all_<entity>_ids);
    `confirm_get` must raise QboNotFoundError for a deleted record. Callers
    must key their membership test through `normalize_qbo_id` as well.
    """
    candidates = [n for n in (normalize_qbo_id(i) for i in local_qbo_ids) if n]
    if not candidates:
        return set()

    try:
        live_ids = set(fetch_live_ids())
    except Exception:
        logger.exception(
            "qbo.pull.delete_reconcile.id_fetch_failed",
            extra={
                "event_name": "qbo.pull.delete_reconcile.id_fetch_failed",
                "entity_type": entity_type,
                "realm_id": realm_id,
            },
        )
        return None

    absent = [i for i in candidates if i not in live_ids]
    if not absent:
        return set()

    ceiling = _delete_max_candidates()
    if len(absent) > ceiling:
        logger.error(
            "qbo.pull.delete_reconcile.candidate_ceiling_exceeded",
            extra={
                "event_name": "qbo.pull.delete_reconcile.candidate_ceiling_exceeded",
                "entity_type": entity_type,
                "realm_id": realm_id,
                "candidate_count": len(absent),
                "max_candidates": ceiling,
                "local_count": len(candidates),
                "live_count": len(live_ids),
            },
        )
        record_delete_reconcile_issue(
            entity_type=entity_type,
            realm_id=realm_id,
            qbo_id=None,
            details=(
                f"Pull delete-reconcile aborted: {len(absent)} locally-staged {entity_type}(s) "
                f"absent from the live QBO id set, above the {ceiling} candidate ceiling "
                f"({len(candidates)} local, {len(live_ids)} live). Nothing was deleted — this is "
                f"far more likely an incomplete id fetch or environment mixup than a mass "
                f"deletion. Raise QBO_PULL_DELETE_MAX_CANDIDATES only after confirming in QBO."
            ),
        )
        return None

    confirmed: Set[str] = set()
    for qbo_id in absent:
        try:
            confirm_get(qbo_id)
        except QboNotFoundError:
            confirmed.add(qbo_id)
        except (QboBudgetExceededError, QboRateLimitError, QboAuthError):
            # Systemic: no later candidate can succeed either. Abort the whole
            # reconcile (delete nothing) instead of burning one metered call +
            # a warning line per remaining candidate against a tripped breaker
            # or a rate-limit window.
            logger.warning(
                "qbo.pull.delete_reconcile.confirm_aborted_systemic",
                extra={
                    "event_name": "qbo.pull.delete_reconcile.confirm_aborted_systemic",
                    "entity_type": entity_type,
                    "realm_id": realm_id,
                    "confirmed_so_far": len(confirmed),
                    "remaining": len(absent) - len(confirmed),
                },
            )
            return None
        except Exception as e:
            logger.warning(
                "qbo.pull.delete_reconcile.confirm_failed",
                extra={
                    "event_name": "qbo.pull.delete_reconcile.confirm_failed",
                    "entity_type": entity_type,
                    "realm_id": realm_id,
                    "qbo_id": qbo_id,
                    "error": str(e),
                },
            )
        else:
            # The GET succeeded: the record is alive despite missing from the
            # id query — an id-set artifact, exactly what confirm exists for.
            logger.warning(
                "qbo.pull.delete_reconcile.candidate_alive",
                extra={
                    "event_name": "qbo.pull.delete_reconcile.candidate_alive",
                    "entity_type": entity_type,
                    "realm_id": realm_id,
                    "qbo_id": qbo_id,
                },
            )
    return confirmed


def record_delete_reconcile_issue(
    *,
    entity_type: str,
    realm_id: str,
    qbo_id: Optional[str],
    details: str,
) -> None:
    """
    Durable follow-up record in [qbo].[ReconciliationIssue] — used for the
    ceiling abort and for partial deletions. Failure-isolated: an issue-write
    error must never affect the reconcile itself.
    """
    try:
        from integrations.intuit.qbo.reconciliation.persistence.repo import (
            ReconciliationIssueRepository,
        )

        ReconciliationIssueRepository().create(
            drift_type="pull_delete_reconcile",
            severity="critical",
            action="flagged",
            entity_type=entity_type,
            realm_id=realm_id,
            qbo_id=qbo_id,
            details=details,
        )
        # Same event name the reconciliation service emits, so existing
        # issue monitoring sees pull-delete records without a new query.
        logger.warning(
            "qbo.reconcile.issue.flagged",
            extra={
                "event_name": "qbo.reconcile.issue.flagged",
                "drift_type": "pull_delete_reconcile",
                "severity": "critical",
                "entity_type": entity_type,
                "realm_id": realm_id,
                "qbo_id": qbo_id,
            },
        )
    except Exception:
        logger.exception(
            "qbo.pull.delete_reconcile.issue_write_failed",
            extra={
                "event_name": "qbo.pull.delete_reconcile.issue_write_failed",
                "entity_type": entity_type,
                "realm_id": realm_id,
                "qbo_id": qbo_id,
            },
        )


def record_partial_delete_issue(
    *,
    entity_type: str,
    mapping_label: str,
    mapped_label: str,
    realm_id: str,
    qbo_id: str,
    local_id: object,
    error: Exception,
) -> None:
    """One canned message for the three services' partial-delete case:
    mapping rows removed but the entity/staging delete failed — the mapped
    record is now unlinked (a zombie invisible to future syncs)."""
    record_delete_reconcile_issue(
        entity_type=entity_type,
        realm_id=realm_id,
        qbo_id=qbo_id,
        details=(
            f"Partial delete: {mapping_label} mapping row(s) removed but {mapped_label}/staging "
            f"delete failed for Qbo{entity_type} {qbo_id} (local id={local_id}): {error}. "
            f"The mapped {mapped_label} is now unlinked — manual cleanup required."
        ),
    )
