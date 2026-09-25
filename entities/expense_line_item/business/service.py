# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.expense_line_item.business.model import ExpenseLineItem
from entities.expense_line_item.persistence.repo import ExpenseLineItemRepository
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService
from entities.expense.business.service import ExpenseService
from shared.access import assert_can_access_expense
from shared.authz import current_user_id, current_is_system_admin
from shared.database import DatabaseConcurrencyError
from shared.lifecycle.terminal_lock import (
    StatusLockedError,
    assert_editable,
    is_exempt,
)

logger = logging.getLogger(__name__)


class ExpenseLineItemService:
    """
    Service for ExpenseLineItem entity business operations.
    """

    def __init__(self, repo: Optional[ExpenseLineItemRepository] = None):
        """Initialize the ExpenseLineItemService."""
        self.repo = repo or ExpenseLineItemRepository()

    def _assert_parent_editable(self, *, expense_id=None, expense_public_id=None,
                                what: str, exempt: bool = False) -> None:
        """U-468: a completed expense's line items are frozen with it.

        Reads the parent rather than trusting the caller — the line item itself
        carries no lifecycle state. Exempt callers skip the read entirely.
        """
        if exempt:
            return
        if expense_id is None and expense_public_id is None:
            return
        from shared.lifecycle.terminal_lock import is_system_caller
        if is_system_caller():
            return
        from entities.expense.business.service import ExpenseService
        svc = ExpenseService()
        parent = (svc.read_by_id(id=expense_id) if expense_id is not None
                  else svc.read_by_public_id(public_id=expense_public_id))
        if parent is None:
            return
        assert_editable(
            status=getattr(parent, "status", None),
            is_draft=getattr(parent, "is_draft", None),
            what=what,
        )

    def _reassert_after_a_lost_write(self, *, expense_id, expense_public_id=None, what, exempt):
        """U-468 (U-446b Codex round 4). Turn a lost reparent race into 422."""
        self._assert_parent_editable(
            expense_id=expense_id, expense_public_id=expense_public_id, what=what, exempt=exempt
        )

    def create(self, *, tenant_id: int = None, expense_public_id: str, sub_cost_code_id: Optional[int] = None, project_public_id: Optional[str] = None, description: Optional[str] = None, quantity: Optional[Decimal] = None, rate: Optional[Decimal] = None, amount: Optional[Decimal] = None, is_billable: Optional[bool] = None, is_billed: Optional[bool] = None, markup: Optional[Decimal] = None, price: Optional[Decimal] = None, is_draft: bool = True, _via_internal_pipeline: bool = False) -> ExpenseLineItem:
        """
        Create a new expense line item.
        """
        self._assert_parent_editable(
            expense_public_id=expense_public_id,
            what="line items cannot be added to it",
            exempt=_via_internal_pipeline,
        )
        # TODO: In Phase 10, use tenant_id for tenant isolation
        # Validate Expense exists and get internal ID
        expense = ExpenseService().read_by_public_id(public_id=expense_public_id)
        if not expense:
            raise ValueError(f"Expense with public_id '{expense_public_id}' not found.")
        
        # Validate SubCostCode exists if provided
        if sub_cost_code_id is not None:
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
            allow_terminal_parent=is_exempt(_via_internal_pipeline),
            expense_id=expense.id,
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

    def read_all(self) -> list[ExpenseLineItem]:
        """
        Read expense line items, scoped by UserProject for non-admin actors.

        Bill's equivalent (`BillLineItemService.read_all`) is scoped in the
        sproc, not via per-row `assert_can_access_*`. Match that shape: scoping
        happens in ReadExpenseLineItems. Every other read on this service
        already gates via `assert_can_access_expense`.
        """
        return self.repo.read_all(
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def read_by_id(self, id: int) -> Optional[ExpenseLineItem]:
        """
        Read an expense line item by ID.
        """
        line_item = self.repo.read_by_id(id)
        if line_item is None:
            return None
        assert_can_access_expense(line_item.expense_id)
        return line_item

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseLineItem]:
        """
        Read an expense line item by public ID.
        """
        line_item = self.repo.read_by_public_id(public_id)
        if line_item is None:
            return None
        assert_can_access_expense(line_item.expense_id)
        return line_item

    def read_by_expense_id(self, expense_id: int) -> list[ExpenseLineItem]:
        """
        Read all expense line items for a specific expense.
        """
        assert_can_access_expense(expense_id)
        return self.repo.read_by_expense_id(expense_id=expense_id)

    def read_by_qbo_identity(self, expense_id: int, qbo_id: str) -> Optional[ExpenseLineItem]:
        """
        Read an expense line item directly by its dbo-native QBO identity,
        scoped to its parent Expense (U-293b) — the line-level Phase-4 repoint
        seam, bypassing the qbo.PurchaseLine/qbo.PurchaseLineExpenseLineItem
        staging/mapping tables.
        """
        assert_can_access_expense(expense_id)
        return self.repo.read_by_qbo_identity(expense_id=expense_id, qbo_id=qbo_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        expense_public_id: str = None,
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
    ) -> Optional[ExpenseLineItem]:
        """
        Update an expense line item by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing is not None:
            self._assert_parent_editable(
                expense_id=getattr(existing, "expense_id", None),
                what="its line items cannot be changed",
                exempt=_via_internal_pipeline,
            )
            if expense_public_id is not None:
                self._assert_parent_editable(
                    expense_public_id=expense_public_id,
                    what="line items cannot be moved onto it",
                    exempt=_via_internal_pipeline,
                )
        if not existing:
            return None

        existing.row_version = row_version

        # Validate Expense exists if provided (using public_id)
        if expense_public_id is not None:
            expense = ExpenseService().read_by_public_id(public_id=expense_public_id)
            if not expense:
                raise ValueError(f"Expense with public_id '{expense_public_id}' not found.")
            existing.expense_id = expense.id

        # Validate SubCostCode exists if provided (or allow None to clear the relationship)
        if sub_cost_code_id is not None:
            sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
            if not sub_cost_code:
                raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")
            existing.sub_cost_code_id = sub_cost_code_id

        # Validate Project exists if provided (or allow None to clear the relationship)
        if project_public_id is not None:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            existing.project_id = project.id

        # Update fields
        if description is not None:
            existing.description = description
        if quantity is not None:
            # Coerced like its Rate/Amount/Markup/Price siblings below. This line
            # was a bare assignment: a float 5.25 arriving from an internal caller
            # was stored on the dataclass as a binary-approximate float and bound
            # straight to @Quantity DECIMAL(18,4). `is not None`, never truthiness
            # — Decimal(0) is falsy and 0 is a real quantity.
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
            self._reassert_after_a_lost_write(
                expense_id=getattr(existing, "expense_id", None),
                expense_public_id=expense_public_id,
                what="its line items cannot be changed",
                exempt=_via_internal_pipeline,
            )
            raise

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None, _via_internal_pipeline: bool = False) -> Optional[ExpenseLineItem]:
        """
        Delete an expense line item by public ID, cascading to its attachment.

        Process:
        1. Find the ExpenseLineItemAttachment link for this line item
        2. If found: delete the Attachment record + Azure blob, then delete the link
        3. Delete the ExpenseLineItem record
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing is not None:
            self._assert_parent_editable(
                expense_id=getattr(existing, "expense_id", None),
                what="its line items cannot be deleted",
                exempt=_via_internal_pipeline,
            )
        if not existing:
            return None

        # Step 1-2: Clean up attachment (1-1 relationship)
        try:
            from entities.expense_line_item_attachment.persistence.repo import ExpenseLineItemAttachmentRepository
            from entities.attachment.business.service import AttachmentService
            from shared.storage import AzureBlobStorage, AzureBlobStorageError

            attachment_link = ExpenseLineItemAttachmentRepository().read_by_expense_line_item_id(
                expense_line_item_id=existing.id
            )
            if attachment_link:
                attachment_service = AttachmentService()
                attachment = attachment_service.read_by_id(id=attachment_link.attachment_id) if attachment_link.attachment_id else None
                # U-446b (Codex round 5, P1). LINK, then ROW, then BLOB — this
                # cascade was missed when its four siblings were reordered.
                #
                # The link first because FK_ExpenseLineItemAttachment_Attachment
                # is NO ACTION, so the Attachment delete fails while it stands.
                # The blob last because the guarded row delete is what decides:
                # an attachment can be a completed Bill's evidence too (BLIA
                # multi-split linking), and destroying the bytes before asking
                # leaves that evidence pointing at nothing.
                try:
                    ExpenseLineItemAttachmentRepository().delete_by_id(
                        id=attachment_link.id,
                        allow_terminal_parent=is_exempt(_via_internal_pipeline),
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Could not delete expense line item attachment link %s: %s",
                        attachment_link.id, e,
                    )

                if attachment:
                    removed = None
                    try:
                        removed = attachment_service.delete_by_public_id(public_id=attachment.public_id)
                    except StatusLockedError:
                        import logging
                        logging.getLogger(__name__).info(
                            "Kept attachment %s: it is evidence for a completed Bill",
                            attachment.public_id,
                        )
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).warning(
                            "Could not delete attachment record %s: %s", attachment.id, e
                        )
                    if removed is not None and attachment.blob_url:
                        try:
                            AzureBlobStorage().delete_file(attachment.blob_url)
                        except Exception as e:
                            import logging
                            logging.getLogger(__name__).warning(
                                "Could not delete blob %s for attachment %s: %s",
                                attachment.blob_url, attachment.id, e,
                            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Error during attachment cleanup for ExpenseLineItem %s: %s", existing.id, e
            )

        # Step 3: Delete the line item (U-364: identity is dbo-native QboId/RealmId).
        deleted = self.repo.delete_by_id(
            existing.id, allow_terminal_parent=is_exempt(_via_internal_pipeline)
        )
        if deleted is None:
            self._reassert_after_a_lost_write(
                expense_id=getattr(existing, "expense_id", None),
                what="its line items cannot be deleted",
                exempt=_via_internal_pipeline,
            )
        return deleted
