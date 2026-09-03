# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from shared.access import assert_can_access_bill, assert_can_access_project
from shared.authz import current_user_id
from entities.bill_line_item.business.model import BillLineItem
from entities.bill_line_item.persistence.repo import BillLineItemRepository
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService
from entities.bill.business.service import BillService

logger = logging.getLogger(__name__)


def _clear_legacy_bill_line_item_bill_line_mapping(bill_line_item_id: int) -> None:
    """U-363 deploy-gap bridge for BillLineItemService.delete_by_public_id — see
    its call site. Raw SQL, not a repo/model (both retired in this unit):
    deletes any row in the (soon-to-be-dropped) qbo.BillLineItemBillLine table
    that still points at this BillLineItem, so its NO ACTION FK
    (FK_BillLineItemBillLine_BillLineItem, live since
    scripts/migrations/u225_qbo_mapping_fk_gaps.sql) never blocks the line
    delete below. Mirrors InvoiceLineItemService's U-362
    _clear_legacy_invoice_line_item_invoice_line_mapping bridge exactly (same
    OBJECT_ID-guard idiom — table-already-dropped becomes a plain SQL no-op,
    not a caught Python exception). Once /em applies the DROP for this table,
    this whole function becomes a permanent no-op and should be deleted (see
    U-365, which did exactly that for the 4 header mapping tables)."""
    from shared.database import get_connection

    try:
        with get_connection() as conn:
            conn.cursor().execute(
                "IF OBJECT_ID('qbo.BillLineItemBillLine', 'U') IS NOT NULL "
                "DELETE FROM [qbo].[BillLineItemBillLine] WHERE [BillLineItemId] = ?",
                (bill_line_item_id,),
            )
    except Exception as e:
        # Only reachable now for a genuine unexpected failure (connection reset,
        # deadlock, permissions) — table-missing no longer raises at all. Logged,
        # not raised: best-effort only. The real safety net is the FK itself — if
        # a mapping row really does still exist and this failed to clear it, the
        # line delete below 547s anyway (fail-safe, not fail-silent-corruption).
        logger.warning(
            f"Could not clear legacy qbo.BillLineItemBillLine mapping for "
            f"BillLineItem {bill_line_item_id}: {e}"
        )


# ---------------------------------------------------------------------------
# Box deep-link URL builders.
# ---------------------------------------------------------------------------
# Kept at module scope (pure, stateless) so they can be unit-tested + shared
# without dragging in the rest of the service. The Box web app accepts a bare
# folder/file id under the canonical paths below; no auth bounce, no realm.

def _build_box_folder_url(box_folder_id: str) -> str:
    return f"https://app.box.com/folder/{box_folder_id}"


def _build_box_file_url(box_file_id: str) -> str:
    return f"https://app.box.com/file/{box_file_id}"


