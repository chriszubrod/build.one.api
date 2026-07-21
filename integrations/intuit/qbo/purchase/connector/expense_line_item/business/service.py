# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.model import PurchaseLineExpenseLineItem
from integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo import PurchaseLineExpenseLineItemRepository
from integrations.intuit.qbo.purchase.business.model import QboPurchaseLine
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.expense_line_item.business.model import ExpenseLineItem
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService

logger = logging.getLogger(__name__)


# Pure pull-side field decision, deliberately NOT connector state. The identical
# AccountBasedExpenseLineDetail gap exists in the Bill and VendorCredit line
# connectors (bill/connector/bill_line_item/, vendorcredit/connector/
# bill_credit_line_item/) — when the second caller arrives, lift this into
# integrations/intuit/qbo/base/ next to preserve_human_edited_ref rather than
# pasting a copy, the way the four customer-ref resolvers went. See TODO.md.
def default_amount_only_line(qty, unit_price, amount):
    """
    Amount-only QBO line (NO Qty AND NO UnitPrice, e.g. a Ramp
    AccountBasedExpenseLineDetail) -> quantity 1 at rate=amount, so
    quantity * rate == amount. Any line that carries either field is
    returned untouched — an explicit 0 is a real value, not a missing one.
    """
    if qty is None and unit_price is None and amount is not None:
        return Decimal("1"), Decimal(str(amount))
    return qty, unit_price


def preserve_stored_value(default_value, qbo_value, stored_value):
    """
    Decide what to send for a field the pull may default.

    Returns None — the "leave it alone" sentinel — when QBO omitted the field and
    the local row already carries a value, so a re-pull never overwrites a coding-
    queue backfill. Otherwise returns the (possibly defaulted) value to write.

    NB the None sentinel is honored by ExpenseLineItemService.update_by_public_id,
    which re-reads the row and only assigns fields that arrive non-None. Sending
    None rather than echoing `stored_value` back is deliberate: the service's read
    is fresher than ours, so a concurrent web edit between our read and the write
    is preserved instead of being clobbered with a stale value.
    """
    if qbo_value is None and stored_value is not None:
        return None
    return default_value


