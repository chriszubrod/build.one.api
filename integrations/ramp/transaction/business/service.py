# Python Standard Library Imports
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Protocol

# Third-party Imports
import pyodbc

# Local Imports
import config
from integrations.ramp.auth.business.service import RampAuthService
from integrations.ramp.base.client import RampHttpClient
from integrations.ramp.base.logger import get_ramp_logger
from integrations.ramp.transaction.business.classify import (
    RampApprovalState,
    classify_transaction,
)
from integrations.ramp.transaction.external.client import RampTransactionExternalClient
from integrations.ramp.user.business.service import RampUserRosterEntry, RampUserService
from integrations.ramp.user.external.client import RampUserExternalClient
from entities.ramp_transaction_follow_up.persistence.repo import (
    RampTransactionFollowUpRepository,
)
from shared.database import get_connection


logger = get_ramp_logger(__name__)


class RampFollowUpRepository(Protocol):
    def upsert_open_item(self, *, conn: Optional[pyodbc.Connection] = None, **kwargs: Any) -> Any: ...

    def mark_resolved(
        self, *, ramp_transaction_id: str, conn: Optional[pyodbc.Connection] = None
    ) -> Any: ...

    def read_unresolved_ramp_transaction_ids(
        self, *, conn: Optional[pyodbc.Connection] = None
    ) -> List[str]: ...


@dataclass
class RampChaserSweepStats:
    transactions_fetched: int = 0
    stragglers_refetched: int = 0
    stragglers_gone_from_ramp: int = 0
    upserted: int = 0
    resolved: int = 0
    skipped_approval_only: int = 0
    refreshed_tracked_approval_only: int = 0
    skipped_inactive_cardholder: int = 0
    unroutable_persisted: int = 0
    flag_unknown: int = 0


def _card_holder_display_name(card_holder: Dict[str, Any]) -> Optional[str]:
    if not card_holder:
        return None
    first = (card_holder.get("first_name") or "").strip()
    last = (card_holder.get("last_name") or "").strip()
    combined = f"{first} {last}".strip()
    return combined or None


def _snapshot_from_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    card_holder = raw.get("card_holder") or {}
    amount_raw = raw.get("amount")
    amount: Optional[Decimal] = None
    if amount_raw is not None:
        amount = Decimal(str(amount_raw))

    txn_date = raw.get("user_transaction_time") or raw.get("accounting_date")

    return {
        "ramp_transaction_id": str(raw.get("id") or ""),
        "merchant_name": raw.get("merchant_name"),
        "amount": amount,
        "transaction_date": str(txn_date) if txn_date is not None else None,
        "card_holder_user_id": (
            str(card_holder.get("user_id")) if card_holder.get("user_id") is not None else None
        ),
        "card_holder_name": _card_holder_display_name(card_holder),
    }