class BillLineItemService:
    """
    Service for BillLineItem entity business operations.
    """

    def __init__(self, repo: Optional[BillLineItemRepository] = None):
        """Initialize the BillLineItemService."""
        self.repo = repo or BillLineItemRepository()

    def create(self, *, tenant_id: int = None, bill_public_id: str, sub_cost_code_id: Optional[int] = None, project_public_id: Optional[str] = None, description: Optional[str] = None, quantity: Optional[int] = None, rate: Optional[Decimal] = None, amount: Optional[Decimal] = None, is_billable: Optional[bool] = None, is_billed: Optional[bool] = None, markup: Optional[Decimal] = None, price: Optional[Decimal] = None, is_draft: bool = True) -> BillLineItem:
        """
        Create a new bill line item.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        # Validate Bill exists and get internal ID
        bill = BillService().read_by_public_id(public_id=bill_public_id)
        if not bill:
            raise ValueError(f"Bill with public_id '{bill_public_id}' not found.")
        
        # Validate SubCostCode exists if provided
        if sub_cost_code_id is not None:
            # Note: SubCostCodeService.read_by_id expects a string
            sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
            if not sub_cost_code:
                raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")
        
        # Validate Project exists if provided and get internal ID
        project_id = None
        if project_public_id is not None:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            project_id = project.id
        
        return self.repo.create(
            bill_id=bill.id,
            sub_cost_code_id=sub_cost_code_id,
            project_id=project_id,
            description=description,
            quantity=quantity,
            rate=rate,
            amount=amount,
            is_billable=is_billable,
            is_billed=is_billed,
            markup=markup,
            price=price,
            is_draft=is_draft,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[BillLineItem]:
        """
        Read all bill line items.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[BillLineItem]:
        """
        Read a bill line item by ID.
        """
        line_item = self.repo.read_by_id(id)
        if line_item is None:
            return None
        assert_can_access_bill(line_item.bill_id)
        return line_item

    def read_by_public_id(self, public_id: str) -> Optional[BillLineItem]:
        """
        Read a bill line item by public ID.
        """
        line_item = self.repo.read_by_public_id(public_id)
        if line_item is None:
            return None
        assert_can_access_bill(line_item.bill_id)
        return line_item

    def read_by_bill_id(self, bill_id: int) -> list[BillLineItem]:
        """
        Read all bill line items for a specific bill.
        """
        assert_can_access_bill(bill_id)
        return self.repo.read_by_bill_id(bill_id=bill_id)

    def read_by_qbo_identity(self, bill_id: int, qbo_id: str) -> Optional[BillLineItem]:
        """
        Read a bill line item directly by its dbo-native QBO identity,
        scoped to its parent Bill (U-293) — the line-level Phase-4 repoint
        seam, bypassing the qbo.BillLine/qbo.BillLineItemBillLine
        staging/mapping tables.
        """
        assert_can_access_bill(bill_id)
        return self.repo.read_by_qbo_identity(bill_id=bill_id, qbo_id=qbo_id)

    def get_box_links_by_bill_id(self, bill_id: int) -> dict[int, dict]:
        """
        Return per-line-item Box deep-link URLs for a bill, keyed by
        BillLineItemId. Each value is a dict with `box_folder_url` and
        `box_workbook_url` — either nullable string. The router merges
        the result row-by-row into the existing line-item list response.

        Access-gates on the parent bill the same way `read_by_bill_id`
        does, so unauthorized callers don't even learn whether the bill
        has Box mappings.
        """
        assert_can_access_bill(bill_id)
        raw = self.repo.read_box_links_by_bill_id(bill_id=bill_id)
        out: dict[int, dict] = {}
        for line_item_id, ids in raw.items():
            folder_id = ids.get("box_invoices_folder_id")
            file_id = ids.get("box_workbook_file_id")
            out[line_item_id] = {
                "box_folder_url": _build_box_folder_url(folder_id) if folder_id else None,
                "box_workbook_url": _build_box_file_url(file_id) if file_id else None,
            }
        return out

    def read_by_project_id(self, project_id: int) -> list[BillLineItem]:
        """
        Read all bill line items for a specific project.
        """
        assert_can_access_project(project_id)
        return self.repo.read_by_project_id(project_id=project_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        bill_public_id: str = None,
        sub_cost_code_id: int = None,
        project_public_id: str = None,
        description: str = None,
        quantity: int = None,
        rate: float = None,
        amount: float = None,
        is_billable: bool = None,
        is_billed: bool = None,
        markup: float = None,
        price: float = None,
        is_draft: bool = None,
    ) -> Optional[BillLineItem]:
        """
        Update a bill line item by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = row_version

        # Validate Bill exists if provided (using public_id)
        if bill_public_id is not None:
            bill = BillService().read_by_public_id(public_id=bill_public_id)
            if not bill:
                raise ValueError(f"Bill with public_id '{bill_public_id}' not found.")
            existing.bill_id = bill.id

        # Set SubCostCode only when provided; None PRESERVES the existing value (never clears).
        if sub_cost_code_id is not None:
            # Note: SubCostCodeService.read_by_id expects a string
            sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
            if not sub_cost_code:
                raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")
            existing.sub_cost_code_id = sub_cost_code_id

        # Set Project only when provided; None PRESERVES the existing value (never clears — the
        # update sproc's unconditional [ProjectId] SET receives the loaded id, so it re-writes).
        # No clear-a-project path exists; see U-172 (won't-fix, behavior ratified by U-111).
        if project_public_id is not None:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            existing.project_id = project.id

        # Update fields
        if description is not None:
            existing.description = description
        if quantity is not None:
            existing.quantity = quantity
        if rate is not None:
            existing.rate = Decimal(str(rate))
        if amount is not None:
            existing.amount = Decimal(str(amount))
        if is_billable is not None:
            existing.is_billable = is_billable
        if is_billed is not None:
            existing.is_billed = is_billed
        if markup is not None:
            existing.markup = Decimal(str(markup))
        if price is not None:
            existing.price = Decimal(str(price))
        if is_draft is not None:
            existing.is_draft = is_draft

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[BillLineItem]:
        """
        Delete a bill line item by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            from entities.invoice_line_item.persistence.repo import InvoiceLineItemRepository
            from entities.contract_labor.persistence.repo import ContractLaborRepository
            InvoiceLineItemRepository().delete_by_bill_line_item_id(existing.id)
            cl_repo = ContractLaborRepository()
            for cl_entry in cl_repo.read_by_bill_line_item_id(existing.id):
                cl_entry.bill_line_item_id = None
                cl_repo.update_by_id(cl_entry)
            # U-363: qbo.BillLineItemBillLine's CONNECTOR (mapping_repo,
            # create_mapping, the legacy fastpath's mapping fallback) is retired
            # — dbo.BillLineItem.QboId/RealmId (U-238b) is the sole identity store
            # going forward. The TABLE itself is not dropped by this unit (that's
            # a separate /em-run post-deploy step), and it carries a live NO
            # ACTION FK onto this one (FK_BillLineItemBillLine_BillLineItem,
            # added by scripts/migrations/u225_qbo_mapping_fk_gaps.sql) — so a
            # still-mapped row's delete WOULD 547 without this bridge. Correcting
            # this comment's prior claim: that FK did not exist when this delete
            # path was first written, but it does now.
            _clear_legacy_bill_line_item_bill_line_mapping(existing.id)
            return self.repo.delete_by_id(existing.id)
        return None
