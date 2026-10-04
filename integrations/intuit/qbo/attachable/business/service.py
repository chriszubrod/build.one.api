# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.attachable.business.model import QboAttachable
from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
from integrations.intuit.qbo.attachable.external.schemas import QboAttachable as QboAttachableExternalSchema
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)


class QboAttachableService:
    """
    Service for QBO Attachable business operations.
    """

    def __init__(
        self,
        auth_service: Optional[QboAuthService] = None,
        *,
        attachables_since: Optional[str] = None,
    ):
        """Initialize the QboAttachableService.

        `attachables_since` bounds the per-run realm snapshot to attachables
        whose `Metadata.LastUpdatedTime` is after the given watermark (the
        caller's pull watermark, overlap included). Without it the snapshot is
        the FULL realm list — ~19K rows / ~20 metered HTTP calls / minutes per
        run, which was the dominant cost of every purchase tick. An incremental
        pull passes its own `last_sync_time`; a full/historical pull passes
        None and keeps the authoritative full-list behaviour.
        """
        self.auth_service = auth_service or QboAuthService()
        self.attachables_since = attachables_since
        # Per-instance snapshot of the realm attachable list (full, or bounded by
        # `attachables_since`), populated lazily on the first per-entity lookup and
        # reused across the sync run (the service is created once per run). Avoids
        # re-pulling the list per entity while keeping same-entity lookups authoritative.
        self._all_attachables_cache = None
        # Full-realm list, loaded lazily ONLY when a caller asks for an
        # authoritative lookup (`authoritative=True`) on an incremental run —
        # the one case the bounded snapshot cannot answer: a purchase that is
        # NEW locally but OLD in QBO (deferred, skipped, or first pulled late),
        # whose receipts predate the bound. Separate from the bounded cache so
        # the common path never pays for it.
        self._full_attachables_cache = None

    def sync_from_qbo(
        self,
        realm_id: str,
        sync_to_modules: bool = False,
    ) -> List[QboAttachable]:
        """
        Sync all attachables from QBO to local database.
        
        Args:
            realm_id: QBO realm ID
            sync_to_modules: If True, also sync to Attachment module
            
        Returns:
            List of synced QboAttachable records
        """
        # Get QBO auth
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")

        # Fetch attachables from QBO
        with QboAttachableClient(realm_id=realm_id) as client:
            qbo_attachables: List[QboAttachableExternalSchema] = client.query_all_attachables()

        logger.info(f"Fetched {len(qbo_attachables)} attachables from QBO for realm {realm_id}")

        # Upsert each attachable
        synced = []
        for qbo_att in qbo_attachables:
            try:
                local_att = self._upsert_attachable(realm_id, qbo_att)
                synced.append(local_att)
            except Exception as e:
                logger.error(f"Failed to upsert attachable {qbo_att.id}: {e}")

        logger.info(f"Synced {len(synced)} attachables to local database")

        # Sync to Attachment module if requested
        if sync_to_modules:
            # Return only attachables whose blob synced healthily so callers
            # never link a missing-blob record to a line item.
            return self._sync_to_attachments(synced, realm_id)

        return synced

    def _query_attachables_with_fallback(
        self, client, entity_type: str, entity_id: str, *, authoritative: bool = False,
    ) -> list:
        """
        Fetch the attachables linked to one QBO entity — authoritatively.

        QBO does NOT support a WHERE on AttachableRef: the old per-entity query
        (`query_attachables_for_entity`) returned either a 400 (handled) or a misleading
        200 (all rows, or empty) that was trusted as-is, and the single-page fallback
        (`query_attachables`, MAXRESULTS 1000) silently missed any entity whose attachable
        sat past position 1000 of the ~3.3k realm total. Both produced false "no attachments".

        Fix: pull the realm list once (cached on this service for the sync run; the
        service is created once per run) and filter in-memory by an EXACT (entity_ref_type,
        entity_ref_value) match — both must match. Exact type match (not startswith) prevents
        cross-type collisions where ids are only unique per type (a "PurchaseOrder" /
        "BillPayment" attachable mis-attributed to a "Purchase"/"Bill" with the same id).
        Note: this captures SAME-entity docs reliably; cross-entity (Invoice-keyed) docs are
        recovered by the separate all-realm reconcile, not here.

        The list is the FULL realm when `attachables_since` is None, and only the
        attachables updated after that watermark otherwise. The exactness rule is the
        same either way — the bound only changes how many rows are paged, never how a
        row is matched. Continuity is what makes the bound safe: every incremental
        tick (including one with no changed purchases) walks the attachables updated
        since the previous tick's query start, so each attachable is seen by exactly
        one tick after it is created or updated. The one case continuity cannot
        cover is an attachable that was seen while its parent had NO local row yet
        (the parent was deferred, skipped, or is being pulled late) — the caller
        passes `authoritative=True` for such a parent and gets the full list.
        """
        rows = (
            self._ensure_full_list(client)
            if authoritative and self.attachables_since
            else self._ensure_snapshot(client)
        )
        target_type = (entity_type or "").upper()
        filtered = [
            a for a in rows
            if a.attachable_ref and any(
                ref.entity_ref_value == entity_id
                and (ref.entity_ref_type or "").upper() == target_type
                for ref in (a.attachable_ref or [])
            )
        ]
        if filtered:
            logger.info(f"Found {len(filtered)} attachables for {entity_type} {entity_id} (full-list filter)")
        return filtered

    def _ensure_snapshot(self, client) -> list:
        """Load the per-run attachable snapshot once (full or watermark-bounded)."""
        if self._all_attachables_cache is None:
            self._all_attachables_cache = client.query_all_attachables(
                last_updated_time=self.attachables_since,
            )
            logger.info(
                "qbo.attachable.snapshot_loaded mode=%s since=%s rows=%d",
                "incremental" if self.attachables_since else "full",
                self.attachables_since,
                len(self._all_attachables_cache),
            )
        return self._all_attachables_cache

    def _ensure_full_list(self, client) -> list:
        """Load the authoritative full-realm list once per run (see `authoritative`)."""
        if self._full_attachables_cache is None:
            self._full_attachables_cache = client.query_all_attachables()
            logger.info(
                "qbo.attachable.full_list_loaded reason=authoritative_lookup rows=%d",
                len(self._full_attachables_cache),
            )
        return self._full_attachables_cache

    def entity_ids_in_snapshot(self, realm_id: str, entity_type: str) -> set:
        """
        QBO ids of every `entity_type` entity that has at least one attachable in
        this run's snapshot (loading it if needed).

        Lets a pull act on attachables whose PARENT did not change: QBO does not
        bump a Purchase's `MetaData.LastUpdatedTime` when a receipt is attached to
        it, so a receipt matched after the purchase was first pulled is invisible
        to a parent-keyed pull. With an incremental snapshot this is cheap — the
        set is exactly the entities touched by attachables since the watermark.
        """
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")

        if self._all_attachables_cache is None:
            with QboAttachableClient(realm_id=realm_id) as client:
                self._ensure_snapshot(client)

        target_type = (entity_type or "").upper()
        ids: set = set()
        for a in self._all_attachables_cache or []:
            for ref in (a.attachable_ref or []):
                if (ref.entity_ref_type or "").upper() == target_type and ref.entity_ref_value:
                    ids.add(ref.entity_ref_value)
        return ids

    def sync_attachables_for_bill(
        self,
        realm_id: str,
        bill_qbo_id: str,
        sync_to_modules: bool = True,
    ) -> List[QboAttachable]:
        """
        Sync attachables linked to a specific bill.
        
        Args:
            realm_id: QBO realm ID
            bill_qbo_id: QBO Bill ID
            sync_to_modules: If True, also sync to Attachment module
            
        Returns:
            List of synced QboAttachable records
        """
        # Get QBO auth
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")

        # Fetch attachables for this bill from QBO
        with QboAttachableClient(realm_id=realm_id) as client:
            qbo_attachables = self._query_attachables_with_fallback(client, "Bill", bill_qbo_id)

        logger.info(f"Fetched {len(qbo_attachables)} attachables for Bill {bill_qbo_id}")

        # Upsert each attachable
        synced = []
        for qbo_att in qbo_attachables:
            try:
                local_att = self._upsert_attachable(realm_id, qbo_att)
                synced.append(local_att)
            except Exception as e:
                logger.error(f"Failed to upsert attachable {qbo_att.id}: {e}")

        # Sync to Attachment module if requested
        if sync_to_modules:
            # Return only attachables whose blob synced healthily so callers
            # never link a missing-blob record to a line item.
            return self._sync_to_attachments(synced, realm_id)

        return synced

    def sync_attachables_for_vendor_credit(
        self,
        realm_id: str,
        vendor_credit_qbo_id: str,
        sync_to_modules: bool = True,
    ) -> List[QboAttachable]:
        """
        Sync attachables linked to a specific VendorCredit.

        Args:
            realm_id: QBO realm ID
            vendor_credit_qbo_id: QBO VendorCredit ID
            sync_to_modules: If True, also sync to Attachment module

        Returns:
            List of synced QboAttachable records
        """
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")

        with QboAttachableClient(realm_id=realm_id) as client:
            qbo_attachables = self._query_attachables_with_fallback(client, "VendorCredit", vendor_credit_qbo_id)

        logger.info(f"Fetched {len(qbo_attachables)} attachables for VendorCredit {vendor_credit_qbo_id}")

        synced = []
        for qbo_att in qbo_attachables:
            try:
                local_att = self._upsert_attachable(realm_id, qbo_att)
                synced.append(local_att)
            except Exception as e:
                logger.error(f"Failed to upsert attachable {qbo_att.id}: {e}")

        if sync_to_modules:
            # Return only attachables whose blob synced healthily so callers
            # never link a missing-blob record to a line item.
            return self._sync_to_attachments(synced, realm_id)

        return synced

    def sync_attachables_for_purchase(
        self,
        realm_id: str,
        purchase_qbo_id: str,
        sync_to_modules: bool = True,
        *,
        authoritative: bool = False,
    ) -> List[QboAttachable]:
        """
        Sync attachables linked to a specific Purchase from QBO.

        Args:
            realm_id: QBO realm ID
            purchase_qbo_id: QBO Purchase ID
            sync_to_modules: If True, also sync to Attachment module
            authoritative: use the full-realm list even on an incremental run —
                for a purchase that is new locally but old in QBO, whose receipts
                may predate the snapshot bound (see `_query_attachables_with_fallback`).

        Returns:
            List of synced QboAttachable records
        """
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")

        with QboAttachableClient(realm_id=realm_id) as client:
            qbo_attachables = self._query_attachables_with_fallback(
                client, "Purchase", purchase_qbo_id, authoritative=authoritative,
            )

        logger.info(f"Fetched {len(qbo_attachables)} attachables for Purchase {purchase_qbo_id}")

        synced = []
        for qbo_att in qbo_attachables:
            try:
                local_att = self._upsert_attachable(realm_id, qbo_att)
                synced.append(local_att)
            except Exception as e:
                logger.error(f"Failed to upsert attachable {qbo_att.id}: {e}")

        if sync_to_modules:
            # Return only attachables whose blob synced healthily so callers
            # never link a missing-blob record to a line item.
            return self._sync_to_attachments(synced, realm_id)

        return synced

    def _upsert_attachable(
        self,
        realm_id: str,
        qbo_att: QboAttachableExternalSchema,
    ) -> QboAttachable:
        """
        Build a transient QboAttachable (see `QboAttachable.transient`) from a
        single QBO pull response (U-300b: the pull path no longer stages a
        `qbo.Attachable` row — identity resolution happens dbo-only in
        AttachableAttachmentConnector via `run_identity_fastpath_dbo_only`,
        mirroring U-285's push-side `_transient_attachable_from_response`).
        """
        if not qbo_att.id:
            raise ValueError("QBO Attachable must have an ID")

        # Extract entity reference (take first if multiple)
        entity_ref_type = None
        entity_ref_value = None
        if qbo_att.attachable_ref:
            first_ref = qbo_att.attachable_ref[0]
            entity_ref_type = first_ref.entity_ref_type
            entity_ref_value = first_ref.entity_ref_value

        return QboAttachable.transient(
            qbo_id=qbo_att.id,
            sync_token=qbo_att.sync_token,
            realm_id=realm_id,
            file_name=qbo_att.file_name,
            note=qbo_att.note,
            category=qbo_att.category,
            content_type=qbo_att.content_type,
            size=qbo_att.size,
            file_access_uri=qbo_att.file_access_uri,
            temp_download_uri=qbo_att.temp_download_uri,
            entity_ref_type=entity_ref_type,
            entity_ref_value=entity_ref_value,
        )

    def _sync_to_attachments(self, attachables: List[QboAttachable], realm_id: str) -> List[QboAttachable]:
        """
        Sync attachables to the Attachment module (download blob + create Attachment + mapping).

        Per-attachable failures are isolated (one bad attachable doesn't abort the rest)
        and only the attachables whose blob synced healthily are returned. Callers link
        downstream off the returned list, so a missing-blob / failed-download attachable
        is never linked to a line item.

        Args:
            attachables: List of QboAttachable records
            realm_id: QBO realm ID for downloading files

        Returns:
            The subset of attachables that synced to a healthy Attachment (blob present).
        """
        if not attachables:
            return []

        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.attachable.connector.attachment.business.service import AttachableAttachmentConnector

        connector = AttachableAttachmentConnector()

        healthy = []
        for att in attachables:
            try:
                attachment = connector.sync_from_qbo_attachable(att, realm_id)
                # U-300b: att.id is always None (transient, never persisted) —
                # att.qbo_id is the only identifier that still distinguishes
                # one attachable from another in these logs.
                logger.info(f"Synced QboAttachable qbo_id={att.qbo_id} to Attachment {attachment.id}")
                healthy.append(att)
            except (QboBudgetExceededError, QboWriteRefusedError):
                raise
            except Exception as e:
                logger.error(f"Failed to sync QboAttachable qbo_id={att.qbo_id} to Attachment: {e}")

        return healthy
