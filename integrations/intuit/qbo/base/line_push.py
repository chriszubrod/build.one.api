"""
Shared QBO expense-line push helpers (U-797c5a).

Lifted from the Bill connector (`BillBillConnector._build_qbo_line` and its line-id
stamp) so the BillCredit -> VendorCredit create push builds its lines the same way.
The Bill path's output is pinned byte-identical by tests/test_u797c5a_line_push_golden.py.

The helpers take the item / customer resolvers as callables so each connector keeps
its own resolution methods (and any test patches on them) and only the decision logic
is shared. Schema construction stays in each connector (Bill and VendorCredit have
their own pydantic models).
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ItemExpenseLinePlan:
    """Decided values for one ItemBasedExpenseLineDetail line, schema-agnostic."""

    line_num: int
    description: Any
    amount: Decimal
    item_ref: Any
    customer_ref: Any
    billable_status: str
    qty: Optional[Decimal]
    unit_price: Any
    markup_info: Optional[dict]


def build_item_expense_line_plan(
    line_item,
    line_num: int,
    realm_id: Optional[str],
    *,
    resolve_item_ref: Callable[[Any, Optional[str]], Any],
    resolve_customer_ref: Callable[[Any], Any],
    unit_price_of: Callable[[Any], Any],
    markup_of: Callable[[Any], Any],
    line_label: str = "BillLineItem",
    document_label: str = "bill",
) -> ItemExpenseLinePlan:
    """
    Decide the QBO values for one local line (SubCostCode -> ItemRef, Project ->
    CustomerRef, BillableStatus, qty/rate/amount). Raises ValueError on an unmapped
    SubCostCode, a missing SubCostCode, or a billable project line with no CustomerRef
    (fail loud, as the Bill path always has). A holding code raises HoldingCodeRefused
    from the resolver and propagates unchanged. `unit_price_of` / `markup_of` are explicit
    because BillLineItem carries `rate` + `markup` while BillCreditLineItem carries
    `unit_price` and no markup (U-797c5a round 1).
    """
    logger.debug(
        f"Building QBO line for {line_label} {line_item.id}: sub_cost_code_id={line_item.sub_cost_code_id}, "
        f"project_id={line_item.project_id}"
    )

    item_ref = None
    if line_item.sub_cost_code_id:
        item_ref = resolve_item_ref(line_item.sub_cost_code_id, realm_id)
        if not item_ref:
            raise ValueError(
                f"{line_label} {line_item.id}: no QBO Item mapping for sub_cost_code_id={line_item.sub_cost_code_id}. "
                f"Map the SubCostCode to a QBO Item before syncing."
            )
    else:
        raise ValueError(
            f"{line_label} {line_item.id} has no sub_cost_code_id. All line items require a SubCostCode for QBO sync."
        )

    customer_ref = resolve_customer_ref(line_item.project_id) if line_item.project_id else None

    # is_billable=None means default billable (treat same as True).
    # is_billable=False means explicitly not billable.
    # If BillableStatus is "Billable", CustomerRef is REQUIRED by QBO.
    if line_item.is_billable is not False:
        if customer_ref:
            billable_status = "HasBeenBilled" if line_item.is_billed is True else "Billable"
        elif line_item.project_id:
            # Fail loud: a billable line WITH a project but no QBO CustomerRef mapping must not
            # be silently downgraded to NotBillable (that silent downgrade let 14 OVH bills push
            # in the wrong state unnoticed, 2026-07).
            raise ValueError(
                f"{line_label} {line_item.id}: billable line on project_id={line_item.project_id} "
                f"has no QBO CustomerRef mapping. Ensure the Project has a QBO identity stamped "
                f"(dbo.Project.QboId/RealmId, e.g. by syncing it from QBO) before syncing this {document_label}."
            )
        else:
            logger.warning(
                f"Line item {line_item.id} is billable but has no project_id; setting to NotBillable."
            )
            billable_status = "NotBillable"
    else:
        billable_status = "NotBillable"

    markup = markup_of(line_item)
    markup_percent = None
    if markup is not None:
        markup_percent = markup * Decimal("100")

    line_amount = line_item.amount
    qty = Decimal(str(line_item.quantity)) if line_item.quantity is not None else None
    unit_price = unit_price_of(line_item)

    if line_amount is None and qty is not None and unit_price is not None:
        line_amount = qty * unit_price

    if line_amount is None:
        logger.warning(f"Line item {line_item.id} has no amount, qty, or rate. Using 0.")
        line_amount = Decimal("0")

    return ItemExpenseLinePlan(
        line_num=line_num,
        description=line_item.description,
        amount=line_amount,
        item_ref=item_ref,
        customer_ref=customer_ref,
        billable_status=billable_status,
        qty=qty,
        unit_price=unit_price,
        # float() is acceptable: the percentage goes into a Dict[str, Any] (JSON-serialisable).
        markup_info={"Percent": float(markup_percent)} if markup_percent is not None else None,
    )


def qbo_identity_stamp_landed(restamped, qbo_line_id) -> bool:
    """True when a fresh read-back of a local line carries the QBO line id just stamped."""
    return bool(restamped) and restamped.qbo_id == qbo_line_id
