"""U-797c5b: record-only coding-drift check for pulled documents completed in the app.

A document that was pulled from QBO (has a qbo_id) can be recoded by a reviewer before
completion, so its line coding may differ from QBO. We NEVER write to QBO here. We detect the
difference and record a ReconciliationIssue so a human can fix QBO by hand.
"""
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional


@dataclass(frozen=True)
class CodingDrift:
    line_id: int
    qbo_line_id: str
    field: str  # "item" or "customer"


def _norm(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def find_coding_drift(
    local_lines: Iterable[Any],
    staged_lines: Iterable[Any],
    *,
    item_qbo_id_of: Callable[[Any], Optional[str]],
    customer_qbo_id_of: Callable[[Any], Optional[str]],
    is_holding: Callable[[Any], bool],
) -> list:
    """Pure matcher. Drift is only reported against a resolved QBO value.

    A local SCC/project whose resolved QBO id is None (code not in QBO, or an actor-scoped
    read that came back empty) is skipped, and so is a local None: we never imply clearing a
    QBO value. Description is not compared.
    """
    staged_by_line = {}
    for staged in staged_lines:
        if staged.qbo_line_id is not None:
            staged_by_line[str(staged.qbo_line_id)] = staged

    drift = []
    for local in local_lines:
        if local.qbo_id is None:
            continue
        staged = staged_by_line.get(str(local.qbo_id))
        if staged is None:
            continue
        qbo_line_id = str(local.qbo_id)

        if local.sub_cost_code_id is not None and not is_holding(local.sub_cost_code_id):
            expected_item = _norm(item_qbo_id_of(local.sub_cost_code_id))
            if expected_item is not None and expected_item != _norm(staged.item_ref_value):
                drift.append(CodingDrift(local.id, qbo_line_id, "item"))

        if local.project_id is not None:
            expected_customer = _norm(customer_qbo_id_of(local.project_id))
            if expected_customer is not None and expected_customer != _norm(staged.customer_ref_value):
                drift.append(CodingDrift(local.id, qbo_line_id, "customer"))
    return drift


def record_coding_drift_on_completion(
    *,
    entity_type: str,
    entity_public_id: Optional[str],
    qbo_id: Optional[str],
    realm_id: Optional[str],
    local_lines: Iterable[Any],
    read_staged_lines: Callable[[], Optional[list]],
    item_qbo_id_of: Callable[[Any], Optional[str]],
    customer_qbo_id_of: Callable[[Any], Optional[str]],
    is_holding: Callable[[Any], bool],
    reconciliation_repo: Any,
    logger: Any,
) -> dict:
    """Never raises. Returns {"checked": bool, "drift_count": int}."""
    not_checked = {"checked": False, "drift_count": 0}
    try:
        if not qbo_id or not realm_id:
            return dict(not_checked)

        staged = read_staged_lines()
        if staged is None:
            logger.info(
                "qbo.coding_drift.skipped no staging header entity_type=%s entity=%s qbo_id=%s",
                entity_type, entity_public_id, qbo_id,
            )
            return dict(not_checked)

        item_memo: dict = {}
        customer_memo: dict = {}

        def _item_of(scc_id):
            if scc_id not in item_memo:
                item_memo[scc_id] = item_qbo_id_of(scc_id)
            return item_memo[scc_id]

        def _customer_of(project_id):
            if project_id not in customer_memo:
                customer_memo[project_id] = customer_qbo_id_of(project_id)
            return customer_memo[project_id]

        drift = find_coding_drift(
            list(local_lines), staged,
            item_qbo_id_of=_item_of,
            customer_qbo_id_of=_customer_of,
            is_holding=is_holding,
        )
        if drift:
            logger.warning(
                "qbo.coding_drift.detected entity_type=%s entity=%s qbo_id=%s lines=%d",
                entity_type, entity_public_id, qbo_id, len(drift),
            )
            parts = "; ".join(
                f"line {d.line_id} (qbo line {d.qbo_line_id}) {d.field}" for d in drift
            )
            from integrations.intuit.qbo.base.reconciliation_recorder import (
                record_reviewed_coding_not_in_qbo_deduped,
            )
            record_reviewed_coding_not_in_qbo_deduped(
                reconciliation_repo,
                entity_type=entity_type,
                entity_public_id=entity_public_id,
                qbo_id=qbo_id,
                realm_id=realm_id,
                details=(
                    f"Completed {entity_type} {entity_public_id} coding differs from QBO; "
                    f"fix in QBO by hand: {parts}"
                ),
            )
        return {"checked": True, "drift_count": len(drift)}
    except Exception:
        logger.exception(
            "qbo.coding_drift.failed entity_type=%s entity=%s qbo_id=%s",
            entity_type, entity_public_id, qbo_id,
        )
        return dict(not_checked)


def record_coding_drift_for(*, entity_type, doc, local_lines, read_staged_lines, logger) -> None:
    """Shared Bill/BillCredit wiring. Owns the SCC/project/reconciliation wiring and ONE outer try.

    `doc` needs public_id, qbo_id and realm_id. No qbo_id means no check (the whole call is a
    no-op). Never raises; the caller only supplies its entity type, doc, local lines and staging reader.
    """
    try:
        if not getattr(doc, "qbo_id", None):
            return
        from entities.sub_cost_code.business.service import SubCostCodeService
        from entities.project.business.service import ProjectService
        from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

        sub_cost_code_service = SubCostCodeService()
        project_service = ProjectService()

        def _qbo_id_of(record):
            return getattr(record, "qbo_id", None) if record is not None else None

        record_coding_drift_on_completion(
            entity_type=entity_type,
            entity_public_id=str(doc.public_id),
            qbo_id=doc.qbo_id,
            realm_id=doc.realm_id,
            local_lines=local_lines,
            read_staged_lines=read_staged_lines,
            item_qbo_id_of=lambda scc_id: _qbo_id_of(sub_cost_code_service.read_by_id(id=scc_id)),
            customer_qbo_id_of=lambda project_id: _qbo_id_of(project_service.read_by_id(id=project_id)),
            is_holding=sub_cost_code_service.is_holding_code,
            reconciliation_repo=ReconciliationIssueRepository(),
            logger=logger,
        )
    except Exception:
        logger.exception(
            "qbo.coding_drift.failed entity_type=%s entity=%s",
            entity_type, getattr(doc, "public_id", None),
        )
