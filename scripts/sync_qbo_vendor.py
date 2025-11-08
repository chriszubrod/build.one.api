# Python Standard Library Imports
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Third-party Imports

# Local Imports
from scripts.sync_helper import _normalize_last_sync
from integrations.sync.business.service import SyncService
from modules.vendor.business.service import VendorService

logger = logging.getLogger(__name__)


def sync_qbo_vendor():
    """
    Sync Vendor records with QboVendor from the QBO Vendor API.

    This function:
    # TODO: list steps for this function.
    """
    # Create start time variable
    start_time = _normalize_last_sync(
        datetime.now(timezone.utc).isoformat()
    )
    logger.info(f"Start time: {start_time}")
    print(f"Start time: {start_time}")

    # Initialize the services
    sync_service = SyncService()
    vendor_service = VendorService()

    # Step [1]: Get or create a Sync record
    provider = 'qbo'
    env = 'prod'
    entity = 'vendor'

    all_syncs = sync_service.read_all()
    sync_record = next(
        (sync for sync in all_syncs if sync.provider == provider and sync.env == env and sync.entity == entity),
        None,
    )
    if not sync_record:
        logger.info(f"Creating a new Sync record for {provider} {env} {entity}")
        sync_record = sync_service.create(
            provider=provider,
            env=env,
            entity=entity,
            last_sync_datetime=None,
        )
    last_sync = _normalize_last_sync(sync_record.last_sync_datetime)
    logger.info(f"Last sync: {last_sync}")
    print(f"Last sync: {last_sync}")

    # Phase [1]: Pull QBO Vendors to Local
    # TODO: Query QBO for all vendors between the last sync and the start time.
    # TODO: for each QboVendor
    #   - query local vendor qbo vendor mapping by qbo vendor id.
    #   - if the local vendor qbo vendor mapping exists
    #       - query local vendor by id.
    #       - if the local vendor exists, test if it matches the QboVendor.
    #       - if it does not match, update the local vendor.
    #   - if the local vendor qbo vendor mapping does not exist
    #       - query local vendor by display name.
    #           - if the local vendor does not exist, create it.
    #           - create a new vendor qbo vendor mapping record.
    #       - if the local vendor exists, test if it matches the QboVendor.
    #           - if it does not match, update the local vendor.
    #           - if it matches, do not update the local vendor.
    #           - create a new vendor qbo vendor mapping record.
    #   - update the Sync record with the start time.

    # Phase [2]: Push Local Vendors to QBO
    # TODO: Query local vendors between the last sync and the start time.
    # TODO: for each local vendor
    #   - query local vendor qbo vendor mapping by qbo vendor id.
    #   - if the local vendor qbo vendor mapping exists
    #       - update the QBO vendor.
    #   - if the local vendor qbo vendor mapping does not exist
    #       - query qbo vendor by display name.
    #           - if the qbo vendor does not exist, create it.
    #           - create a new vendor qbo vendor mapping record.
    #       - if the qbo vendor exists, update it.
    #           - create a new vendor qbo vendor mapping record.
    #   - update the Sync record with the last sync datetime.

    pass


if __name__ == "__main__":
    sync_qbo_vendor()
