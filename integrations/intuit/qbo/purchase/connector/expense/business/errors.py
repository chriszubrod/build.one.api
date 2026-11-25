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


class RepointLineBilledError(Exception):
    """U-743: a completion repoint asked to change the customer of a line QBO has already billed (HasBeenBilled).
    Raised before any write; the drain marks the suggestion error with ``WRITE_ERROR``."""

    WRITE_ERROR = "repoint_line_billed"  # the suggestion's write_error; redrive_error_repoint refuses it

    def __init__(self, *, qbo_line_id: str):
        self.qbo_line_id = qbo_line_id
        super().__init__(f"Purchase line {qbo_line_id} is HasBeenBilled; refusing a repoint customer change")
