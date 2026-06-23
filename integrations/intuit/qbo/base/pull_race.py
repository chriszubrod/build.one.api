"""Shared guard + re-read helper for the QBO pull-race that can mint half-built entities.

A real QBO Bill / Purchase / VendorCredit with a non-zero total ALWAYS has lines. An
empty line list paired with a non-zero header means the lines weren't committed/visible
yet — a CROSS-PROCESS staging write race (one sync delete+reinserts a bill's qbo.*Line
rows while another reads them). Projecting in that window creates a header-only entity
(header total != sum of lines) AND a mapping, so a plain re-pull never re-selects it.

Incident (2026-06-23): a global sweep found 139 half-built Bills + 243 half-built Expenses
created this way (see project_qbo_pull_p0_fixes memory).

Two-part defense:
  * Connectors call `guard_lines_present()` — a LAST-RESORT raise so no caller can ever
    persist a header-only entity. Non-transient by design (see below).
  * Pull scripts call `read_lines_riding_out_race()` BEFORE projecting — re-reads the
    staging lines a few times to ride out the (sub-second-to-seconds) race, then either
    projects (lines arrived) or DEFERS the row (skip so the watermark advances). This keeps
    a genuinely line-less QBO record (TotalAmt>0, no Line array) from wedging the watermark
    forever. Recovery for the rare permanent case: the deferred row is logged (WARNING), and
    for BILLS the daily QBO reconcile counts unprojected bills (auto-recreate gated on
    QBO_RECONCILE_BILL_AUTOFIX). Purchase/VendorCredit have no reconciler yet — a permanently
    line-less one is logged only (TODO.md: "QBO reconcilers for purchase/vendorcredit").
"""
import time


def header_has_amount(total) -> bool:
    """True when a header total is meaningfully non-zero (the race-guard threshold).

    `total` may be Decimal / float / None. float() here is a magnitude comparison only
    (never persisted), so it does not violate the Decimal-precision rule.
    """
    return bool(total) and abs(float(total)) > 0.01


def guard_lines_present(lines, total, *, entity_label: str, entity_id, qbo_id) -> None:
    """Raise if a non-zero-header entity arrived with NO lines (almost certainly a pull race).

    Intentionally NOT a transient-coded error: `with_retry` won't auto-retry the same empty
    list. Callers that can re-read the staging lines should do so via
    `read_lines_riding_out_race`; the scheduler's watermark machinery otherwise holds + re-pulls
    next tick (re-pull is idempotent). The pull scripts pre-empt this by deferring empty rows,
    so in practice this only fires for callers (e.g. the reconciler) that don't pre-read.
    """
    if not lines and header_has_amount(total):
        raise RuntimeError(
            f"No lines for {entity_label} {entity_id} (qbo_id={qbo_id}) with non-zero "
            f"header total {total} — likely a QBO pull race; holding for retry."
        )


def read_lines_riding_out_race(read_fn, entity_id, total, *, attempts: int = 3, delay: float = 2.0):
    """Read staging lines, re-reading a few times to ride out the cross-process pull-race.

    Only re-reads when the header is non-zero (an empty list there is suspect); a genuinely
    zero-total entity returns immediately. Returns the lines, which may STILL be empty if the
    race didn't resolve (caller should then defer the row). `read_fn(entity_id)` opens/closes
    its own DB connection, so the sleeps hold no connection.
    """
    lines = read_fn(entity_id)
    if header_has_amount(total):
        tries = 0
        while not lines and tries < attempts:
            time.sleep(delay)
            lines = read_fn(entity_id)
            tries += 1
    return lines