class PurchaseLineExpenseLineItemConnector:
    """
    Connector service for synchronization between QboPurchaseLine and ExpenseLineItem.
    """

    def __init__(
        self,
        mapping_repo: Optional[PurchaseLineExpenseLineItemRepository] = None,
        expense_line_item_service: Optional[ExpenseLineItemService] = None,
        item_sub_cost_code_repo: Optional[ItemSubCostCodeRepository] = None,
        qbo_item_repo: Optional[QboItemRepository] = None,
        customer_project_repo: Optional[CustomerProjectRepository] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
    ):
        """Initialize the PurchaseLineExpenseLineItemConnector."""
        self.mapping_repo = mapping_repo or PurchaseLineExpenseLineItemRepository()
        self.expense_line_item_service = expense_line_item_service or ExpenseLineItemService()
        self.item_sub_cost_code_repo = item_sub_cost_code_repo or ItemSubCostCodeRepository()
        # Per-sync caches: the same QBO item / customer ref appears on many lines.
        # Caching avoids 2 DB queries per line for each repeated value.
        self._sub_cost_code_cache: dict = {}  # qbo_item_ref_value -> sub_cost_code_id | None
        self._project_cache: dict = {}        # (realm_id, qbo_customer_ref_value) -> project_public_id | None
        self.qbo_item_repo = qbo_item_repo or QboItemRepository()
        self.customer_project_repo = customer_project_repo or CustomerProjectRepository()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()

    def sync_from_qbo_purchase_line(self, expense_id: int, expense_public_id: str, qbo_line: QboPurchaseLine, realm_id: Optional[str] = None) -> ExpenseLineItem:
        """
        Sync data from QboPurchaseLine to ExpenseLineItem module.

        Args:
            expense_id: Database ID of the Expense
            expense_public_id: Public ID of the Expense (passed in to avoid a per-line DB read)
            qbo_line: QboPurchaseLine record

        Returns:
            ExpenseLineItem: The synced ExpenseLineItem record
        """
        
        # Resolve sub_cost_code from item reference
        sub_cost_code_id = None
        if qbo_line.item_ref_value:
            sub_cost_code_id = self._get_sub_cost_code_id(qbo_line.item_ref_value)
        
        # Resolve project from customer reference
        project_public_id = None
        if qbo_line.customer_ref_value:
            project_public_id = self._get_project_public_id(qbo_line.customer_ref_value, realm_id)
        
        # Determine billable status
        is_billable = None
        is_billed = None
        if qbo_line.billable_status:
            if qbo_line.billable_status == "Billable":
                is_billable = True
                is_billed = False
            elif qbo_line.billable_status == "HasBeenBilled":
                is_billable = True
                is_billed = True
            elif qbo_line.billable_status == "NotBillable":
                is_billable = False
                is_billed = False
        
        # Calculate markup (convert from percentage to decimal if needed)
        markup = None
        if qbo_line.markup_percent is not None:
            # QBO stores markup as percentage (e.g., 10 for 10%), we store as decimal (e.g., 0.10)
            markup = Decimal(str(qbo_line.markup_percent)) / Decimal('100')

        # What this pull WOULD write if the local row carried nothing (U-098). A QBO
        # amount-only line (Ramp card spend on 58999) has no Qty/UnitPrice/MarkupInfo
        # at all, which used to persist as NULL quantity/rate/markup. Derived once
        # per line; the create and update paths below decide whether to apply them.
        default_qty, default_rate = default_amount_only_line(
            qbo_line.qty, qbo_line.unit_price, qbo_line.amount
        )
        default_markup = markup if markup is not None else Decimal("0")

        # Calculate price: amount * (1 + markup), or amount if no markup.
        # Unchanged by the markup default — amount * (1 + 0) == amount.
        price = None
        if qbo_line.amount is not None:
            amount_val = Decimal(str(qbo_line.amount))
            if markup is not None:
                price = amount_val * (Decimal('1') + markup)
            else:
                price = amount_val

        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_purchase_line_id(qbo_line.id)

        if not mapping:
            # Shape B fallback (task #17): content-fingerprint match when QBO
            # regenerates line IDs. Adopts an existing unmapped ExpenseLineItem
            # whose fields match this QBO line rather than creating a duplicate.
            orphan = self._find_and_match_by_fingerprint(
                expense_id=expense_id,
                description=qbo_line.description,
                amount=qbo_line.amount,
                qty=qbo_line.qty,
                rate=qbo_line.unit_price,
            )
            if orphan is not None:
                logger.info(
                    f"Adopting orphaned ExpenseLineItem {orphan.id} for QboPurchaseLine {qbo_line.id} "
                    f"via content fingerprint match"
                )
                try:
                    mapping = self.mapping_repo.create(
                        expense_line_item_id=int(orphan.id),
                        qbo_purchase_line_id=qbo_line.id,
                    )
                except Exception as error:
                    logger.warning(
                        f"Could not adopt orphaned ExpenseLineItem {orphan.id}: {error}"
                    )

        if mapping:
            # Found existing mapping - update the ExpenseLineItem
            line_item = self.expense_line_item_service.read_by_id(mapping.expense_line_item_id)
            if line_item:
                logger.debug(f"Updating existing ExpenseLineItem {line_item.id} from QboPurchaseLine {qbo_line.id}")

                # Defaults fill a hole, they never overwrite: a value QBO omitted but
                # the user later set (coding-queue backfill) survives the re-pull.
                update_qty = preserve_stored_value(default_qty, qbo_line.qty, line_item.quantity)
                update_rate = preserve_stored_value(default_rate, qbo_line.unit_price, line_item.rate)
                update_markup = preserve_stored_value(
                    default_markup, qbo_line.markup_percent, line_item.markup
                )

                line_item = self.expense_line_item_service.update_by_public_id(
                    line_item.public_id,
                    row_version=line_item.row_version,
                    sub_cost_code_id=sub_cost_code_id,
                    project_public_id=project_public_id,
                    description=qbo_line.description,
                    quantity=update_qty,
                    rate=update_rate,
                    amount=qbo_line.amount,
                    is_billable=is_billable,
                    is_billed=is_billed,
                    markup=update_markup,
                    price=price,
                    is_draft=False,
                )
                
                return line_item
            else:
                # Mapping exists but ExpenseLineItem not found - recreate
                logger.warning(f"Mapping exists but ExpenseLineItem {mapping.expense_line_item_id} not found. Creating new.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new ExpenseLineItem
        logger.debug(f"Creating new ExpenseLineItem from QboPurchaseLine {qbo_line.id}")
        line_item = self.expense_line_item_service.create(
            expense_public_id=expense_public_id,
            sub_cost_code_id=sub_cost_code_id,
            project_public_id=project_public_id,
            description=qbo_line.description,
            quantity=default_qty,
            rate=default_rate,
            amount=qbo_line.amount,
            is_billable=is_billable,
            is_billed=is_billed,
            markup=default_markup,
            price=price,
            is_draft=False,
        )
        
        # Create mapping — if this fails we must roll back the line item we just created,
        # otherwise the unmapped line item will be duplicated on every subsequent sync run.
        line_item_id = int(line_item.id) if isinstance(line_item.id, str) else line_item.id
        try:
            mapping = self.create_mapping(expense_line_item_id=line_item_id, qbo_purchase_line_id=qbo_line.id)
            logger.debug(f"Created mapping: ExpenseLineItem {line_item_id} <-> QboPurchaseLine {qbo_line.id}")
        except Exception as e:
            try:
                self.expense_line_item_service.delete_by_public_id(line_item.public_id)
                logger.warning(
                    f"Rolled back orphan ExpenseLineItem {line_item_id} after mapping failure "
                    f"for QboPurchaseLine {qbo_line.id}"
                )
            except Exception as del_e:
                logger.error(f"Could not delete orphan ExpenseLineItem {line_item_id}: {del_e}")
            raise ValueError(
                f"Failed to create PurchaseLineExpenseLineItem mapping for QboPurchaseLine {qbo_line.id}: {e}"
            ) from e
        
        return line_item

    def _get_sub_cost_code_id(self, qbo_item_ref_value: str) -> Optional[int]:
        """
        Get the SubCostCode ID from QBO item reference value.
        
        Args:
            qbo_item_ref_value: QBO item reference value (QBO Item ID)
        
        Returns:
            int: SubCostCode ID or None
        """
        if not qbo_item_ref_value:
            return None

        if qbo_item_ref_value in self._sub_cost_code_cache:
            return self._sub_cost_code_cache[qbo_item_ref_value]

        # First find the QboItem by qbo_id
        qbo_item = self.qbo_item_repo.read_by_qbo_id(qbo_item_ref_value)
        if not qbo_item:
            logger.warning(f"QboItem not found for qbo_id: {qbo_item_ref_value} — ExpenseLineItem will have no SubCostCode (billing gap)")
            self._sub_cost_code_cache[qbo_item_ref_value] = None
            return None

        # Then find the ItemSubCostCode mapping
        item_mapping = self.item_sub_cost_code_repo.read_by_qbo_item_id(qbo_item.id)
        if not item_mapping:
            logger.warning(f"ItemSubCostCode mapping not found for QboItem ID: {qbo_item.id} (QBO Item '{qbo_item_ref_value}') — ExpenseLineItem will have no SubCostCode (billing gap)")
            self._sub_cost_code_cache[qbo_item_ref_value] = None
            return None

        self._sub_cost_code_cache[qbo_item_ref_value] = item_mapping.sub_cost_code_id
        return item_mapping.sub_cost_code_id

    # One of FOUR near-identical QBO customer-ref -> Project resolvers (invoice /
    # purchase / vendorcredit / bill). All four are realm-scoped as of U-060; they
    # still diverge on heal (invoice only) and caching (invoice + purchase only).
    # Lift into one shared resolver when multi-realm lands — see TODO.md.
    def _get_project_public_id(self, qbo_customer_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """
        Get the Project public_id from QBO customer reference value.
        
        Args:
            qbo_customer_ref_value: QBO customer reference value (QBO Customer ID)
            realm_id: Optional QBO realm ID for realm-scoped customer lookup
        
        Returns:
            str: Project public_id or None
        """
        if not qbo_customer_ref_value:
            return None

        cache_key = (realm_id, qbo_customer_ref_value)

        if cache_key in self._project_cache:
            return self._project_cache[cache_key]

        # First find the QboCustomer by qbo_id
        if realm_id:
            qbo_customer = self.qbo_customer_repo.read_by_qbo_id_and_realm_id(qbo_customer_ref_value, realm_id)
        else:
            qbo_customer = self.qbo_customer_repo.read_by_qbo_id(qbo_customer_ref_value)
        if not qbo_customer:
            logger.debug(f"QboCustomer not found for qbo_id: {qbo_customer_ref_value}")
            self._project_cache[cache_key] = None
            return None

        # Then find the CustomerProject mapping
        customer_mapping = self.customer_project_repo.read_by_qbo_customer_id(qbo_customer.id)
        if not customer_mapping:
            logger.debug(f"CustomerProject mapping not found for QboCustomer ID: {qbo_customer.id}")
            self._project_cache[cache_key] = None
            return None

        # Get the Project
        project = ProjectService().read_by_id(customer_mapping.project_id)
        if not project:
            logger.debug(f"Project not found for ID: {customer_mapping.project_id}")
            self._project_cache[cache_key] = None
            return None

        self._project_cache[cache_key] = project.public_id
        return project.public_id

    def create_mapping(self, expense_line_item_id: int, qbo_purchase_line_id: int) -> PurchaseLineExpenseLineItem:
        """
        Create a mapping between ExpenseLineItem and QboPurchaseLine.
        
        Args:
            expense_line_item_id: Database ID of ExpenseLineItem record
            qbo_purchase_line_id: Database ID of QboPurchaseLine record
        
        Returns:
            PurchaseLineExpenseLineItem: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_line_item = self.mapping_repo.read_by_expense_line_item_id(expense_line_item_id)
        if existing_by_line_item:
            raise ValueError(
                f"ExpenseLineItem {expense_line_item_id} is already mapped to QboPurchaseLine {existing_by_line_item.qbo_purchase_line_id}"
            )
        
        existing_by_qbo_line = self.mapping_repo.read_by_qbo_purchase_line_id(qbo_purchase_line_id)
        if existing_by_qbo_line:
            raise ValueError(
                f"QboPurchaseLine {qbo_purchase_line_id} is already mapped to ExpenseLineItem {existing_by_qbo_line.expense_line_item_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(expense_line_item_id=expense_line_item_id, qbo_purchase_line_id=qbo_purchase_line_id)

    # ------------------------------------------------------------------ #
    # Shape B line-matching helpers (task #17)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_for_fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            pass
        return str(value).strip()

    def _fingerprint_tuple(self, description, amount, qty, rate):
        return (
            self._normalize_for_fingerprint(description),
            self._normalize_for_fingerprint(amount),
            self._normalize_for_fingerprint(qty),
            self._normalize_for_fingerprint(rate),
        )

    def _find_and_match_by_fingerprint(
        self,
        *,
        expense_id: int,
        description,
        amount,
        qty,
        rate,
    ):
        """
        Find an unmapped ExpenseLineItem whose content fingerprint matches,
        POSITION-AWARE.

        Two tiers preserve pre-patch adoption while rescuing legacy NULL rows:
        - Tier 1 (exact): raw (description, amount, qty, rate) on target vs
          (description, amount, quantity, rate) on each candidate — no defaulting.
        - Tier 2 (normalized fallback): only if tier 1 found nothing, compare
          default_amount_only_line on both sides.

        Invariant: any row the pre-patch matcher would adopt is still adopted
        (tier 1); normalization only adds matches where tier 1 would find none.

        Tier 2 is a LEGACY-ROW SHIM, not a permanent feature: it exists only because
        rows created before U-098 stored NULL quantity/rate where the pull now stores
        1 x amount. Retire it (and collapse back to one tier) once no unmapped
        ExpenseLineItem on a QBO-sourced Expense still has NULL quantity/rate —
        otherwise it will be carried along into any future shared-matcher extraction.

        When several unmapped lines share a fingerprint within a tier, return the
        FIRST in stable position order (by id ≈ creation ≈ LineNum). The caller
        consumes it (creates a mapping) before the next QBO line, so processing lines
        in order pairs identical-content lines 1:1 by position — robust to QBO line-id
        regeneration even with duplicate content, instead of bailing and duplicating.
        Returns None only when nothing matches in either tier.
        """
        existing = self.expense_line_item_service.read_by_expense_id(expense_id=expense_id)
        unmapped = [
            li for li in sorted(existing, key=lambda c: int(getattr(c, "id", 0) or 0))
            if not self.mapping_repo.read_by_expense_line_item_id(int(li.id))
        ]

        def matches_for(tier_target, defaulted: bool):
            """Candidates whose fingerprint equals tier_target, in position order."""
            found = []
            for candidate in unmapped:
                cand_amount = getattr(candidate, "amount", None)
                cand_qty = getattr(candidate, "quantity", None)
                cand_rate = getattr(candidate, "rate", None)
                if defaulted:
                    cand_qty, cand_rate = default_amount_only_line(cand_qty, cand_rate, cand_amount)
                candidate_fp = self._fingerprint_tuple(
                    getattr(candidate, "description", None), cand_amount, cand_qty, cand_rate
                )
                if candidate_fp == tier_target:
                    found.append(candidate)
            return found

        def adopt(matches, tier_label):
            if len(matches) > 1:
                logger.info(
                    f"{len(matches)} unmapped ExpenseLineItems share the tier-{tier_label} "
                    f"fingerprint; adopting the first by position (QBO line-id regeneration)"
                )
            return matches[0]

        # Tier 1 — raw, exactly as the pre-patch matcher compared. Runs alone whenever
        # it hits, so the normalized pass costs nothing on the common path.
        exact = matches_for(self._fingerprint_tuple(description, amount, qty, rate), defaulted=False)
        if exact:
            return adopt(exact, "exact")

        # Tier 2 — legacy-row rescue only (see docstring).
        norm_qty, norm_rate = default_amount_only_line(qty, rate, amount)
        normalized = matches_for(
            self._fingerprint_tuple(description, amount, norm_qty, norm_rate), defaulted=True
        )
        if normalized:
            return adopt(normalized, "normalized")
        return None

    def get_mapping_by_expense_line_item_id(self, expense_line_item_id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Get mapping by ExpenseLineItem ID.
        """
        return self.mapping_repo.read_by_expense_line_item_id(expense_line_item_id)

    def get_mapping_by_qbo_purchase_line_id(self, qbo_purchase_line_id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Get mapping by QboPurchaseLine ID.
        """
        return self.mapping_repo.read_by_qbo_purchase_line_id(qbo_purchase_line_id)
