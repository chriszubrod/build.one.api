# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_fastpath import run_identity_fastpath_dbo_only
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.term.business.model import QboTerm
from entities.payment_term.business.service import PaymentTermService
from entities.payment_term.business.model import PaymentTerm

logger = logging.getLogger(__name__)


class TermPaymentTermConnector:
    """
    Connector service for synchronization between QboTerm and PaymentTerm modules.

    U-352: dbo-only identity resolution via `run_identity_fastpath_dbo_only` — no
    `qbo.TermPaymentTerm` mapping-table read/write of any kind (the 3rd family of
    the U-349 program, mirroring U-350's `CompanyInfoCompanyConnector` pattern-setter,
    per Wave 5's "trust dbo alone" plan, `docs/design/u349-qbo-mapping-table-
    retirement.md`; U-351's `PhysicalAddressAddressConnector` is the same program's
    2nd family but had not yet shipped as of this unit). `dbo.PaymentTerm.QboId`/
    `RealmId` (U-238c/U-282) is the sole identity store; dbo.PaymentTerm's own
    filtered unique index (`UQ_PaymentTerm_QboId_RealmId`) plus
    `SetPaymentTermQboIdentity`'s theft-clear UPDATE guarantee at most one row holds a
    given identity at any instant, so a direct hit needs no cross-check and the old
    legacy Step-1/Step-2 mapping-table branch structure (driven by a second,
    independently-writable mapping table) no longer has anything to drift from.

    Three things make this family NOT a pure clone of U-350:
      * PaymentTerm carries a `QboActive` mirror column (U-275) that Company does
        not. This connector deliberately does NOT refresh it on a fast-path HIT
        (a pre-existing staleness tradeoff, U-282) — only the MISS/create path
        threads `active=` through, exactly as the pre-U-352 legacy CREATE path did.
      * PaymentTerm's create path has never had a by-name adopt step (unlike
        Company/SubCostCode) — `_resolve_payment_term_candidate` always creates a
        fresh row; this migration preserves that rather than importing the sibling
        families' adopt-by-name behavior.
      * Because there is no adopt step, `_stamp_payment_term_identity` stamps
        identity with a direct `set_qbo_identity` call rather than the shared
        `stamp_dbo_identity_with_lock` helper Company uses: that helper's
        candidate-scoped lock + theft-guard exists to protect an ADOPTED,
        pre-existing row from a side-channel (by-name) collision — a scenario that
        cannot arise here, since every candidate is a row this call just created
        with no side-channel key any other syncer could use to find it.

    Field Mapping:
        QboTerm.name <-> PaymentTerm.name
        QboTerm.type/due_days/day_of_month_due -> PaymentTerm.description (derived)
        QboTerm.discount_percent <-> PaymentTerm.discount_percent
        QboTerm.discount_days <-> PaymentTerm.discount_days
        QboTerm.due_days <-> PaymentTerm.due_days
    """

    def __init__(
        self,
        payment_term_service: Optional[PaymentTermService] = None,
    ):
        """Initialize the TermPaymentTermConnector."""
        self.payment_term_service = payment_term_service or PaymentTermService()

    def sync_from_qbo_term(self, qbo_term: QboTerm) -> Optional[PaymentTerm]:
        """
        Sync data from QboTerm to PaymentTerm module, via the dbo-only identity fast
        path (U-352).

        No connector-level realm fallback here (unlike CompanyInfoCompanyConnector's
        U-277 fallback) — `sync_from_qbo_term` has never taken a separate realm
        parameter; realm comes straight from the staging row.

        Args:
            qbo_term: QboTerm record

        Returns:
            PaymentTerm: The synced PaymentTerm record
        """
        term_name = qbo_term.name or ""

        description_parts = []
        if qbo_term.type:
            description_parts.append(f"Type: {qbo_term.type}")
        if qbo_term.due_days is not None:
            description_parts.append(f"Due in {qbo_term.due_days} days")
        if qbo_term.day_of_month_due is not None:
            description_parts.append(f"Due on day {qbo_term.day_of_month_due} of month")
        description = "; ".join(description_parts) if description_parts else None

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_term.qbo_id,
            realm_id=qbo_term.realm_id,
            entity_label="PaymentTerm",
            external_label="QboTerm",
            lock_resource_label="PaymentTerm",
            read_direct_by_qbo_identity=self.payment_term_service.read_by_qbo_identity,
            apply_fields=lambda entity: self._apply_payment_term_fields_and_sync(
                entity, qbo_term=qbo_term, term_name=term_name, description=description,
            ),
            resolve_candidate=lambda: self._resolve_payment_term_candidate(
                qbo_term, term_name=term_name, description=description,
            ),
            stamp_identity=lambda candidate: self._stamp_payment_term_identity(candidate, qbo_term),
        )
        if outcome.entity is None:
            # No longer race-reachable in practice (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a directly
            # invoked falsy qbo_term.qbo_id, mirroring every sibling connector's
            # identical guard (U-350/U-310/U-313/U-311).
            raise RuntimeError(
                f"Failed to resolve PaymentTerm for QboTerm {qbo_term.id} "
                f"(qbo_id={qbo_term.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _apply_payment_term_fields_and_sync(
        self,
        payment_term: PaymentTerm,
        *,
        qbo_term: QboTerm,
        term_name: str,
        description: Optional[str],
    ) -> Optional[PaymentTerm]:
        """
        `apply_fields` for the dbo-only fast path's HIT branch (U-352): write the
        QboTerm-derived fields onto an existing dbo-identity-matched PaymentTerm and
        persist. `name` is preserved via `preserve_human_edited_name` (PaymentTerm.Name
        is `NOT NULL` and the one field this connector has always guarded against a
        human rename being clobbered on re-pull) — every other field is QBO
        source-of-truth, unconditionally overwritten, matching the pre-U-352 fast
        path's own `_apply_payment_term_fields` behavior.

        Deliberately does NOT call `set_qbo_identity` / refresh `QboActive` here —
        PaymentTerm accepted a staleness tradeoff on a fast-path HIT (U-282), unlike
        Vendor/SubCostCode's "refresh every hit" pattern; this migration explicitly
        preserves that pre-existing divergence rather than importing SubCostCode's own
        refresh-on-hit call.

        Returns None on a ROWVERSION-race/concurrent-delete `update_by_id` miss
        (U-291) — `run_identity_fastpath_dbo_only`'s own `_apply()` raises
        `raise_concurrent_write_race` unconditionally whenever `apply_fields` returns
        None, so staying silent on a miss here is what keeps that single raise as the
        ONE place the guarantee lives (mirrors every sibling connector's identical
        HIT-branch shape).
        """
        payment_term.name = preserve_human_edited_name(payment_term.name, term_name)
        payment_term.description = description
        payment_term.discount_percent = (
            float(qbo_term.discount_percent) if qbo_term.discount_percent is not None else None
        )
        payment_term.discount_days = qbo_term.discount_days
        payment_term.due_days = qbo_term.due_days
        return self.payment_term_service.repo.update_by_id(payment_term)

    def _resolve_payment_term_candidate(
        self, qbo_term: QboTerm, *, term_name: str, description: Optional[str],
    ) -> PaymentTerm:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-352): called
        only under `run_identity_fastpath_dbo_only`'s create lock, once a genuine miss
        is confirmed (no dbo.PaymentTerm currently holds this identity, including the
        re-read under lock). Always creates a NEW PaymentTerm — unlike
        `CompanyInfoCompanyConnector`/SubCostCode, PaymentTerm's create path has never
        had a by-name adopt step, and this migration preserves that (tests/
        test_qbo_identity_reference.py's inactive-unmapped-adopt coverage
        deliberately excludes payment_term). Deactivation guard (U-219) runs here,
        directly before create, exactly as the pre-U-352 legacy path did.
        """
        raise_if_inactive_unmapped(
            qbo_term.active, qbo_label="QboTerm", qbo_id=qbo_term.id, target="PaymentTerm"
        )
        logger.info(f"Creating new PaymentTerm from QboTerm {qbo_term.id}: name={term_name}")
        return self.payment_term_service.create(
            name=term_name,
            description=description,
            discount_percent=(
                float(qbo_term.discount_percent) if qbo_term.discount_percent is not None else None
            ),
            discount_days=qbo_term.discount_days,
            due_days=qbo_term.due_days,
        )

    def _stamp_payment_term_identity(
        self, candidate: PaymentTerm, qbo_term: QboTerm,
    ) -> Optional[PaymentTerm]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-352): stamps
        `dbo.PaymentTerm.QboId`/`RealmId`/`QboActive` directly onto `candidate`, no
        lock.

        Company's `_stamp_company_identity` (U-350) delegates to the shared
        `stamp_dbo_identity_with_lock` because its `resolve_candidate` can ADOPT a
        PRE-EXISTING row by name — a side channel two different incoming QBO records
        could both resolve to, which is exactly what that helper's candidate-scoped
        lock + theft-guard exists to serialize. PaymentTerm's `_resolve_payment_term_
        candidate` above has no such side channel: `candidate` is always a row THIS
        call just created via `.create()`, with no key any other syncer could use to
        find and race it — so that lock + guard would protect a collision this family
        cannot produce. Carrying it anyway for sibling-shape symmetry was flagged as
        over-engineering by the `/simplify` altitude pass and dropped here; this
        matches the pre-U-352 legacy `create_mapping`'s own shape (a direct
        `set_qbo_identity` call, no lock).

        Re-reads and returns the row after stamping (rather than returning `candidate`
        as-is) so the result reflects the just-written identity, matching every
        sibling connector's stamp-function contract. If `candidate` was deleted
        between `resolve_candidate`'s `.create()` and this call (a concurrent-delete
        race `run_identity_fastpath_dbo_only`'s own outer lock does not cover — it
        only serializes racers for the SAME incoming `qbo_id`), `set_qbo_identity`
        affects 0 rows (silent no-op, matching the pre-U-352 legacy behavior) and this
        re-read returns None, which the caller's `stamped is None` check turns into
        `raise_concurrent_write_race` — so the race still surfaces, just via the
        re-read rather than a pre-emptive lock.
        """
        self.create_mapping(
            payment_term_id=coerce_id(candidate.id),
            qbo_id=qbo_term.qbo_id,
            realm_id=qbo_term.realm_id,
            active=qbo_term.active,
        )
        return self.payment_term_service.read_by_id(candidate.id)

    def create_mapping(
        self,
        payment_term_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        active: Optional[bool] = None,
    ) -> None:
        """
        Stamp `dbo.PaymentTerm.QboId`/`RealmId`/`QboActive` (U-352).

        `dbo.PaymentTerm.QboId`/`RealmId` is the SOLE identity store — this no longer
        reads or writes a `qbo.TermPaymentTerm` mapping row (that table is retired).
        `active` threads PaymentTerm's own QboActive mirror (U-275/U-282) through —
        the one param Company's own `create_mapping` never carried, since Company has
        no QboActive column. Mirrors `CompanyInfoCompanyConnector.create_mapping`
        (U-350), minus the `qbo_<external>_id` symmetry param Company/Address keep:
        nothing in this family or its callers reads it, so it was dropped rather than
        carried (`/simplify` simplification-angle finding, U-352).
        """
        self.payment_term_service.repo.set_qbo_identity(
            id=payment_term_id, qbo_id=qbo_id, realm_id=realm_id, active=active,
        )
