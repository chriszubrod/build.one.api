# Python Standard Library Imports
import json
import logging
from typing import Optional, Tuple

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.company_info.business.model import QboCompanyInfo
from integrations.intuit.qbo.company_info.connector.address.business.service import (
    CompanyInfoAddressConnector,
)
from integrations.intuit.qbo.company_info.external.client import QboCompanyInfoClient
from integrations.intuit.qbo.company_info.external.schemas import QboCompanyInfo as QboCompanyInfoExternalSchema
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome

logger = logging.getLogger(__name__)

# The three inline address slots CompanyInfo carries, as
# (external-schema attribute, synthetic-id suffix).
#
# Phase 1 of U-513 introduced this table because the pull then had TWO consumers
# of the same identity -- the `qbo.PhysicalAddress` staging write and the inline
# `dbo.Address` projection -- and hand-repeating the three suffixes in
# near-identical `if` blocks was how a typo could re-key an address instead of
# updating it. Phase 3a deleted the staging write, so the projection is the only
# consumer left; the table stays because the per-slot suffix is still the thing
# that must not be mistyped (see `_address_qbo_id`), and because a slot dropped
# from it silently stops projecting with nothing else failing.
#
# The third element (the transient-model field the staging row id used to be
# threaded into) went with the write: those ids no longer exist, and
# `_build_company_info` now sets the model's three `*_addr_id` fields to None in
# one documented place.
ADDRESS_SLOTS: Tuple[Tuple[str, str], ...] = (
    ("company_addr", "company"),
    ("legal_addr", "legal"),
    ("customer_communication_addr", "customer-communication"),
)


