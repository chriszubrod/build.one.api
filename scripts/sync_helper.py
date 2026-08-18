# Python Standard Library Imports
import logging

# Third-party Imports

# Local Imports

logger = logging.getLogger(__name__)


# Shared by every sync_qbo_*.py CLI epilog that supports --start-date/--end-date
# historical batches (bill, purchase, invoice, vendorcredit) — one source of truth
# for the --end-date clamp behavior implemented in WatermarkRun._clamp_historical_stamp.
END_DATE_CLAMP_EPILOG_NOTE = (
    "Note: When --end-date is provided, the sync record timestamp is normally set to the\n"
    "end_date (end of day) so you can track progress through historical batch imports —\n"
    "UNLESS that end-of-day value would land at or after the moment this run started, in\n"
    "which case it is clamped to the run's own current-time watermark instead (protects\n"
    "the incremental cursor from a same-day/future end_date)."
)


def exit_nonzero_on_sync_failure(result: dict) -> None:
    """
    Every sync_qbo_*.py's __main__ must call this AFTER printing its result.
    A {"success": False} result that falls off the end of __main__ exits 0 —
    indistinguishable from success to a scripted historical-batch chunk loop
    or to anything else invoking these scripts as a subprocess.
    """
    status_code = result.get("status_code")
    success = (result.get("result") or {}).get("success")
    if success is False or (isinstance(status_code, int) and status_code >= 400):
        raise SystemExit(1)


def assert_cli_system_admin() -> None:
    """
    CLI sync scripts span all users by design; declare system intent so the
    per-row access guards in shared/access.py bypass for these reads.
    Mirrors what `_require_drain_secret` does for HTTP-triggered drains.

    Call this as the first statement under `if __name__ == "__main__":` in
    every sync script. Safe to call when the script is imported (it just
    sets a ContextVar) but should only be reached when the script is the
    program entry point.
    """
    from shared.authz.context import set_authz_context
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)
