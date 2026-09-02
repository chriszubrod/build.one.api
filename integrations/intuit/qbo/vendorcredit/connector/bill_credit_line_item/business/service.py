# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCreditLine
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.bill_credit_line_item.business.model import BillCreditLineItem
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.identity_drift import stamp_line_identity_or_warn
from integrations.intuit.qbo.base.identity_fastpath import run_line_identity_fastpath_dbo_only
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.base.cost_code_resolver import resolve_dbo_sub_cost_code
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)


class VendorCreditLineItemConnector:
    """Connector for syncing QBO VendorCredit lines to BillCreditLineItems."""

    def __init__(self, reconciliation_repo: Optional[ReconciliationIssueRepository] = None):
        self.bill_credit_line_item_service = BillCreditLineItemService()
        self.project_service = ProjectService()
        self.sub_cost_code_service = SubCostCodeService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # Run-scoped memo (U-229) — same shape as the sibling PurchaseLineExpenseLineItemConnector's
        # _sub_cost_code_cache (purchase/connector/expense_line_item/business/service.py), which
        # already ships this exact pattern in prod: keyed by qbo_item_ref_value, caches hits AND
        # misses, never invalidated for the life of this instance. Accepted tradeoff, not a bug: a
        # mid-run repoint or delete of the underlying ItemSubCostCode/SubCostCode (a rare,
        # permission-gated admin action) can feed a stale id to a later line in the same run. That
        # fails safe — a stale id pointing at a deleted SubCostCode trips the BillCreditLineItem FK,
        # which the per-line try/except in _sync_line_items turns into a per-credit skip that
        # retries next run, never partial/corrupt data; a stale id pointing at a repointed-but-
        # still-valid SubCostCode at worst misattributes one line's cost coding until the next pull.
        self._sub_cost_code_cache: dict = {}
        # Run-scoped memo (U-278) — same shape/tradeoffs as _sub_cost_code_cache above,
        # keyed by (qbo_customer_ref_value, realm_id). A mid-run repoint of the
        # underlying Project (rare, permission-gated) can feed a stale public_id to a
        # later line in the same run; that fails safe the same way (an FK/access check
        # downstream turns it into a per-credit skip that retries next run).
        self._project_public_id_cache: dict = {}

    def sync_from_qbo_line(
        self,
        bill_credit_id: int,
        bill_credit_public_id: str,
        qbo_line: QboVendorCreditLine,
        realm_id: Optional[str] = None,
    ) -> BillCreditLineItem:
        """
        Upsert a QBO VendorCredit line into a BillCreditLineItem.

        U-361: dbo.BillCreditLineItem.QboId/RealmId (U-238b), scoped to this
        line's own parent BillCredit, is the SOLE identity store — the
        qbo.VendorCreditLineItemBillCreditLineItem mapping table is retired
        (U-349 program family 8/11, the first line-item family). Resolution runs
        through the shared `run_line_identity_fastpath_dbo_only` primitive
        (base/identity_fastpath.py — see its docstring for the create lock, the
        create-only MISS, and the stamp-rollback guarantee this connector relies
        on rather than re-implements):

          * HIT — a dbo row already carries `(bill_credit_id, qbo_line_id)`:
            write the QBO-derived fields onto it in place. No identity re-stamp
            (the row was found BY that identity; re-stamping every touch was the
            mapping era's dual-write), except a one-off realm self-heal for a
            legacy row stamped with a QboId but no RealmId — the pruned
            `backfill_qbo_identity_lines.py --mode realm-only` was the only other
            tool that could repair such a row for this family.
          * MISS — create the line, then stamp identity with the bare
            `set_qbo_identity`. The U-341 `create_mapping_then_stamp` /
            `stamp_line_identity_or_warn` wrappers this path used to run guarded
            the MAPPING write, which no longer exists; a best-effort stamp is no
            longer acceptable either, because an unstamped line has no mapping
            row left to make it findable on the next pull (it would be
            re-created as a duplicate every pull) — so a stamp that raises or
            does not land is rolled back by the helper and re-raised.
          * No content-fingerprint adopt (design §4, create-only). The pre-U-361
            adopt re-bound "unmapped" local lines, a state a dbo-only row cannot
            be in once stamped, so it has no dbo-only translation: a QBO line-id
            regeneration now creates a sibling row instead of adopting the
            stamped orphan.

        Raises on any projection failure; the parent fails the whole credit so
        the watermark holds and it retries.
        """
        if not qbo_line.qbo_line_id:
            # Without a QBO Line.Id there is no dbo-native identity to resolve or
            # stamp, and no mapping row keyed on the staging PK to make an
            # unstamped line findable next pull — creating one would duplicate it
            # on every re-pull. QBO always assigns Line.Id on a persisted
            # transaction, so this is a fail-closed guard, not a path.
            raise ValueError(
                f"QboVendorCreditLine {qbo_line.id} on BillCredit {bill_credit_id} has no "
                f"QBO Line.Id - cannot resolve or stamp dbo-native line identity; skipping."
            )

        # Resolve project from CustomerRef (if billable)
        project_public_id = None
        if qbo_line.customer_ref_value:
            project_public_id = self._get_project_public_id(qbo_line.customer_ref_value, realm_id)

        # Resolve sub_cost_code from ItemRef
        sub_cost_code_id = None
        if qbo_line.item_ref_value:
            sub_cost_code_id = self._get_sub_cost_code_id(qbo_line.item_ref_value, realm_id)

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

        def _apply_line_fields(direct: BillCreditLineItem) -> Optional[BillCreditLineItem]:
            """
            `apply_fields` for the HIT branch: write the QBO-derived fields onto
            the dbo-identity-matched BillCreditLineItem. Returns None on a
            concurrent-DELETE `update_by_public_id` miss (code-review finding,
            2026-09-01: `update_by_id` RAISES `DatabaseConcurrencyError` on an
            actual ROWVERSION mismatch — it never returns None for that case;
            None here means `update_by_public_id`'s own pre-check found the row
            already gone). Either way the helper's own `_apply()` raises
            `raise_concurrent_write_race` unconditionally on a None return, so
            that ONE raise stays the single place the guarantee lives (mirrors
            the header connector's `_apply_bill_credit_fields_and_sync`); a true
            ROWVERSION conflict instead propagates `DatabaseConcurrencyError`
            straight out of this closure, still caught by `_sync_line_items`'s
            generic handler and still failing/retrying the credit the same way.
            """
            updated = self.bill_credit_line_item_service.update_by_public_id(
                direct.public_id,
                row_version=direct.row_version,
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
            if updated is None:
                logger.error(
                    f"Failed to update BillCreditLineItem {direct.id} from "
                    f"QboVendorCreditLine {qbo_line.id} - update_by_public_id returned None "
                    f"(concurrent delete: the row was gone by the time the update ran)"
                )
                return None
            if realm_id and not getattr(direct, "realm_id", None):
                # Legacy realm gap (U-293-dw): the row was found by its QboId but
                # never got the RealmId half of the atomic pair. Heal it once,
                # best-effort — a failure here must not fail the line (the row
                # is still correctly identified by (BillCreditId, QboId)).
                stamp_line_identity_or_warn(
                    self.bill_credit_line_item_service.repo,
                    id=coerce_id(updated.id),
                    qbo_id=qbo_line.qbo_line_id,
                    realm_id=realm_id,
                    context=f"Updated BillCreditLineItem {updated.id} (realm self-heal)",
                    enforce_realm_pairing=True,
                )
            return updated

        def _create_line() -> Optional[BillCreditLineItem]:
            """
            `resolve_candidate` for the MISS branch: create the line fresh (no
            adopt — see the method docstring). Fails closed BEFORE creating when
            the realm needed for the stamp is missing: SetBillCreditLineItemQbo
            Identity's own atomic-pair guard declines to write a QboId without a
            RealmId, so the line would come out unstamped — which post-U-361
            means unfindable, i.e. re-created as a duplicate on every pull. The
            helper would catch the un-landed stamp and roll the row back anyway;
            refusing up front just saves the create+delete round trip.
            """
            if not realm_id:
                raise RuntimeError(
                    f"Refusing to create BillCreditLineItem for QboVendorCreditLine "
                    f"{qbo_line.qbo_line_id} on BillCredit {bill_credit_id}: realm_id is "
                    f"missing, so its dbo-native identity stamp could not land and the line "
                    f"would be an unfindable orphan. Holding for retry."
                )
            return self.bill_credit_line_item_service.create(
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

        def _stamp_line_identity(candidate: BillCreditLineItem) -> Optional[BillCreditLineItem]:
            """
            `stamp_identity` for the MISS branch: the bare dbo-native stamp, then
            a re-read — `set_qbo_identity` is a void DB write that never mutates
            `candidate` in memory, and the helper verifies the returned row
            actually carries the identity (its "stamp did not land" guard).
            """
            self.bill_credit_line_item_service.repo.set_qbo_identity(
                id=coerce_id(candidate.id), qbo_id=qbo_line.qbo_line_id, realm_id=realm_id,
            )
            return self.bill_credit_line_item_service.read_by_id(coerce_id(candidate.id))

        def _rollback_line(candidate: BillCreditLineItem) -> None:
            """
            `rollback_candidate` for the MISS branch (U-354/U-355's identity-
            stamp rollback, at line level): best-effort delete of the just-
            created, unstamped line. `rollback_orphan_header` is the shared
            compensating-delete mechanism (it is not header-specific beyond its
            name — same isolate-each-delete, never-raise, record-on-final-
            failure shape); `delete_mapping` is a no-op because there is no
            mapping row. A failed delete leaves an unstamped orphan that inflates
            this credit's local lines on every future pull, so it is recorded to
            reconciliation monitoring exactly as the header connector records
            its own orphan header.
            """
            rollback_orphan_header(
                delete_header=lambda: self.bill_credit_line_item_service.delete_by_public_id(
                    candidate.public_id
                ),
                delete_mapping=lambda: None,
                entity_label="BillCreditLineItem",
                entity_id=candidate.id,
                on_header_delete_failed=lambda exc: self._record_orphan_line_issue(
                    line_item=candidate, qbo_line=qbo_line, bill_credit_id=bill_credit_id,
                    realm_id=realm_id, exc=exc,
                ),
            )

        outcome = run_line_identity_fastpath_dbo_only(
            parent_local_id=bill_credit_id,
            qbo_line_id=qbo_line.qbo_line_id,
            entity_label="BillCreditLineItem",
            external_label="QboVendorCreditLine",
            lock_resource_label="BillCreditLineItem",
            read_direct_by_parent_and_qbo_line_id=self.bill_credit_line_item_service.read_by_qbo_identity,
            resolve_candidate=_create_line,
            stamp_identity=_stamp_line_identity,
            rollback_candidate=_rollback_line,
            apply_fields=_apply_line_fields,
        )
        # qbo_line_id is guaranteed truthy above, so the helper's only hit=False
        # outcome is unreachable here and hit=True never carries entity=None.
        return outcome.entity

    def _record_orphan_line_issue(
        self,
        *,
        line_item: BillCreditLineItem,
        qbo_line: QboVendorCreditLine,
        bill_credit_id: int,
        realm_id: Optional[str],
        exc: Exception,
    ) -> None:
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphan_bcli_line_item",
            entity_type="BillCreditLineItem",
            entity_public_id=str(line_item.public_id) if getattr(line_item, "public_id", None) else None,
            qbo_id=str(qbo_line.qbo_line_id) if qbo_line.qbo_line_id else None,
            realm_id=realm_id or "",
            details=(
                f"Compensating rollback failed to delete unstamped BillCreditLineItem "
                f"{line_item.id} ({getattr(line_item, 'public_id', None)}) on BillCredit "
                f"{bill_credit_id} after its identity stamp for QboVendorCreditLine "
                f"{qbo_line.qbo_line_id} failed: {exc}. The orphan is invisible to the "
                f"dbo-native fast path, so every re-pull will mint a duplicate line until "
                f"it is deleted or stamped by hand."
            ),
        )

    # One of FOUR near-identical QBO customer-ref -> Project resolvers (invoice /
    # purchase / vendorcredit / bill). All four are realm-scoped as of U-060; they
    # still diverge on heal (invoice only) and caching (invoice + purchase only).
    # Lift into one shared resolver when multi-realm lands — see TODO.md.
    def _get_project_public_id(self, qbo_customer_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """Resolve QBO customer ref to local project public_id, memoized for this
        connector's lifetime (U-278) — same shape as `_get_sub_cost_code_id` below.
        A VendorCredit's lines commonly repeat the same CustomerRef (one project,
        several cost-coded lines); without this, each line paid its own
        ReadProjectByQboIdAndRealmId round trip for an identical key."""
        if not qbo_customer_ref_value:
            return None
        key = (qbo_customer_ref_value, realm_id)
        if key in self._project_public_id_cache:
            return self._project_public_id_cache[key]
        result = self._resolve_project_public_id(qbo_customer_ref_value, realm_id)
        self._project_public_id_cache[key] = result
        return result

    def _resolve_project_public_id(
        self, qbo_customer_ref_value: str, realm_id: Optional[str] = None
    ) -> Optional[str]:
        """Uncached resolution.

        U-278 (Phase-4 pull-resolver repoint, deferred from U-276 §10): tries the direct
        dbo-native lookup first — `ProjectService.read_by_qbo_identity` (built by U-276)
        matches `dbo.Project.QboId`/`.RealmId` against this CustomerRef value directly,
        with no `qbo.Customer`/`qbo.CustomerProject` hop. Every Project synced even once
        since U-276 already carries this identity.

        U-311 (Wave-5, scope expansion — this resolver was missed by
        `docs/design/wave5.md` §4's own consumer sweep; found + fixed in-unit): a direct
        hit is now verified via `verify_identity_dbo_only` before being trusted — this
        resolver previously trusted a direct hit UNCONDITIONALLY, unlike its 3 near-
        identical siblings (bill/bill_line_item/expense_line_item), which all verify —
        closing that pre-existing gap as a natural side effect of retiring the legacy
        hop below (the mapping table this verify step reads was already live; only the
        *check* against it was missing here). The legacy QboCustomer-by-qbo_id ->
        CustomerProject-by-qbo_customer_id mapping-table hop is deleted outright — Wave 5
        retires that table, so there is no fallback data source left; per
        `docs/design/wave5.md` §2's "consequence worth flagging," a miss/refusal now
        resolves to None (line syncs without a Project binding) rather than degrading to
        the legacy hop, measured as a no-op today (0 dbo<->mapping disagreements live).
        """
        from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only

        direct = self.project_service.read_by_qbo_identity(qbo_customer_ref_value, realm_id)
        if direct:
            verified_qbo_id = verify_identity_dbo_only(
                direct, read_direct_by_qbo_identity=self.project_service.read_by_qbo_identity,
            )
            if verified_qbo_id:
                return direct.public_id
        return None

    def _get_sub_cost_code_id(self, qbo_item_ref_value: str, realm_id: Optional[str] = None) -> Optional[int]:
        """Resolve QBO item ref to local sub_cost_code_id, memoized for this connector's
        lifetime. dbo-native SubCostCode.QboId (U-307a; U-307d retired the legacy
        qbo.Item -> qbo.ItemSubCostCode hop) — see cost_code_resolver.py."""
        if not qbo_item_ref_value:
            return None
        cache_key = (realm_id, qbo_item_ref_value)
        if cache_key in self._sub_cost_code_cache:
            return self._sub_cost_code_cache[cache_key]
        result = self._resolve_sub_cost_code_id(qbo_item_ref_value, realm_id)
        self._sub_cost_code_cache[cache_key] = result
        return result

    def _resolve_sub_cost_code_id(self, qbo_item_ref_value: str, realm_id: Optional[str] = None) -> Optional[int]:
        """Uncached resolution via the shared cost-code resolver."""
        sub_cost_code = resolve_dbo_sub_cost_code(
            qbo_item_ref_value,
            realm_id,
            sub_cost_code_service=self.sub_cost_code_service,
        )
        if not sub_cost_code:
            logger.warning(
                f"No SubCostCode resolved for QBO Item ref '{qbo_item_ref_value}' — "
                f"BillCreditLineItem will have no SubCostCode (billing gap)"
            )
            return None
        return sub_cost_code.id
