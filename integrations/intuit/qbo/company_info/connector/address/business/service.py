# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.physical_address.connector.business.service import (
    PhysicalAddressAddressConnector,
)
from entities.address.business.model import Address

logger = logging.getLogger(__name__)


class CompanyInfoAddressConnector:
    """
    Projects one of CompanyInfo's three INLINE addresses into `dbo.Address`
    (U-513), without a `qbo.PhysicalAddress` round trip.

    Thin on purpose. The identity fast path, the tombstone guard, the
    street/city soft-dedup and the conflict recorder all stay where they are, in
    `PhysicalAddressAddressConnector`; this class owns only the two facts that
    are specific to the CompanyInfo family and that a shared connector has no
    way to know:

      1. the address fields arrive as a `QboPhysicalAddressRef` off the
         CompanyInfo payload rather than as a staging row, and
      2. which realm this family's `dbo.Address` identity is stamped under —
         see `ADDRESS_IDENTITY_REALM_ID`, which is NOT the live realm.

    It is a separate class rather than three more keyword arguments on
    `CompanyInfoCompanyConnector` because the two project different entities
    into different tables; the Company connector's whole surface is the dbo-only
    Company identity fast path.
    """

    # ⚠️ The realm `dbo.Address` identity for CompanyInfo's three addresses is
    # stamped under — and it is None, NOT the live realm id. Verified 2026-09-24
    # against the code, in the order the value actually flows:
    #
    #   1. `QboCompanyInfoService._sync_physical_address` calls
    #      `QboPhysicalAddressRepository.create()` / `.update_by_id()` WITHOUT
    #      `realm_id` (the repo's parameter defaults to None), so every
    #      `qbo.PhysicalAddress` row this family owns carries `RealmId = NULL`.
    #      The board records the live count: three rows, and they are the reason
    #      U-514 (realm-scoping the staging read) had to be split out of U-508a.
    #   2. `PhysicalAddressAddressConnector.sync_from_qbo_to_address` reads
    #      `realm_id = qbo_physical_address.realm_id` — NULL — and
    #      `SetAddressQboIdentity` stamps that onto `dbo.Address.RealmId`.
    #   3. `ReadAddressByQboIdAndRealmId` matches NULL only against NULL:
    #      `(([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL))`.
    #
    # So handing the LIVE realm to `sync_address_from_external` would MISS the
    # exact row it must update, fall to the street/city adopt path, find that
    # same row, and trip `_check_no_conflicting_address_identity` (same QboId,
    # different realm) — a plain `ValueError`, which `record_projection_error`
    # classifies as a PERMANENT SKIP. The watermark would advance and the
    # company address would silently stop reaching dbo: loud only in
    # `qbo.ReconciliationIssue`. Passing None reproduces today's identity
    # exactly, so the sunset re-points the SOURCE of the fields without
    # re-keying anything.
    #
    # Flipping this to the live realm is U-514's job, in the same deploy as its
    # realm backfill — not this unit's. Customer/vendor are NOT affected: their
    # `_upsert_physical_address` does pass `realm_id` to the repo, so their
    # `dbo.Address` rows carry a real realm and their inline projection should
    # pass one.
    ADDRESS_IDENTITY_REALM_ID: Optional[str] = None

    def __init__(
        self,
        address_connector: Optional[PhysicalAddressAddressConnector] = None,
    ):
        """Initialize the CompanyInfoAddressConnector."""
        self.address_connector = address_connector or PhysicalAddressAddressConnector()

    def project_address(self, address_ref, *, qbo_id: str) -> Address:
        """
        Project one inline address slot into `dbo.Address`.

        Args:
            address_ref: the `QboPhysicalAddressRef` off the CompanyInfo payload.
                Blankness is the CALLER's gate
                (`QboCompanyInfoService._inline_address_is_blank`), for the same
                reason the caller owns the per-slot try/except: it is the holder
                of the `SyncOutcome` a skipped or failed slot has to be recorded
                against.
            qbo_id: the address's QBO identity — its own `Id`, or the
                `f"{realm_id}-{suffix}"` synthetic. Derived by
                `QboCompanyInfoService._address_qbo_id`, the one derivation the
                staging write also uses, so the two cannot drift.

        Returns:
            Address: the projected dbo.Address.

        No field-match lookup of any kind happens here, and none may be added
        (U-508a): a qbo_id miss must CREATE. The deleted fallback scanned all 957
        staging rows and re-keyed the first row sharing (line1, city,
        postal_code) — unscoped by realm and by owner, it would have adopted
        Vendor 1246's billing address. `PhysicalAddressAddressConnector`'s own
        street/city adopt is a different, bounded thing: it runs only under the
        create lock on a confirmed dbo miss, and it refuses any row already
        carrying a different (QboId, RealmId).
        """
        return self.address_connector.sync_address_from_external(
            qbo_id=qbo_id,
            realm_id=self.ADDRESS_IDENTITY_REALM_ID,
            line1=address_ref.line1,
            line2=address_ref.line2,
            city=address_ref.city,
            country_sub_division_code=address_ref.country_sub_division_code,
            postal_code=address_ref.postal_code,
            source_ref=f"company_info:{qbo_id}",
        )
