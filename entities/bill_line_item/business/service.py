# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from shared.access import assert_can_access_bill, assert_can_access_project
from shared.database import DatabaseConcurrencyError
from shared.lifecycle.terminal_lock import (
    StatusLockedError,
    assert_editable,
    is_exempt,
)
from shared.authz import current_user_id, current_is_system_admin
from entities.bill_line_item.business.model import BillLineItem
from entities.bill_line_item.persistence.repo import BillLineItemRepository
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService
from entities.bill.business.service import BillService

logger = logging.getLogger(__name__)


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


    def _assert_parent_editable(self, *, bill_id=None, bill_public_id=None,
                                what: str, exempt: bool = False) -> None:
        """U-446b: a completed bill's line items are frozen with it.

        Reads the parent rather than trusting the caller — the line item itself
        carries no lifecycle state. Exempt callers skip the read entirely, which
        matters because invoice completion touches these in a loop.
        """
        if exempt:
            return
        if bill_id is None and bill_public_id is None:
            # Nothing identifies the parent (a partially-populated object).
            # Fail OPEN, for the same reason terminal_lock.is_terminal does:
            # this is a lifecycle guard, not authorization, and blocking here
            # would break callers holding a partial row.
            return
        from shared.lifecycle.terminal_lock import is_system_caller
        if is_system_caller():
            return
        from entities.bill.business.service import BillService
        svc = BillService()
        parent = (svc.read_by_id(id=bill_id) if bill_id is not None
                  else svc.read_by_public_id(public_id=bill_public_id))
        if parent is None:
            return
        assert_editable(
            status=getattr(parent, "status", None),
            is_draft=getattr(parent, "is_draft", None),
            what=what,
        )

    def _reassert_after_a_lost_write(self, *, bill_id, bill_public_id=None, what, exempt):
        """U-446b (Codex round 4, P2). Turn a lost reparent race into 422.

        The in-transaction guards make a wrong write IMPOSSIBLE, but they make
        it impossible by matching zero rows — so a caller that lost the race
        sees "not found", a row-version 409, or a bare repo failure, none of
        which is the `status_locked` this unit promises. 409 is the worst of the
        three: installed iOS routes it to reload-and-retry, so a permanent
        refusal delivered that way loops.

        Re-reading the parent on the FAILURE PATH ONLY costs nothing in the
        normal case and names what actually happened.
        """
        self._assert_parent_editable(
            bill_id=bill_id, bill_public_id=bill_public_id, what=what, exempt=exempt
        )

    def create(self, *, tenant_id: int = None, bill_public_id: str, sub_cost_code_id: Optional[int] = None, project_public_id: Optional[str] = None, description: Optional[str] = None, quantity: Optional[Decimal] = None, rate: Optional[Decimal] = None, amount: Optional[Decimal] = None, is_billable: Optional[bool] = None, is_billed: Optional[bool] = None, markup: Optional[Decimal] = None, price: Optional[Decimal] = None, is_draft: bool = True, _via_internal_pipeline: bool = False) -> BillLineItem:
        """
        Create a new bill line item.
        """
        self._assert_parent_editable(
            bill_public_id=bill_public_id,
            what="line items cannot be added to it",
            exempt=_via_internal_pipeline,
        )
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
            # U-446b: the sproc re-checks the parent INSIDE the writing
            # transaction, closing the check-then-write race the Python guard
            # above cannot. Always passed explicitly, though the sproc default is
            # fail-closed since U-446c.
            allow_terminal_parent=is_exempt(_via_internal_pipeline),
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
        Read bill line items, scoped by UserProject for non-admin actors.

        Mirrors BillService.read_all. Scoping happens in the sproc (a per-row
        assert_can_access_bill would be an N-query scan); every other read on
        this service gates via assert_can_access_* instead, because they return
        at most one bill's worth of rows.
        """
        return self.repo.read_all(
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

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
        quantity: Decimal = None,
        rate: float = None,
        amount: float = None,
        is_billable: bool = None,
        is_billed: bool = None,
        markup: float = None,
        price: float = None,
        is_draft: bool = None,
        _via_internal_pipeline: bool = False,
    ) -> Optional[BillLineItem]:
        """
        Update a bill line item by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing is not None:
            # The CURRENT parent...
            self._assert_parent_editable(
                bill_id=getattr(existing, "bill_id", None),
                what="its line items cannot be changed",
                exempt=_via_internal_pipeline,
            )
            # ...AND the TARGET parent, when the caller is re-pointing the line
            # (Codex P0). Checking only the current one let anyone MOVE a line
            # onto a completed bill by PUTting it with that bill's public id:
            # the destination was read for existence and access, but never for
            # lifecycle, so the completed bill silently gained a line.
            if bill_public_id is not None:
                self._assert_parent_editable(
                    bill_public_id=bill_public_id,
                    what="line items cannot be moved onto it",
                    exempt=_via_internal_pipeline,
                )
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
            existing.quantity = Decimal(str(quantity))
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

        try:
            return self.repo.update_by_id(
                existing, allow_terminal_parent=is_exempt(_via_internal_pipeline)
            )
        except StatusLockedError:
            raise
        except DatabaseConcurrencyError:
            # NARROW on purpose (Codex round 5, P2). Re-asserting on ANY
            # exception meant a deadlock victim, a dropped connection or a
            # serialization failure came back as `status_locked` whenever the
            # bill happened to complete in the meantime — a transient fault
            # relabelled as a permanent refusal, which is the wrong thing for
            # the client to act on. Only a concurrency/zero-row outcome is what
            # a lost terminal race actually looks like.
            self._reassert_after_a_lost_write(
                bill_id=getattr(existing, "bill_id", None),
                bill_public_id=bill_public_id,
                what="its line items cannot be changed",
                exempt=_via_internal_pipeline,
            )
            raise

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None, _via_internal_pipeline: bool = False) -> Optional[BillLineItem]:
        """
        Delete a bill line item by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing is not None:
            self._assert_parent_editable(
                bill_id=getattr(existing, "bill_id", None),
                what="its line items cannot be deleted",
                exempt=_via_internal_pipeline,
            )
        if existing:
            # U-446c: ONE transaction. Every dependent row this used to clear in
            # its own committed statement — the invoice lines, the ContractLabor
            # FK, the legacy qbo mapping — now goes inside the same transaction
            # as the line delete, with the parent Bill locked throughout. Before
            # this, a completion landing after the cleanup committed produced
            # `status_locked` on a line whose dependents were already gone.
            #
            # The sproc also clears the BillLineItemAttachment link, which this
            # path never did: that FK is NO ACTION, so deleting a line that
            # still had its attachment link failed with 547. Only the bill-level
            # cascade cleared it first, which is why the bug only bit here.
            deleted = self.repo.delete_cascade_by_id(
                existing.id, allow_terminal_parent=is_exempt(_via_internal_pipeline)
            )
            if deleted is None:
                # The DELETE is bound to the parent the guard locked, so zero
                # rows can mean the line was MOVED onto a bill that completed.
                self._reassert_after_a_lost_write(
                    bill_id=getattr(existing, "bill_id", None),
                    what="its line items cannot be deleted",
                    exempt=_via_internal_pipeline,
                )
            return deleted
        return None
