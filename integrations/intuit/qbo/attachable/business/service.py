# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.attachable.business.model import QboAttachable
from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
from integrations.intuit.qbo.attachable.external.schemas import QboAttachable as QboAttachableExternalSchema
from integrations.intuit.qbo.attachable.persistence.repo import QboAttachableRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)


class QboAttachableService:
    """
    Service for QBO Attachable business operations.
    """

    def __init__(
        self,
        repo: Optional[QboAttachableRepository] = None,
        auth_service: Optional[QboAuthService] = None,
    ):
        """Initialize the QboAttachableService."""
        self.repo = repo or QboAttachableRepository()
        self.auth_service = auth_service or QboAuthService()
        # Per-instance snapshot of the full realm attachable list, populated lazily on the first
        # per-entity lookup and reused across the sync run (the service is created once per run).
        # Avoids re-pulling the full list per entity while making same-entity lookups authoritative.
        self._all_attachables_cache = None

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
        with QboAttachableClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
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

    def _query_attachables_with_fallback(self, client, entity_type: str, entity_id: str) -> list:
        """
        Fetch the attachables linked to one QBO entity — authoritatively.

        QBO does NOT support a WHERE on AttachableRef: the old per-entity query
        (`query_attachables_for_entity`) returned either a 400 (handled) or a misleading
        200 (all rows, or empty) that was trusted as-is, and the single-page fallback
        (`query_attachables`, MAXRESULTS 1000) silently missed any entity whose attachable
        sat past position 1000 of the ~3.3k realm total. Both produced false "no attachments".

        Fix: pull the FULL realm list once (cached on this service for the sync run; the
        service is created once per run) and filter in-memory by an EXACT (entity_ref_type,
        entity_ref_value) match — both must match. Exact type match (not startswith) prevents
        cross-type collisions where ids are only unique per type (a "PurchaseOrder" /
        "BillPayment" attachable mis-attributed to a "Purchase"/"Bill" with the same id).
        Note: this captures SAME-entity docs reliably; cross-entity (Invoice-keyed) docs are
        recovered by the separate all-realm reconcile, not here.
        """
        if self._all_attachables_cache is None:
            self._all_attachables_cache = client.query_all_attachables()
        target_type = (entity_type or "").upper()
        filtered = [
            a for a in self._all_attachables_cache
            if a.attachable_ref and any(
                ref.entity_ref_value == entity_id
                and (ref.entity_ref_type or "").upper() == target_type
                for ref in (a.attachable_ref or [])
            )
        ]
        if filtered:
            logger.info(f"Found {len(filtered)} attachables for {entity_type} {entity_id} (full-list filter)")
        return filtered

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
        with QboAttachableClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
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

        with QboAttachableClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
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
    ) -> List[QboAttachable]:
        """
        Sync attachables linked to a specific Purchase from QBO.

        Args:
            realm_id: QBO realm ID
            purchase_qbo_id: QBO Purchase ID
            sync_to_modules: If True, also sync to Attachment module

        Returns:
            List of synced QboAttachable records
        """
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")

        with QboAttachableClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_attachables = self._query_attachables_with_fallback(client, "Purchase", purchase_qbo_id)

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
        Upsert a single attachable to local database.
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

        # Check if exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_att.id, realm_id)

        if existing:
            # Update existing
            return self.repo.update_by_qbo_id(
                qbo_id=qbo_att.id,
                row_version=existing.row_version_bytes,
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
        else:
            # Create new
            return self.repo.create(
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
                logger.info(f"Synced QboAttachable {att.id} to Attachment {attachment.id}")
                healthy.append(att)
            except Exception as e:
                logger.error(f"Failed to sync QboAttachable {att.id} to Attachment: {e}")

        return healthy

    def read_by_id(self, id: int) -> Optional[QboAttachable]:
        """
        Read a QboAttachable by database ID.
        """
        return self.repo.read_by_id(id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboAttachable]:
        """
        Read a QboAttachable by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_entity_ref(
        self, entity_ref_type: str, entity_ref_value: str, realm_id: str
    ) -> List[QboAttachable]:
        """
        Read QboAttachables by entity reference.
        """
        return self.repo.read_by_entity_ref(entity_ref_type, entity_ref_value, realm_id)

    def read_by_realm_id(self, realm_id: str) -> List[QboAttachable]:
        """
        Read all QboAttachables by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)
