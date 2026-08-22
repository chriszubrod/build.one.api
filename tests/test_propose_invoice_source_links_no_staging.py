"""U-301c guard: ProposeInvoiceSourceLinks' Tier 0c/0d must never revert to
reading qbo.* staging.

Tier 0c/0d were repointed off qbo.Bill/qbo.BillLine/qbo.BillLineItemBillLine
and qbo.Purchase/qbo.PurchaseLine/qbo.PurchaseLineExpenseLineItem onto
dbo.Bill/dbo.Expense's own native QboId/RealmId (U-238a) plus an amount
fingerprint against that header's own dbo line items. Zero live rows use these
tiers today (no LinkedTxnType='Bill'/'Purchase' provenance rows exist), so
there is no live-traffic canary to catch an accidental revert (a merge
conflict, a "cleanup", a regenerated base file) — this is a pure text check,
no DB required, cheap enough to run on every test invocation.

Full functional verification (the SQL actually resolves correctly, including
the individually-mapped-vs-unmapped-sibling precision gate) lives in
scripts/verify_propose_invoice_source_links_tier0.py, which needs live DB
write access and is not pytest-collected (tests/conftest.py blocks real
pyodbc.connect() from the suite, per the U-295 incident). This test imports
that script's extraction helper + banned-table list rather than re-deriving
them, so the two checks can't silently drift apart.
"""
from scripts.verify_propose_invoice_source_links_tier0 import (
    BANNED_STAGING_REFS,
    extract_sproc_body,
)


def test_propose_invoice_source_links_has_no_qbo_staging_reach():
    body = extract_sproc_body()
    for banned in BANNED_STAGING_REFS:
        assert banned not in body, (
            f"ProposeInvoiceSourceLinks reverted to reading {banned} — "
            f"Tier 0c/0d must resolve via dbo.Bill/dbo.Expense native "
            f"QboId/RealmId, not qbo.* staging (U-301c)"
        )


def test_propose_invoice_source_links_tier0_gates_on_line_qboid():
    """U-301c fix: Tier 0c/0d must require the specific dbo line to itself
    carry a QboId (individually synced), not just fingerprint-match any
    sibling line under the identified header — else an unmapped sibling
    sharing a mapped line's amount turns a clean match into a false
    'ambiguous' (live-verified regression, Bill.Id=16897)."""
    body = extract_sproc_body()
    assert "dbli.[QboId] IS NOT NULL" in body
    assert "deli.[QboId] IS NOT NULL" in body
