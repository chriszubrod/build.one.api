# Python Standard Library Imports
import json
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.company_info.business.model import QboCompanyInfo
from integrations.intuit.qbo.company_info.external.client import QboCompanyInfoClient
from integrations.intuit.qbo.company_info.external.schemas import QboCompanyInfo as QboCompanyInfoExternalSchema
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.physical_address.persistence.repo import QboPhysicalAddressRepository

logger = logging.getLogger(__name__)


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
        # Extract and sync PhysicalAddress records
        company_addr_id = None
        legal_addr_id = None
        customer_communication_addr_id = None
        
        # Sync CompanyAddr
        if qbo_company_info.company_addr:
            # Use the actual QBO Id from the address reference, or fallback to constructed ID
            addr_qbo_id = qbo_company_info.company_addr.id or f"{realm_id}-company"
            company_addr_id = self._sync_physical_address(
                realm_id,
                qbo_company_info.company_addr,
                addr_qbo_id
            )
        
        # Sync LegalAddr
        if qbo_company_info.legal_addr:
            # Use the actual QBO Id from the address reference, or fallback to constructed ID
            addr_qbo_id = qbo_company_info.legal_addr.id or f"{realm_id}-legal"
            legal_addr_id = self._sync_physical_address(
                realm_id,
                qbo_company_info.legal_addr,
                addr_qbo_id
            )
        
        # Sync CustomerCommunicationAddr
        if qbo_company_info.customer_communication_addr:
            # Use the actual QBO Id from the address reference, or fallback to constructed ID
            addr_qbo_id = qbo_company_info.customer_communication_addr.id or f"{realm_id}-customer-communication"
            customer_communication_addr_id = self._sync_physical_address(
                realm_id,
                qbo_company_info.customer_communication_addr,
                addr_qbo_id
            )
        
        try:
            record = self._build_company_info(
                qbo_company_info,
                realm_id=realm_id,
                company_addr_id=company_addr_id,
                legal_addr_id=legal_addr_id,
                customer_communication_addr_id=customer_communication_addr_id,
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
        return outcome

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