class RampTransactionService:
    """Fetch window, classify, upsert follow-up rows, observe resolution from Ramp."""

    def __init__(
        self,
        *,
        settings: Optional[config.Settings] = None,
        auth_service: Optional[RampAuthService] = None,
        http_client: Optional[RampHttpClient] = None,
        transaction_client: Optional[RampTransactionExternalClient] = None,
        user_service: Optional[RampUserService] = None,
        follow_up_repo: Optional[RampFollowUpRepository] = None,
    ):
        self._settings = settings or config.Settings()
        self._auth = auth_service or RampAuthService(self._settings)
        base = (self._settings.ramp_api_base_url or "https://api.ramp.com").rstrip("/")
        self._http = http_client or RampHttpClient(api_base=base, auth_service=self._auth)
        self._tx_client = transaction_client or RampTransactionExternalClient(self._http)
        self._user_service = user_service or RampUserService(
            RampUserExternalClient(self._http)
        )
        self._repo = follow_up_repo

    def run_chaser_sweep(
        self,
        *,
        follow_up_repo: Optional[RampFollowUpRepository] = None,
    ) -> RampChaserSweepStats:
        repo = follow_up_repo or self._repo
        if repo is None:
            raise ValueError("follow_up_repo is required for run_chaser_sweep")

        stats = RampChaserSweepStats()

        try:
            roster = self._user_service.build_roster()

            window_days = int(self._settings.ramp_chaser_window_days or 90)
            transactions = self._tx_client.list_transactions_in_window(window_days=window_days)
            stats.transactions_fetched = len(transactions)

            window = {
                str(r["id"]): r for r in transactions if r.get("id") is not None
            }

            if isinstance(repo, RampTransactionFollowUpRepository):
                with get_connection() as conn:
                    self._process_chaser_window(
                        repo=repo,
                        conn=conn,
                        roster=roster,
                        window=window,
                        stats=stats,
                    )
            else:
                self._process_chaser_window(
                    repo=repo,
                    conn=None,
                    roster=roster,
                    window=window,
                    stats=stats,
                )
        finally:
            self._http.close()

        return stats

    def _process_chaser_window(
        self,
        *,
        repo: RampFollowUpRepository,
        conn: Optional[pyodbc.Connection],
        roster: Dict[str, RampUserRosterEntry],
        window: Dict[str, Dict[str, Any]],
        stats: RampChaserSweepStats,
    ) -> None:
        unresolved_ids = set(repo.read_unresolved_ramp_transaction_ids(conn=conn))
        for ramp_id in unresolved_ids - window.keys():
            stats.stragglers_refetched += 1
            extra = self._tx_client.get_transaction(ramp_id)
            if extra:
                window[ramp_id] = extra
            else:
                stats.stragglers_gone_from_ramp += 1
        logger.info(
            "ramp.chaser.stragglers refetched=%s gone=%s",
            stats.stragglers_refetched,
            stats.stragglers_gone_from_ramp,
        )

        for raw in window.values():
            cls = classify_transaction(raw)
            ramp_id = cls.ramp_transaction_id
            if not ramp_id:
                continue

            if cls.approval_state == RampApprovalState.COMPLETE:
                if ramp_id in unresolved_ids:
                    repo.mark_resolved(ramp_transaction_id=ramp_id, conn=conn)
                    stats.resolved += 1
                continue

            if cls.approval_state == RampApprovalState.UNKNOWN:
                stats.flag_unknown += 1
                logger.warning(
                    "ramp.chaser.flag.unknown",
                    extra={
                        "event_name": "ramp.chaser.flag.unknown",
                        "ramp_transaction_id": ramp_id,
                    },
                )
                continue

            if cls.skip_approval_only:
                if ramp_id in unresolved_ids:
                    stats.refreshed_tracked_approval_only += 1
                    logger.warning(
                        "ramp.chaser.refresh.approval_only_tracked",
                        extra={
                            "event_name": "ramp.chaser.refresh.approval_only_tracked",
                            "ramp_transaction_id": ramp_id,
                        },
                    )
                else:
                    stats.skipped_approval_only += 1
                    logger.warning(
                        "ramp.chaser.skip.approval_only",
                        extra={
                            "event_name": "ramp.chaser.skip.approval_only",
                            "ramp_transaction_id": ramp_id,
                        },
                    )
                    continue

            snapshot = _snapshot_from_raw(raw)
            card_holder_user_id = snapshot["card_holder_user_id"]
            roster_entry = roster.get(card_holder_user_id) if card_holder_user_id else None

            if roster_entry is not None and not roster_entry.is_active:
                stats.skipped_inactive_cardholder += 1
                continue

            if roster_entry is None:
                stats.unroutable_persisted += 1
                logger.warning(
                    "ramp.chaser.unroutable.missing_user",
                    extra={
                        "event_name": "ramp.chaser.unroutable.missing_user",
                        "ramp_transaction_id": ramp_id,
                        "card_holder_user_id": card_holder_user_id,
                    },
                )
            elif not roster_entry.email:
                stats.unroutable_persisted += 1
                logger.warning(
                    "ramp.chaser.unroutable.no_email",
                    extra={
                        "event_name": "ramp.chaser.unroutable.no_email",
                        "ramp_transaction_id": ramp_id,
                        "card_holder_user_id": card_holder_user_id,
                    },
                )

            repo.upsert_open_item(
                conn=conn,
                ramp_transaction_id=ramp_id,
                card_holder_ramp_user_id=card_holder_user_id,
                card_holder_name=snapshot["card_holder_name"],
                merchant_name=snapshot["merchant_name"],
                amount=snapshot["amount"],
                transaction_date=snapshot["transaction_date"],
                needs_memo=cls.needs_memo,
                needs_receipt=cls.needs_receipt,
            )
            stats.upserted += 1
