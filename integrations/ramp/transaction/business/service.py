# Python Standard Library Imports
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Protocol

# Local Imports
import config
from integrations.ramp.auth.business.service import RampAuthService
from integrations.ramp.base.client import RampHttpClient
from integrations.ramp.base.logger import get_ramp_logger
from integrations.ramp.transaction.business.classify import classify_transaction
from integrations.ramp.transaction.external.client import RampTransactionExternalClient
from integrations.ramp.user.business.service import RampUserRosterEntry, RampUserService
from integrations.ramp.user.external.client import RampUserExternalClient


logger = get_ramp_logger(__name__)


class RampFollowUpRepository(Protocol):
    def upsert_open_item(self, **kwargs: Any) -> Any: ...

    def mark_resolved(self, *, ramp_transaction_id: str) -> Any: ...

    def read_unresolved_ramp_transaction_ids(self) -> List[str]: ...


@dataclass
class RampChaserSweepStats:
    transactions_fetched: int = 0
    upserted: int = 0
    resolved: int = 0
    skipped_approval_only: int = 0
    skipped_inactive_cardholder: int = 0
    unroutable_persisted: int = 0
    user_roster_fetches: int = 0
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

        roster = self._user_service.build_roster()
        stats.user_roster_fetches = 1

        window_days = int(self._settings.ramp_chaser_window_days or 90)
        transactions = self._tx_client.list_transactions_in_window(window_days=window_days)
        stats.transactions_fetched = len(transactions)

        by_id: Dict[str, Dict[str, Any]] = {}
        for raw in transactions:
            rid = raw.get("id")
            if rid is not None:
                by_id[str(rid)] = raw

        unresolved_ids = set(repo.read_unresolved_ramp_transaction_ids())
        for ramp_id in unresolved_ids:
            if ramp_id not in by_id:
                extra = self._tx_client.get_transaction(ramp_id)
                if extra:
                    by_id[ramp_id] = extra

        for raw in by_id.values():
            cls = classify_transaction(raw)
            ramp_id = cls.ramp_transaction_id
            if not ramp_id:
                continue

            if cls.is_complete:
                if ramp_id in unresolved_ids:
                    repo.mark_resolved(ramp_transaction_id=ramp_id)
                    stats.resolved += 1
                continue

            if not cls.is_open and not cls.is_complete:
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
            roster_entry: Optional[RampUserRosterEntry] = None
            if card_holder_user_id:
                roster_entry = roster.get(card_holder_user_id)

            if roster_entry is not None and not roster_entry.is_active:
                stats.skipped_inactive_cardholder += 1
                continue

            email: Optional[str] = None
            if roster_entry is None:
                logger.warning(
                    "ramp.chaser.unroutable.missing_user",
                    extra={
                        "event_name": "ramp.chaser.unroutable.missing_user",
                        "ramp_transaction_id": ramp_id,
                        "card_holder_user_id": card_holder_user_id,
                    },
                )
            elif not roster_entry.email:
                logger.warning(
                    "ramp.chaser.unroutable.no_email",
                    extra={
                        "event_name": "ramp.chaser.unroutable.no_email",
                        "ramp_transaction_id": ramp_id,
                        "card_holder_user_id": card_holder_user_id,
                    },
                )
            else:
                email = roster_entry.email

            if email is None:
                stats.unroutable_persisted += 1

            repo.upsert_open_item(
                ramp_transaction_id=ramp_id,
                card_holder_ramp_user_id=card_holder_user_id,
                card_holder_name=snapshot["card_holder_name"],
                card_holder_email=email,
                merchant_name=snapshot["merchant_name"],
                amount=snapshot["amount"],
                transaction_date=snapshot["transaction_date"],
                needs_memo=cls.needs_memo,
                needs_receipt=cls.needs_receipt,
            )
            stats.upserted += 1

        return stats
