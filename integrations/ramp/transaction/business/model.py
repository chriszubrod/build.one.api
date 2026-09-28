# Python Standard Library Imports
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class RampTransactionSnapshot:
    ramp_transaction_id: str
    merchant_name: Optional[str]
    amount: Optional[Decimal]
    transaction_date: Optional[str]
    card_holder_user_id: Optional[str]
    card_holder_name: Optional[str]
