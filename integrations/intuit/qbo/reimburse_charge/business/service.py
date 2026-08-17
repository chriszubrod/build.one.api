# Python Standard Library Imports
import logging
import time
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.reimburse_charge.business.model import QboReimburseCharge
from integrations.intuit.qbo.reimburse_charge.business.parse import (
    merge_reimburse_charge,
    parse_reimburse_charge,
)
from integrations.intuit.qbo.reimburse_charge.persistence.repo import QboReimburseChargeRepository
from integrations.intuit.qbo.invoice.external.client import (
    QboInvoiceClient,
    reject_reimburse_charge_txndate_filter,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process reimburse charges in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboReimburseChargeService:
    """
    Service for QboReimburseCharge staging (U-186).

    Upsert-only capture of QBO ReimburseCharges: NO module / Excel / Box / QBO
    fan-out. Its sole job is to keep qbo.ReimburseCharge current for invoice-line
    linking and to PRESERVE any stored source pointer across re-pulls
    (defensive/forward-compatible — QBO does not currently expose a reverse
    Bill/Purchase LinkedTxn; see docs/rc_source_linking_signal_2026_08_16.md).
    """

    def __init__(self, repo: Optional[QboReimburseChargeRepository] = None):
        """Initialize the QboReimburseChargeService."""
        self.repo = repo or QboReimburseChargeRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> SyncOutcome[QboReimburseCharge]:
        """
        Fetch ReimburseCharges from QBO and upsert into staging.

        Args:
            realm_id: QBO company realm ID.
            last_updated_time: Optional ISO datetime; only RCs with
                Metadata.LastUpdatedTime > this are fetched (incremental).
            start_date / end_date: Unsupported — QBO rejects TxnDate filters on
                ReimburseCharge; passing either raises QboValidationError.

        Returns:
            SyncOutcome[QboReimburseCharge]: Pull run envelope including synced staging rows

        WATERMARK CONTRACT: ``staging_failed_ids`` holds the watermark on any RC
        that failed to persist so the caller can idempotently re-pull the same
        window on the next tick. This is conservative good practice regardless of
        QBO pointer behavior (the original KI-32 one-shot rationale is not
        supported by measurement — see docs/rc_source_linking_signal_2026_08_16.md).
        """
        reject_reimburse_charge_txndate_filter(start_date, end_date)
        outcome: SyncOutcome[QboReimburseCharge] = SyncOutcome.for_service_pull()
        with QboInvoiceClient(realm_id=realm_id) as client:
            raw_records = client.query_all_reimburse_charges(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
            )

        if not raw_records:
            logger.info(f"No ReimburseCharges found since {last_updated_time or 'beginning'}")
            outcome.fetched = 0
            return outcome

        outcome.fetched = len(raw_records)
        logger.info(f"Retrieved {len(raw_records)} reimburse charges from QBO")

        for i, raw in enumerate(raw_records):
            parsed = parse_reimburse_charge(raw)
            if not parsed.get("qbo_id"):
                # Malformed RC (QBO entity with no Id) — can never persist or be
                # retried, so it does NOT hold the watermark (would stall forever).
                logger.warning(f"Skipping ReimburseCharge with no Id: {raw}")
                outcome.record_staging_skip("<no-id>", "ReimburseCharge with no Id")
                continue
            try:
                record = with_retry(
                    self._upsert,
                    parsed,
                    realm_id,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                outcome.record_synced(record)
                logger.debug(f"Upserted reimburse charge {parsed['qbo_id']} ({i + 1}/{len(raw_records)})")
            except Exception as e:
                # Transient persistence failure — hold the watermark so the
                # window is re-pulled idempotently on the next tick.
                logger.error(f"Failed to upsert reimburse charge {parsed.get('qbo_id')}: {e}")
                outcome.record_staging_failure(parsed["qbo_id"], e)

            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(raw_records):
                logger.debug(f"Processed {i + 1}/{len(raw_records)} reimburse charges, pausing...")
                time.sleep(BATCH_DELAY)

        if outcome.staging_failed_ids:
            logger.warning(
                f"Failed to upsert {len(outcome.staging_failed_ids)} reimburse charges: "
                f"{outcome.staging_failed_ids}"
            )

        return outcome

    def _upsert(self, parsed: dict, realm_id: str) -> QboReimburseCharge:
        """
        Create or update one staging record from a parsed RC dict.

        On update, `merge_reimburse_charge` preserves a previously-stored
        source pointer when the incoming parse carries NULL (defensive —
        QBO does not currently populate these fields; see
        docs/rc_source_linking_signal_2026_08_16.md), belt-and-suspenders
        with the sproc's CASE-WHEN-preserve.
        """
        qbo_id = parsed["qbo_id"]
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_id, realm_id=realm_id)

        if existing:
            merged = merge_reimburse_charge(
                stored={
                    "source_txn_type": existing.source_txn_type,
                    "source_txn_id": existing.source_txn_id,
                    "source_txn_line_id": existing.source_txn_line_id,
                },
                incoming=parsed,
            )
            logger.debug(f"Updating existing QBO reimburse charge {qbo_id}")
            return self.repo.update_by_qbo_id(
                qbo_id=qbo_id,
                row_version=existing.row_version_bytes,
                realm_id=realm_id,
                customer_ref_value=merged["customer_ref_value"],
                customer_ref_name=merged["customer_ref_name"],
                txn_date=merged["txn_date"],
                amount=merged["amount"],
                has_been_invoiced=merged["has_been_invoiced"],
                source_txn_type=merged["source_txn_type"],
                source_txn_id=merged["source_txn_id"],
                source_txn_line_id=merged["source_txn_line_id"],
            )

        logger.debug(f"Creating new QBO reimburse charge {qbo_id}")
        return self.repo.create(
            qbo_id=qbo_id,
            realm_id=realm_id,
            customer_ref_value=parsed["customer_ref_value"],
            customer_ref_name=parsed["customer_ref_name"],
            txn_date=parsed["txn_date"],
            amount=parsed["amount"],
            has_been_invoiced=parsed["has_been_invoiced"],
            source_txn_type=parsed["source_txn_type"],
            source_txn_id=parsed["source_txn_id"],
            source_txn_line_id=parsed["source_txn_line_id"],
        )

    def read_by_realm_id(self, realm_id: str) -> List[QboReimburseCharge]:
        """Read all QboReimburseCharges by realm ID."""
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboReimburseCharge]:
        """Read a QboReimburseCharge by QBO ID and realm ID."""
        return self.repo.read_by_qbo_id_and_realm_id(qbo_id, realm_id)
