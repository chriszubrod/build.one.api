"""QBO Purchase line BillableStatus for a reviewer's billable judgement (U-743b).

Neutral module (no entity or connector imports) so both the expense completion divergence
check and the purchase connector can share it without an import cycle.
"""
from typing import Optional

_STATUS_BY_JUDGEMENT = {True: "Billable", False: "NotBillable"}


def desired_billable_status(is_billable: Optional[bool]) -> Optional[str]:
    """``True`` -> "Billable", ``False`` -> "NotBillable", anything else (no judgement) -> ``None``."""
    if is_billable is True or is_billable is False:
        return _STATUS_BY_JUDGEMENT[is_billable]
    return None
