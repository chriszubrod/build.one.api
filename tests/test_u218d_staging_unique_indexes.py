"""U-218d guard: QBO staging tables declare filtered unique (QboId, RealmId) indexes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Eight tables that gained U-218d filtered unique indexes in base SQL files.
U218D_STAGING_BASE_FILES: dict[str, Path] = {
    "Bill": REPO_ROOT / "integrations/intuit/qbo/bill/sql/qbo.bill.sql",
    "Purchase": REPO_ROOT / "integrations/intuit/qbo/purchase/sql/qbo.purchase.sql",
    "Vendor": REPO_ROOT / "integrations/intuit/qbo/vendor/sql/qbo.vendor.sql",
    "Customer": REPO_ROOT / "integrations/intuit/qbo/customer/sql/qbo.customer.sql",
    "Item": REPO_ROOT / "integrations/intuit/qbo/item/sql/qbo.item.sql",
    "Account": REPO_ROOT / "integrations/intuit/qbo/account/sql/qbo.account.sql",
    "Term": REPO_ROOT / "integrations/intuit/qbo/term/sql/qbo.term.sql",
    "ReimburseCharge": REPO_ROOT
    / "integrations/intuit/qbo/reimburse_charge/sql/qbo.reimburse_charge.sql",
}

U218D_EXPECTED_INDEX_NAMES: dict[str, str] = {
    entity: f"UQ_Qbo{entity}_QboId_RealmId" for entity in U218D_STAGING_BASE_FILES
}

PREEXISTING_UNIQUE_INDEX_FILES: dict[str, tuple[Path, str]] = {
    "VendorCredit": (
        REPO_ROOT / "integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql",
        "UQ_QboVendorCredit_QboId_RealmId",
    ),
    "Invoice": (
        REPO_ROOT / "integrations/intuit/qbo/invoice/sql/qbo.invoice.sql",
        "UQ_QboInvoice_QboId_RealmId",
    ),
    "Attachable": (
        REPO_ROOT / "integrations/intuit/qbo/attachable/sql/qbo.attachable.sql",
        "UX_QboAttachable_QboId_RealmId",
    ),
}

_FILTERED_UNIQUE_RE = re.compile(
    r"CREATE\s+UNIQUE\s+INDEX\s+(?P<name>\w+)\s+ON\s+\[qbo\]\.\[(?P<table>\w+)\]\s+"
    r"\(\[QboId\],\s*\[RealmId\]\)\s+WHERE\s+\[QboId\]\s+IS\s+NOT\s+NULL\s+"
    r"AND\s+\[RealmId\]\s+IS\s+NOT\s+NULL",
    re.IGNORECASE,
)

_ANY_UNIQUE_QBOID_REALM_RE = re.compile(
    r"CREATE\s+UNIQUE\s+INDEX\s+(?P<name>\w+)\s+ON\s+\[qbo\]\.\[\w+\]\s+"
    r"\(\[QboId\],\s*\[RealmId\]\)",
    re.IGNORECASE,
)


def _parse_filtered_unique_indexes(sql_text: str) -> list[tuple[str, str]]:
    return [(m.group("name"), m.group("table")) for m in _FILTERED_UNIQUE_RE.finditer(sql_text)]


def _parse_any_unique_qbo_realm_indexes(sql_text: str) -> list[str]:
    return [m.group("name") for m in _ANY_UNIQUE_QBOID_REALM_RE.finditer(sql_text)]


def test_u218d_discovery_non_vacuous() -> None:
    """Fail loudly if the file map is empty or paths are missing."""
    assert len(U218D_STAGING_BASE_FILES) == 8, "expected exactly 8 U-218d staging base files"
    missing = [name for name, path in U218D_STAGING_BASE_FILES.items() if not path.is_file()]
    assert not missing, f"U-218d base SQL files not found: {missing}"


@pytest.mark.parametrize("entity", sorted(U218D_STAGING_BASE_FILES))
def test_u218d_staging_base_declares_filtered_unique_index(entity: str) -> None:
    path = U218D_STAGING_BASE_FILES[entity]
    text = path.read_text(encoding="utf-8")
    matches = _parse_filtered_unique_indexes(text)
    expected_name = U218D_EXPECTED_INDEX_NAMES[entity]
    matching = [m for m in matches if m[0] == expected_name and m[1] == entity]
    assert matching, (
        f"{path.relative_to(REPO_ROOT)}: expected filtered unique index "
        f"{expected_name} on [qbo].[{entity}] ([QboId], [RealmId]); "
        f"found filtered matches={matches!r}"
    )


@pytest.mark.parametrize("entity,spec", sorted(PREEXISTING_UNIQUE_INDEX_FILES.items()))
def test_preexisting_staging_unique_indexes_still_declared(
    entity: str, spec: tuple[Path, str]
) -> None:
    path, expected_name = spec
    assert path.is_file(), f"pre-existing base SQL missing: {path}"
    text = path.read_text(encoding="utf-8")
    names = _parse_any_unique_qbo_realm_indexes(text)
    assert expected_name in names, (
        f"{path.relative_to(REPO_ROOT)}: expected unique (QboId, RealmId) index "
        f"{expected_name}; found {names!r}"
    )


def test_eleven_staging_unique_indexes_total() -> None:
    """Eight new U-218d indexes plus three pre-existing ones = 11 guarded declarations."""
    all_paths = list(U218D_STAGING_BASE_FILES.values()) + [
        spec[0] for spec in PREEXISTING_UNIQUE_INDEX_FILES.values()
    ]
    assert len(all_paths) == 11
    found_filtered = 0
    found_preexisting = 0
    for path in U218D_STAGING_BASE_FILES.values():
        found_filtered += len(_parse_filtered_unique_indexes(path.read_text(encoding="utf-8")))
    for path, _ in PREEXISTING_UNIQUE_INDEX_FILES.values():
        found_preexisting += len(_parse_any_unique_qbo_realm_indexes(path.read_text(encoding="utf-8")))
    assert found_filtered == 8, f"expected 8 filtered unique indexes, found {found_filtered}"
    assert found_preexisting == 3, f"expected 3 pre-existing unique indexes, found {found_preexisting}"
