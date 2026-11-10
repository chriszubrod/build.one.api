"""The 58999 "NEED TO CATEGORIZE" placeholder: one Python definition of "uncategorized" (U-652d3).

A QBO purchase line needs categorizing when it sits on the placeholder account AND has no
QBO Item ref (a recoded line keeps the label but carries an Item). The SQL twin is the
computed column qbo.PurchaseLine.IsUncodedPlaceholder (integrations/intuit/qbo/purchase/sql/
qbo.purchase.sql); every SQL site filters on `pl.[IsUncodedPlaceholder] = 1`.
"""

from typing import Optional

PLACEHOLDER_ACCOUNT_LABEL = "NEED TO CATEGORIZE"


def has_placeholder_label(account_ref_name: Optional[str]) -> bool:
    """True when the account name carries the placeholder label (case-insensitive)."""
    return bool(account_ref_name) and PLACEHOLDER_ACCOUNT_LABEL.lower() in account_ref_name.lower()


def is_uncoded_placeholder_line(
    account_ref_name: Optional[str], item_ref_value: Optional[str]
) -> bool:
    """True when the line carries the placeholder label (case-insensitive) and no Item ref.

    Mirrors the SQL column: label contains, Item ref empty or NULL.
    """
    return has_placeholder_label(account_ref_name) and not item_ref_value
