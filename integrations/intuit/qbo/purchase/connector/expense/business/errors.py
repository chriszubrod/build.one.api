"""Non-retryable errors for surgical QBO Purchase line recoding."""


class PurchaseChangedInQboError(Exception):
    """Raised when the live QBO Purchase SyncToken no longer matches the coding decision."""

    def __init__(
        self,
        *,
        qbo_purchase_qbo_id: str,
        expected_sync_token: str,
        actual_sync_token: str,
    ):
        self.qbo_purchase_qbo_id = qbo_purchase_qbo_id
        self.expected_sync_token = expected_sync_token
        self.actual_sync_token = actual_sync_token
        super().__init__(
            f"QBO Purchase {qbo_purchase_qbo_id} changed in QBO: "
            f"expected SyncToken {expected_sync_token}, actual {actual_sync_token}"
        )


class PurchaseRecodeMappingError(Exception):
    """Raised when SubCostCode has no QBO Item mapping required for recoding."""

    def __init__(self, *, sub_cost_code_id: int):
        self.sub_cost_code_id = sub_cost_code_id
        super().__init__(
            f"No QBO Item mapping for sub_cost_code_id={sub_cost_code_id}; cannot recode purchase line"
        )
