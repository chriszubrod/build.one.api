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
from integrations.intuit.qbo.physical_address.persistence.repo import QboPhysicalAddressRepository

logger = logging.getLogger(__name__)

# The three inline address slots CompanyInfo carries, as
# (external-schema attribute, synthetic-id suffix, transient-model field).
#
# ONE table, two consumers (U-513): the staging write (`_sync_physical_address`)
# and the inline projection (`_project_addresses_from_payload`) both derive the
# address's QBO id from this tuple, so the id they use CANNOT drift apart while
# the two paths run side by side. Before U-513 the suffixes were hand-repeated
# in three near-identical `if` blocks; a typo in one of them would have re-keyed
# an address rather than updated it.
ADDRESS_SLOTS: Tuple[Tuple[str, str, str], ...] = (
    ("company_addr", "company", "company_addr_id"),
    ("legal_addr", "legal", "legal_addr_id"),
    ("customer_communication_addr", "customer-communication", "customer_communication_addr_id"),
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

    `qbo.PhysicalAddress` IS still written, by `_sync_physical_address` — that
    table is shared with the customer and vendor pulls and is out of scope.
    """

    def sync_from_qbo(self, realm_id: str, last_updated_time: Optional[str] = None) -> SyncOutcome[QboCompanyInfo]:
        """
        Fetch CompanyInfo from QBO API and return it as a transient object.
        Extracts PhysicalAddress objects and upserts them (into
        `qbo.PhysicalAddress`) first, so their row ids can ride along for the
        caller's address projection.

        Nothing about the CompanyInfo itself is persisted here — see
        `_build_company_info`. The `SyncOutcome` it returns therefore holds an
        object whose `id`/`public_id`/`row_version` are None; consumers must
        key off `qbo_id`.
        
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
        # Extract and sync PhysicalAddress records. Still written (U-513 stops
        # DEPENDING on qbo.PhysicalAddress; the EM drops the table in a later
        # step, once all three consumer packages are integrated and verified).
        staged_address_ids = {field: None for _, _, field in ADDRESS_SLOTS}
        for attribute, suffix, field in ADDRESS_SLOTS:
            address_ref = getattr(qbo_company_info, attribute)
            if not address_ref:
                continue
            staged_address_ids[field] = self._sync_physical_address(
                realm_id,
                address_ref,
                # The actual QBO Id from the address reference, or a synthesised
                # fallback. See `_address_qbo_id`.
                self._address_qbo_id(address_ref, realm_id=realm_id, suffix=suffix),
            )

        try:
            record = self._build_company_info(
                qbo_company_info,
                realm_id=realm_id,
                **staged_address_ids,
            )
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

        The staging WRITE above is untouched and still runs — only the READ is
        gone. That is what lets this land while the customer and vendor packages
        are being converted in parallel.

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
        for attribute, suffix, _field in ADDRESS_SLOTS:
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
                # the board already flags on `_sync_physical_address`.
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
        The QBO identity of one CompanyInfo address slot — the ONE derivation
        both the staging write and the inline projection use (U-513).

        ⚠️ This is NOT the realm id. It is the address's own QBO `Id` when QBO
        supplies one, and `f"{realm_id}-{suffix}"` (e.g.
        `"9130353016965726-company"`) when it does not — the synthetic fallback
        that has existed since the pull was written. `dbo.Address.QboId` carries
        whichever of the two was in force, so projecting under any other value
        would MINT A DUPLICATE rather than update the existing row.

        The synthetic branch is also the live trigger U-518 owns: if QBO later
        starts supplying a real `Id` for a slot that was synthesised, the two
        identities are different rows. That transition is unhandled BY DESIGN
        (re-keying by address fields is precisely what U-508a deleted, because
        it could steal a foreign vendor's row) — see `_sync_physical_address`'s
        docstring and BOARD.md U-518.
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

        ⚠️ Deliberately NOT the same predicate as `_sync_physical_address`'s own
        `any([line1, line2, city, country])` staging guard, which stays as-is.
        The two now diverge in two directions, both toward the U-506 predicate
        being right: a country-only slot still gets a staging row but is no
        longer projected (it used to mint a blank `dbo.Address`), and a
        postal-code-only slot is now projected though it never earned a staging
        row. The staging guard disappears with the table.
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
        company_addr_id: Optional[int],
        legal_addr_id: Optional[int],
        customer_communication_addr_id: Optional[int],
    ) -> QboCompanyInfo:
        """
        Build the transient QboCompanyInfo this pull projects from.

        Raises ValueError when the response carries no QBO `Id`; `sync_from_qbo`
        turns that into a staging failure so the watermark HOLDS. See the module
        docstring of tests/test_u504_company_info_staging_repoint.py for why
        that matters -- it is the regression this guard exists to prevent.

        The address ids are the `qbo.PhysicalAddress` rows `_sync_physical_address`
        just upserted; they are parameters because only the caller has them.
        Everything else is derived here, matching `QboItemService._upsert_item`.
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
            company_addr_id=company_addr_id,
            legal_addr_id=legal_addr_id,
            customer_communication_addr_id=customer_communication_addr_id,
            tax_payer_id=qbo_company_info.tax_payer_id,
            fiscal_year_start_month=qbo_company_info.fiscal_year_start_month,
            country=qbo_company_info.country,
            email=email,
            web_addr=web_addr,
            currency_ref=currency_ref,
        )

    def _sync_physical_address(
        self,
        realm_id: str,
        address_ref,
        qbo_id: str
    ) -> Optional[int]:
        """
        Sync a PhysicalAddress record and return its database ID.

        Token resolution is lazy inside QboHttpClient / the physical-address
        repos, so no access_token is threaded here (removing it closed a P0
        NameError: the caller referenced an undefined `qbo_auth`).

        Plain read-or-create, identical in shape to the customer/ and vendor/
        siblings' `_upsert_physical_address`. It deliberately has NO
        match-by-address-fields fallback:

          U-508 — a "# Migration:" block used to run `repo.read_all()` (957
          rows) on a qbo_id MISS and linear-scan in Python for a row with the
          same (line1, city, postal_code), then REWRITE that row's qbo_id to
          this one. It was unscoped by realm and by owner, so it could adopt
          any customer's or vendor's billing address that happened to share a
          street. It was also spent: commit e3f2f068 (07:34:07 UTC) introduced
          it 14 minutes AFTER the three CompanyInfo rows it was meant to heal
          were created (07:20:32 UTC); it re-keyed them once in January 2026
          and has matched nothing since. Its trigger stayed live, though — the
          caller still synthesises `f"{realm_id}-company"` when QBO omits an
          address Id — so U-514's realm-scoped read (which makes those three
          realm-NULL rows MISS until the backfill lands) would have fired it
          against `read_all()`'s `ORDER BY [QboId] ASC`, where '1246_bill'
          sorts before '1612' and Vendor 1246's billing address is the first
          (line1, city, postal_code) match.

          ⚠️ U-514 (realm-scoping the read, the unique index, the column
          narrowing, the realm backfill) was SPLIT OUT of this unit on
          2026-09-23, after its fix round kept uncovering adjacent pre-existing
          defects in the physical_address package. This deletion ships ALONE
          and is safe alone: with no realm-scoped read, the three CompanyInfo
          rows still resolve by qbo_id exactly as they do today, so the miss
          that would fire this fallback cannot occur. It must still never be
          reinstated -- U-514 makes that miss reachable, and the theft above is
          what would follow.

        ⚠️ ONE TRANSITION IS DELIBERATELY UNHANDLED — see BOARD.md U-518.
          The caller synthesises `f"{realm_id}-company"` when QBO omits an
          address Id. If a later response carries a REAL Id, this method creates
          a SECOND staging row rather than re-keying the first (re-keying by
          address fields is exactly what was deleted above, and what could steal
          a foreign vendor's row).

          Consequence, traced: `dbo.Address` owns its QBO identity directly
          (U-351 retired the mapping), so it still carries the synthetic id.
          Projecting the real-id row then trips
          `PhysicalAddressAddressConnector._check_no_conflicting_address_identity`,
          which raises `ValueError`; `record_projection_error` classifies a
          plain ValueError as a PERMANENT SKIP, so the watermark advances and
          the real address never reaches dbo. It is loud in
          `qbo.ReconciliationIssue` and silent everywhere else.

          0 synthetic rows exist today. The detection query, the cross-check
          that distinguishes a live transition from a still-current synthetic
          row, and the repair sequence live in BOARD.md U-518 — NOT here. An
          earlier draft carried ~40 lines of runbook in this docstring and drifted
          out of true three times in one day; a function docstring is the wrong
          home for an operational procedure.

        Args:
            realm_id: QBO company realm ID. Used to synthesise a fallback
                qbo_id when QBO omits the address Id, and passed to the
                projection. NOT threaded to the repo: this service's read is
                qbo_id-only and neither write persists a realm. That threading
                is U-514's, split out of this unit on 2026-09-23 — it needs its
                realm backfill in the same deploy or the CompanyAddr projection
                regresses.
            address_ref: QboPhysicalAddressRef object from CompanyInfo
            qbo_id: QBO ID to use for the address record

        Returns:
            int: The PhysicalAddress.Id, or None if address is empty
        """
        if not address_ref or not any([
            address_ref.line1,
            address_ref.line2,
            address_ref.city,
            address_ref.country
        ]):
            return None
        
        # Check if PhysicalAddress already exists. NOT realm-scoped: that is
        # U-514's, split out of this unit 2026-09-23. Realm-scoping this read
        # is what makes the three RealmId-NULL CompanyInfo rows MISS, and that
        # miss is what used to fire the deleted fallback into Vendor 1246's
        # row. U-514 must land its realm backfill in the same deploy.
        physical_address_repo = QboPhysicalAddressRepository()
        existing = physical_address_repo.read_by_qbo_id(qbo_id=qbo_id)

        if existing:
            # Update existing PhysicalAddress
            logger.debug(f"Updating existing PhysicalAddress with QBO ID: {qbo_id}")
            updated = physical_address_repo.update_by_id(
                id=existing.id,
                row_version=existing.row_version_bytes,
                qbo_id=qbo_id,
                line1=address_ref.line1,
                line2=address_ref.line2,
                city=address_ref.city,
                country=address_ref.country,
                country_sub_division_code=address_ref.country_sub_division_code,
                postal_code=address_ref.postal_code,
            )
            return updated.id if updated else None
        else:
            # Create new PhysicalAddress
            logger.debug(f"Creating new PhysicalAddress with QBO ID: {qbo_id}")
            created = physical_address_repo.create(
                qbo_id=qbo_id,
                line1=address_ref.line1,
                line2=address_ref.line2,
                city=address_ref.city,
                country=address_ref.country,
                country_sub_division_code=address_ref.country_sub_division_code,
                postal_code=address_ref.postal_code,
            )
            return created.id if created else None


