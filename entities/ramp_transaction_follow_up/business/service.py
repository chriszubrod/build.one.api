# Python Standard Library Imports
from typing import Optional

# Local Imports
import config
from entities.ramp_transaction_follow_up.persistence.repo import RampTransactionFollowUpRepository
from integrations.ramp.transaction.business.service import RampChaserSweepStats, RampTransactionService


class RampTransactionFollowUpService:
    """Entity façade for the chaser sweep (Phase A — no email/UI)."""

    def __init__(
        self,
        *,
        settings: Optional[config.Settings] = None,
        repo: Optional[RampTransactionFollowUpRepository] = None,
        ramp_transaction_service: Optional[RampTransactionService] = None,
    ):
        self._settings = settings or config.Settings()
        self._repo = repo or RampTransactionFollowUpRepository()
        self._ramp_tx_service = ramp_transaction_service or RampTransactionService(
            settings=self._settings
        )

    def run_chaser_sweep(self) -> RampChaserSweepStats:
        return self._ramp_tx_service.run_chaser_sweep(follow_up_repo=self._repo)