class QboCompanyInfoService:
    """
    Service for QboCompanyInfo entity business operations.

    Holds no repository: the pull no longer stages a `qbo.CompanyInfo` row.
    `sync_from_qbo` returns a transient QboCompanyInfo that
    `CompanyInfoCompanyConnector` projects straight into `dbo.Company`, which
    is the sole store for this entity's identity (U-350) and now for its data
    too. Mirrors `QboItemService` (U-307c) and `QboAttachableService`
    (U-300b), whose persistence packages are likewise empty.

    `qbo.PhysicalAddress` is no longer written either (U-513 phase 3a). Phases
    1-2 stopped this package READING the staging table — the three addresses
    project from the INLINE payload via `CompanyInfoAddressConnector` — but the
    write was deliberately left in place so the customer and vendor packages
    could be converted in parallel without the table disappearing underneath
    them. With those converted, the write was pure cost: a row nothing read.
    Dropping the table and its sprocs is the EM's phase 3b; this package no
    longer touches either.
    """

    def sync_from_qbo(self, realm_id: str, last_updated_time: Optional[str] = None) -> SyncOutcome[QboCompanyInfo]:
        """
        Fetch CompanyInfo from QBO API and return it as a transient object,
        projecting its three inline addresses into `dbo.Address` on the way.

        Nothing is persisted to any `qbo.*` staging table — not the CompanyInfo
        (U-505) and, since phase 3a, not its addresses either. The `SyncOutcome`
        it returns therefore holds an object whose `id`/`public_id`/`row_version`
        are None; consumers must key off `qbo_id`.

        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                CompanyInfo where Metadata.LastUpdatedTime > last_updated_time.
                If no records match, returns an outcome with empty ``synced``.
        
        Returns:
            SyncOutcome[QboCompanyInfo]: Pull run envelope; ``synced`` holds 0 or 1
                transient (never persisted) record
        """
        outcome: SyncOutcome[QboCompanyInfo] = SyncOutcome.for_service_pull()
        # Fetch CompanyInfo from QBO API. QboHttpClient resolves and refreshes
        # the access token lazily, so no upfront auth call is needed.
        with QboCompanyInfoClient(realm_id=realm_id) as client:
            try:
                qbo_company_info: QboCompanyInfoExternalSchema = client.get_company_info(
                    last_updated_time=last_updated_time
                )
            except ValueError as e:
                # If no CompanyInfo found (e.g., no updates since last sync), return None
                if "No CompanyInfo found" in str(e):
                    logger.info(f"No CompanyInfo updates found since {last_updated_time or 'beginning'}")
                    outcome.fetched = 0
                    return outcome
                raise
        
        if not qbo_company_info:
            logger.info("No CompanyInfo found in QBO API response")
            outcome.fetched = 0
            return outcome

        outcome.fetched = 1
        # No `qbo.PhysicalAddress` upsert precedes this any more (phase 3a). The
        # loop that used to sit here wrote a staging row per slot purely so its
        # PK could be threaded onto the transient record; phases 1-2 replaced the
        # read-back with the inline projection below, leaving the write with no
        # reader at all. Its removal also closes a small asymmetry: a malformed
        # (no-Id) response used to still write three staging rows before failing
        # the guard below, so a row this pull could not project nonetheless
        # existed.
        try:
            record = self._build_company_info(qbo_company_info, realm_id=realm_id)
        except ValueError as e:
            # A staging failure on purpose: `SyncOutcome.should_hold` is what
            # makes `WatermarkRun.commit` hold the delta cursor, so the next
            # tick refetches this CompanyInfo instead of stepping past it. A
            # missing Id is a malformed response that may well be intact on the
            # retry, not a permanent data gap. (Since U-507 there is no staging
            # skip verb at all -- the staging tier holds, and permanent-data
            # skips live only at the projection tier. See sync_outcome.py's
            # module docstring.)
            logger.error(f"Cannot project CompanyInfo for realm_id {realm_id}: {e}")
            # A sentinel, not the None id: record_staging_failure stringifies,
            # so passing None writes the literal "None" into staging_failed_ids,
            # which _record_bound_forced_advance stamps as the
            # ReconciliationIssue's qbo_id at the hold bound.
            #
            # A BARE sentinel is correct HERE and only here: this pull stages
            # exactly one CompanyInfo per run, so it cannot collide with itself.
            # reimburse_charge deliberately diverged in U-507 to a per-row
            # f"<no-id>:{i}" -- it loops, and N malformed rows under one bare
            # sentinel would emit N identical ReconciliationIssues at the bound.
            outcome.record_staging_failure("<no-id>", e)
            return outcome

        outcome.record_synced(record)
        # Project the three addresses straight from the INLINE payload (U-513).
        # Deliberately AFTER `record_synced` and inside the success path only:
        # pre-U-513 the caller projected addresses off `outcome.synced[0]`, so a
        # malformed (no-Id) response reached no address projection either. The
        # ordering relative to the Company projection is also unchanged —
        # addresses still land before `CompanyInfoCompanyConnector` runs.
        self._project_addresses_from_payload(
            qbo_company_info, realm_id=realm_id, outcome=outcome
        )
        return outcome

    def _project_addresses_from_payload(
        self,
        qbo_company_info: QboCompanyInfoExternalSchema,
        *,
        realm_id: str,
        outcome: SyncOutcome[QboCompanyInfo],
    ) -> None:
        """
        Project CompanyInfo's three inline addresses into `dbo.Address` (U-513).

        Replaces the caller's old write-then-read-back-by-id round trip: the
        script used to take the `qbo.PhysicalAddress` row ids this pull had just
        written and hand each to
        `PhysicalAddressAddressConnector.sync_from_qbo_to_address`, which read
        the very same row back. Every column staging held
        (Line1/Line2/City/CountrySubDivisionCode/PostalCode) is present INLINE on
        the CompanyInfo response (`external/schemas.py`), so the staging hop
        bought nothing but a dependency on a table being sunset.

        Phase 1 removed the READ and kept the write so customer and vendor could
        convert in parallel; phase 3a removed the write. This is now the ONLY
        thing the pull does with an address.

        Bookkeeping is the caller's old shape, moved here intact: one
        `record_projection_error` per failing slot, so a transient DB error still
        HOLDS the watermark and a plain `ValueError` still records a permanent
        skip. `record_projected()` is deliberately NOT called — the caller never
        counted addresses toward `projected_count` (that counter means "the
        Company projected"), and inflating it would silently change what a
        watermark run reports. Failures are isolated per slot, exactly as the
        three-iteration loop in the script was.

        Blank slots mint nothing — see `_inline_address_is_blank`.
        """
        connector = CompanyInfoAddressConnector()
        for attribute, suffix in ADDRESS_SLOTS:
            address_ref = getattr(qbo_company_info, attribute, None)
            if self._inline_address_is_blank(address_ref):
                continue
            address_qbo_id = self._address_qbo_id(
                address_ref, realm_id=realm_id, suffix=suffix
            )
            try:
                # No realm argument: which realm `dbo.Address` identity for THIS
                # family is stamped under is an identity fact, and it lives in
                # exactly one documented place —
                # `CompanyInfoAddressConnector.ADDRESS_IDENTITY_REALM_ID`.
                # Threading `realm_id` here only to have it ignored is the shape
                # the board flagged on the deleted `_sync_physical_address`,
                # whose `realm_id` argument reached no repo call.
                address = connector.project_address(
                    address_ref, qbo_id=address_qbo_id
                )
                logger.info(
                    f"Projected CompanyInfo {suffix} address {address_qbo_id} to "
                    f"Address module. Address ID: {address.id if address else 'None'}"
                )
            except Exception as e:
                # Same classifier the caller used, so hold-vs-skip is unchanged.
                # The recorded id is now the ADDRESS's QBO id rather than the
                # staging row's PK — the staging PK is exactly the handle that
                # stops existing once the table is dropped, and the QBO id is
                # what a ReconciliationIssue reader can actually act on.
                outcome.record_projection_error(
                    address_qbo_id, e, label="CompanyInfoAddr->Address", logger=logger
                )

    @staticmethod
    def _address_qbo_id(address_ref, *, realm_id: str, suffix: str) -> str:
        """
        The QBO identity of one CompanyInfo address slot.

        ⚠️ This is NOT the realm id. It is the address's own QBO `Id` when QBO
        supplies one, and `f"{realm_id}-{suffix}"` (e.g.
        `"9130353016965726-company"`) when it does not — the synthetic fallback
        that has existed since the pull was written. `dbo.Address.QboId` carries
        whichever of the two was in force, so projecting under any other value
        would MINT A DUPLICATE rather than update the existing row.

        U-513 phase 1 extracted this out of the two paths that then needed it
        (the `qbo.PhysicalAddress` write and the inline projection) so they could
        not drift; phase 3a deleted the write, and this is now the sole
        derivation of the sole remaining path.

        ⚠️ A MISS MUST CREATE, NEVER ADOPT (U-508a). The deleted staging write
        once carried a "# Migration:" block that, on a qbo_id miss, enumerated
        all 957 `qbo.PhysicalAddress` rows, linear-scanned for one sharing
        (line1, city, postal_code), and REWROTE that row's qbo_id to
        CompanyInfo's — scoped by neither realm nor owner. Ordered by QboId ASC,
        '1246_bill' sorts before '1612', so the first row matching the company
        address ('PO Box 594', 'Brentwood', '37024') is Vendor 1246's BILLING
        address. Nothing of that shape may be reintroduced on the projection
        path either: `CompanyInfoAddressConnector` is deliberately given only an
        identity and the payload's fields, and never a way to go looking for a
        same-street row.

        ⚠️ ONE TRANSITION IS DELIBERATELY UNHANDLED — see BOARD.md U-518.
        If QBO later starts supplying a REAL `Id` for a slot that was previously
        synthesised, the two are different identities. The existing
        `dbo.Address` still carries the synthetic one (U-351 retired the mapping
        table; dbo owns its QBO identity directly), so projecting under the real
        id trips
        `PhysicalAddressAddressConnector._check_no_conflicting_address_identity`,
        which raises `ValueError`; `record_projection_error` classifies a plain
        ValueError as a PERMANENT SKIP, so the watermark advances and the real
        address never reaches dbo. It is loud in `qbo.ReconciliationIssue` and
        silent everywhere else.

        0 synthetic rows exist today. The detection query, the cross-check that
        distinguishes a live transition from a still-current synthetic row, and
        the repair sequence live in BOARD.md U-518 — NOT here. An earlier draft
        carried ~40 lines of runbook in a docstring and drifted out of true three
        times in one day; a function docstring is the wrong home for an
        operational procedure.
        """
        return address_ref.id or f"{realm_id}-{suffix}"

    @staticmethod
    def _inline_address_is_blank(address_ref) -> bool:
        """
        True when an inline address slot carries no content — QBO's placeholder
        shape. U-506 P1's predicate, re-expressed against the payload instead of
        the staging row (U-513): blank when `line1`, `city` and `postal_code` are
        ALL empty after `.strip()`. Blank means ABSENT; a blank slot must mint no
        `dbo.Address` at all (191 blank rows already exist because two of the
        three staging writers never had a guard).

        An absent slot (QBO omitted the key) counts as blank too.

        Fields are read directly, not via `getattr` with a default — a renamed
        `QboPhysicalAddressRef` field must break loudly rather than silently make
        every address look blank. Mirrors
        `CustomerProjectConnector._is_blank_staged_address`.

        This is now the pull's ONLY blankness predicate. The deleted staging
        write carried its own, looser `any([line1, line2, city, country])` gate;
        the two deliberately disagreed in both directions, each time with this
        one right — a country-only slot earned a staging row but is not
        projected (it used to mint a blank `dbo.Address`), and a
        postal-code-only slot is projected though it never earned a staging row.
        That second gate went with the write in phase 3a, so the disagreement is
        resolved rather than merely documented.
        """
        if address_ref is None:
            return True
        return not (
            (address_ref.line1 or "").strip()
            + (address_ref.city or "").strip()
            + (address_ref.postal_code or "").strip()
        )

    def _build_company_info(
        self,
        qbo_company_info: QboCompanyInfoExternalSchema,
        *,
        realm_id: str,
    ) -> QboCompanyInfo:
        """
        Build the transient QboCompanyInfo this pull projects from.

        Raises ValueError when the response carries no QBO `Id`; `sync_from_qbo`
        turns that into a staging failure so the watermark HOLDS. See the module
        docstring of tests/test_u505_company_info_staging_repoint.py for why
        that matters -- it is the regression this guard exists to prevent.

        Every field is derived here, matching `QboItemService._upsert_item`. The
        three `*_addr_id` parameters went with the staging write (U-513 phase
        3a): they carried `qbo.PhysicalAddress` row PKs that no longer exist, and
        keeping them as parameters would have advertised a value no caller could
        ever supply. The model's own three fields are set to None below, in one
        place, until the model itself is narrowed.
        """
        if not qbo_company_info.id:
            raise ValueError("QBO CompanyInfo must have an ID")

        email = None
        if qbo_company_info.email and hasattr(qbo_company_info.email, "address"):
            email = qbo_company_info.email.address

        web_addr = None
        if qbo_company_info.web_addr and hasattr(qbo_company_info.web_addr, "uri"):
            web_addr = qbo_company_info.web_addr.uri

        currency_ref = None
        if qbo_company_info.currency_ref:
            currency_ref = json.dumps(qbo_company_info.currency_ref.dict(exclude_none=True))

        return QboCompanyInfo(
            id=None,
            public_id=None,
            row_version=None,
            created_datetime=None,
            modified_datetime=None,
            qbo_id=qbo_company_info.id,
            sync_token=qbo_company_info.sync_token,
            realm_id=realm_id,
            company_name=qbo_company_info.company_name,
            legal_name=qbo_company_info.legal_name,
            # Always None since U-513 phase 3a. These three fields held the
            # `qbo.PhysicalAddress` row PKs the deleted staging write produced.
            # Nothing reads them — `CompanyInfoCompanyConnector` uses only
            # qbo_id / realm_id / legal_name / web_addr — and the addresses
            # themselves reach `dbo.Address` through
            # `_project_addresses_from_payload`, keyed on `_address_qbo_id`, not
            # on a row PK. They stay on the dataclass (and in `to_dict()`, hence
            # in the sync script's response body) only because narrowing the
            # model is a separate change; a reader must not take a None here as
            # "this company has no address".
            company_addr_id=None,
            legal_addr_id=None,
            customer_communication_addr_id=None,
            tax_payer_id=qbo_company_info.tax_payer_id,
            fiscal_year_start_month=qbo_company_info.fiscal_year_start_month,
            country=qbo_company_info.country,
            email=email,
            web_addr=web_addr,
            currency_ref=currency_ref,
        )
