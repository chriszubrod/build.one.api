# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCreditLine
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.persistence.repo import VendorCreditLineItemBillCreditLineItemMappingRepository
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.bill_credit_line_item.business.model import BillCreditLineItem
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService

logger = logging.getLogger(__name__)


class VendorCreditLineItemConnector:
    """Connector for syncing QBO VendorCredit lines to BillCreditLineItems."""

    def __init__(self):
        self.bill_credit_line_item_service = BillCreditLineItemService()
        self.project_service = ProjectService()
        self.sub_cost_code_service = SubCostCodeService()
        self.mapping_repo = VendorCreditLineItemBillCreditLineItemMappingRepository()

    def sync_from_qbo_line(
        self,
        bill_credit_id: int,
        bill_credit_public_id: str,
        qbo_line: QboVendorCreditLine,
    ) -> Optional[BillCreditLineItem]:
        """
        Upsert a QBO VendorCredit line into a BillCreditLineItem.

        Matches an existing BillCreditLineItem via the (now-stable)
        VendorCreditLineItemBillCreditLineItem mapping keyed on qbo_line.id; if the
        mapping is missing (e.g. QBO regenerated the line id on an edit) it falls
        back to a content fingerprint to adopt the orphaned local line instead of
        duplicating it. Updates in place when matched, creates otherwise.
        Prefers ItemBasedExpenseLineDetail when available.
        """
        try:
            # Resolve project from CustomerRef (if billable)
            project_public_id = None
            if qbo_line.customer_ref_value:
                project_public_id = self._get_project_public_id(qbo_line.customer_ref_value)

            # Resolve sub_cost_code from ItemRef
            sub_cost_code_id = None
            if qbo_line.item_ref_value:
                sub_cost_code_id = self._get_sub_cost_code_id(qbo_line.item_ref_value)

            # Determine billable and billed status from QBO BillableStatus.
            # Leave both None when QBO omits the status so the in-place UPDATE
            # PRESERVES the local value instead of regressing an already-billed line
            # back to not-billed on a re-pull (mirrors the Bill connector).
            # "Billable" = not yet invoiced, "HasBeenBilled" = already invoiced, "NotBillable" = not billable
            is_billable = None
            is_billed = None
            if qbo_line.billable_status:
                is_billable = qbo_line.billable_status in ("Billable", "HasBeenBilled")
                is_billed = qbo_line.billable_status == "HasBeenBilled"

            # Calculate billable amount (same as amount if billable)
            billable_amount = qbo_line.amount if is_billable else None

            # --- Find an existing BillCreditLineItem to update in place ---
            existing = None
            mapping = self.mapping_repo.read_by_qbo_line_id(qbo_line.id) if qbo_line.id else None
            if mapping and mapping.bill_credit_line_item_id:
                existing = self.bill_credit_line_item_service.read_by_id(mapping.bill_credit_line_item_id)
                if not existing:
                    # Dangling mapping (line deleted out from under it) — drop it.
                    try:
                        self.mapping_repo.delete_by_id(mapping.id)
                    except Exception as e:
                        logger.warning(f"Could not delete dangling line mapping {mapping.id}: {e}")
                    mapping = None

            if existing is None and bill_credit_id and qbo_line.id:
                # Fingerprint fallback: adopt an unmapped local line with matching
                # content (QBO regenerated the line id) rather than duplicating.
                orphan = self._match_unmapped_by_fingerprint(bill_credit_id, qbo_line)
                if orphan is not None:
                    try:
                        self.mapping_repo.create(
                            qbo_vendor_credit_line_id=qbo_line.id,
                            bill_credit_line_item_id=orphan.id,
                        )
                        existing = orphan
                        logger.info(
                            f"Adopted orphaned BillCreditLineItem {orphan.id} for "
                            f"QboVendorCreditLine {qbo_line.id} via content fingerprint"
                        )
                    except Exception as e:
                        logger.warning(f"Could not adopt orphaned BillCreditLineItem {orphan.id}: {e}")

            if existing is not None:
                return self.bill_credit_line_item_service.update_by_public_id(
                    existing.public_id,
                    row_version=existing.row_version,
                    sub_cost_code_id=sub_cost_code_id,
                    project_public_id=project_public_id,
                    description=qbo_line.description,
                    quantity=qbo_line.qty,
                    unit_price=qbo_line.unit_price,
                    amount=qbo_line.amount,
                    is_billable=is_billable,
                    is_billed=is_billed,
                    billable_amount=billable_amount,
                    is_draft=False,
                )

            # --- No match: create a new line item + mapping ---
            line_item = self.bill_credit_line_item_service.create(
                bill_credit_public_id=bill_credit_public_id,
                sub_cost_code_id=sub_cost_code_id,
                project_public_id=project_public_id,
                description=qbo_line.description,
                quantity=qbo_line.qty,
                unit_price=qbo_line.unit_price,
                amount=qbo_line.amount,
                is_billable=is_billable,
                is_billed=is_billed,
                billable_amount=billable_amount,
                is_draft=False,
            )

            # Create VendorCreditLine <-> BillCreditLineItem mapping so that
            # LinkedTxn references can be resolved when syncing invoices to QBO.
            # qbo_line.id is the stable local PK of the QboVendorCreditLine record
            # (the snapshot layer now upserts lines in place).
            if line_item and qbo_line.id:
                try:
                    self.mapping_repo.create(
                        qbo_vendor_credit_line_id=qbo_line.id,
                        bill_credit_line_item_id=line_item.id,
                    )
                except Exception as mapping_err:
                    logger.warning(
                        f"Created BillCreditLineItem {line_item.id} but could not create "
                        f"VendorCreditLineItemBillCreditLineItem mapping: {mapping_err}"
                    )

            return line_item

        except Exception as e:
            logger.error(f"Error syncing QBO line {qbo_line.qbo_line_id}: {e}")
            return None

    @staticmethod
    def _fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison (10 == 10.00)."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            return str(value).strip()

    def _match_unmapped_by_fingerprint(self, bill_credit_id: int, qbo_line: QboVendorCreditLine):
        """
        Find at most one unmapped BillCreditLineItem on this credit whose
        (description, amount, qty, unit_price) matches the QBO line. The full
        4-field key (parity with Bill's _match_by_fingerprint) keeps distinct lines
        that merely share description+amount distinguishable, so they're adopted in
        place rather than duplicated. Returns None on zero or multiple matches
        (ambiguous → create new rather than adopt the wrong one).
        """
        existing = self.bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id)
        target = (
            self._fingerprint(qbo_line.description), self._fingerprint(qbo_line.amount),
            self._fingerprint(qbo_line.qty), self._fingerprint(qbo_line.unit_price),
        )
        matches = [
            li for li in existing
            if not self.mapping_repo.read_by_bill_credit_line_item_id(li.id)
            and (
                self._fingerprint(li.description), self._fingerprint(li.amount),
                self._fingerprint(li.quantity), self._fingerprint(li.unit_price),
            ) == target
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.info(
                f"Fingerprint match ambiguous for QboVendorCreditLine {qbo_line.id}: "
                f"{len(matches)} unmapped lines share (description, amount); creating new"
            )
        return None

    def _get_project_public_id(self, qbo_customer_ref_value: str) -> Optional[str]:
        """Resolve QBO customer ref to local project public_id (QboCustomer by qbo_id -> CustomerProject by qbo_customer_id)."""
        try:
            from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
            from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository

            qbo_customer_repo = QboCustomerRepository()
            customer_project_repo = CustomerProjectRepository()
            qbo_customer = qbo_customer_repo.read_by_qbo_id(qbo_customer_ref_value)
            if not qbo_customer:
                return None
            mapping = customer_project_repo.read_by_qbo_customer_id(qbo_customer.id)
            if not mapping or not mapping.project_id:
                return None
            project = self.project_service.read_by_id(id=str(mapping.project_id))
            return project.public_id if project else None
        except Exception as e:
            logger.warning(f"Error resolving customer ref {qbo_customer_ref_value}: {e}")
            return None

    def _get_sub_cost_code_id(self, qbo_item_ref_value: str) -> Optional[int]:
        """Resolve QBO item ref to local sub_cost_code_id (QboItem by qbo_id -> ItemSubCostCode by qbo_item_id)."""
        try:
            from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
            from integrations.intuit.qbo.item.persistence.repo import QboItemRepository

            qbo_item_repo = QboItemRepository()
            item_scc_repo = ItemSubCostCodeRepository()
            qbo_item = qbo_item_repo.read_by_qbo_id(qbo_item_ref_value)
            if not qbo_item:
                return None
            mapping = item_scc_repo.read_by_qbo_item_id(qbo_item.id)
            return mapping.sub_cost_code_id if mapping else None
        except Exception as e:
            logger.warning(f"Error resolving item ref {qbo_item_ref_value}: {e}")
            return None
