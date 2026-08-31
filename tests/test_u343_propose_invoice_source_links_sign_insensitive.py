"""U-343 guard: ProposeInvoiceSourceLinks' Tier 0c/0d/1/2 amount comparisons
must use the double-ABS (KI-34) sign-insensitive form, matching the
VendorCredit tier (Tier 3) that already had it.

A refund line's source Amount (BillLineItem/ExpenseLineItem) is stored
positive while the invoice line's own QboAmount is negative for the same
refund -- a signed `ABS(x - y) < 0.01` comparison never matches it, producing
a false "no source" halt (6 real refund lines hit this on TB3-20,
2026-08-07). KI-34 fixed this for the VendorCredit tier only
(`ABS(ABS(bcli.[Amount]) - ABS(lc.[QboAmount])) < 0.01`); U-343 extends the
same double-ABS form to the 4 remaining amount comparisons: Tier 0c (Bill
header-direct), Tier 0d (Purchase header-direct), Tier 1 (Bill fingerprint),
Tier 2 (Purchase fingerprint).

Zero live InvoiceLineItemSourceProvenance rows exercise a refund shape on
these 4 tiers as of 2026-08-31 (rare-path), so this is a pure text check, no
DB required -- same rationale and the same extract_sproc_body() helper as
tests/test_propose_invoice_source_links_no_staging.py, so the two checks
can't silently drift apart.
"""
import re

from scripts.verify_propose_invoice_source_links_tier0 import extract_sproc_body

# One amount comparison expected per source-line alias, in tier order:
# Tier 0c (dbli), Tier 0d (deli), Tier 1 (bli), Tier 2 (eli), Tier 3 (bcli).
_SOURCE_ALIASES = ("dbli", "deli", "bli", "eli", "bcli")


def test_all_source_amount_comparisons_are_double_abs_sign_insensitive():
    body = extract_sproc_body()
    for alias in _SOURCE_ALIASES:
        double_abs = re.compile(
            rf"ABS\(ABS\({alias}\.\[Amount\]\)\s*-\s*ABS\(lc\.\[QboAmount\]\)\)\s*<\s*0\.01"
        )
        assert double_abs.search(body), (
            f"ProposeInvoiceSourceLinks amount comparison for {alias} is not "
            f"double-ABS (KI-34) sign-insensitive -- a refund line (positive "
            f"source Amount, negative invoice QboAmount) would never match, "
            f"producing a false 'no source' halt (U-343, TB3-20)."
        )
