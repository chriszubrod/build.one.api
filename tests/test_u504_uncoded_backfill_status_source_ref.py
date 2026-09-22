"""U-504 — namespaced StatusSourceRef for uncoded-draft backfill provenance."""

from __future__ import annotations

import hashlib
import re
from unittest.mock import MagicMock

import pytest

from entities.expense.business.service import (
    ExpenseService,
    _uncoded_backfill_status_source_ref,
)
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments
from tests.test_u491_uncoded_candidates_dbo_native import (
    DETECTION_OUTER_SELECT_COLUMNS,
    DETECTION_SPROC,
    EXPENSE_SQL,
    _detection_sproc_body_stripped,
)

EXPENSE_REPO_PY = REPO_ROOT / "entities/expense/persistence/repo.py"
EXPENSE_SERVICE_PY = REPO_ROOT / "entities/expense/business/service.py"

# Keys the backfill helper reads beyond the always-present id/public_id fields.
BACKFILL_PROVENANCE_ITEM_KEYS = frozenset(
    {
        "coding_item_public_id",
        "realm_id",
        "purchase_qbo_id",
        "qbo_line_id",
        "qbo_purchase_line_id",
    }
)

_FULL_QBO_REF = "qbo:9130353016965726/76207#3"
_FULL_ITEM = {
    "id": 1,
    "realm_id": "9130353016965726",
    "purchase_qbo_id": "76207",
    "qbo_line_id": "3",
    "qbo_purchase_line_id": 13903,
    "coding_item_public_id": None,
}


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _outer_select_columns_from_sproc() -> list[str]:
    body = _detection_sproc_body_stripped()
    match = re.search(
        r"\)\s*SELECT\s+(.*?)\s+FROM Ranked\s+WHERE \[Rn\] = 1",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None
    return [c.strip() for c in match.group(1).split(",")]


# ---------------------------------------------------------------------------
# 1 — detector projects RealmId, PurchaseQboId, QboLineId (11-column pin)
# ---------------------------------------------------------------------------


def test_detection_sproc_projects_qbo_provenance_columns_in_pinned_order():
    cols = _outer_select_columns_from_sproc()
    assert cols == DETECTION_OUTER_SELECT_COLUMNS
    assert len(cols) == 11


def _detection_body_from_sql_text(sql_text: str) -> str:
    match = re.search(
        rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?{DETECTION_SPROC}\b(.*?)^GO\s*$",
        sql_text,
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )
    assert match is not None
    return strip_sql_comments(match.group(1))


def test_u504_mutation_s1_drop_realm_id_column_goes_red():
    original = EXPENSE_SQL.read_text()
    mutated = original.replace("        [RealmId],\n", "", 1)
    assert _md5(mutated) != _md5(original)
    body = _detection_body_from_sql_text(mutated)
    match = re.search(
        r"\)\s*SELECT\s+(.*?)\s+FROM Ranked\s+WHERE \[Rn\] = 1",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    cols = [c.strip() for c in match.group(1).split(",")]
    with pytest.raises(AssertionError):
        assert cols == DETECTION_OUTER_SELECT_COLUMNS


# ---------------------------------------------------------------------------
# 2 — fallback emits qbo:{realm}/{purchase}#{line} (full string)
# ---------------------------------------------------------------------------


def test_uncoded_backfill_qbo_fallback_ref_full_string():
    ref = _uncoded_backfill_status_source_ref(_FULL_ITEM)
    # qbo: 1, /: 1, #: 1 — substring checks alone would stay green on most mutations.
    assert ref.count("qbo:") == 1
    assert ref.count("/") == 1
    assert ref.count("#") == 1
    assert ref == _FULL_QBO_REF


def test_u504_mutation_s2_hash_separator_to_slash_goes_red():
    original = EXPENSE_SERVICE_PY.read_text()
    mutated = original.replace(
        'return f"qbo:{realm_id}/{purchase_qbo_id}#{qbo_line_id}"',
        'return f"qbo:{realm_id}/{purchase_qbo_id}/{qbo_line_id}"',
        1,
    )
    assert _md5(mutated) != _md5(original)
    ns: dict = {}
    exec(  # noqa: S102 — in-memory mutation of helper only
        mutated.split("class ExpenseService:")[0]
        + "\nresult = _uncoded_backfill_status_source_ref("
        + repr(_FULL_ITEM)
        + ")\n",
        ns,
    )
    with pytest.raises(AssertionError):
        assert ns["result"] == _FULL_QBO_REF


# ---------------------------------------------------------------------------
# 3 — coding_item_public_id wins over QBO fallback
# ---------------------------------------------------------------------------


def test_uncoded_backfill_coding_item_public_id_precedence():
    coding_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    item = {**_FULL_ITEM, "coding_item_public_id": coding_id}
    assert _uncoded_backfill_status_source_ref(item) == coding_id


def test_u504_mutation_s3_swap_precedence_goes_red():
    original = EXPENSE_SERVICE_PY.read_text()
    old = (
        "    coding_ref = item.get(\"coding_item_public_id\")\n"
        "    if coding_ref is not None:\n"
        "        return coding_ref\n"
        "\n"
        "    realm_id = item.get(\"realm_id\")\n"
    )
    new = (
        "    realm_id = item.get(\"realm_id\")\n"
        "    purchase_qbo_id = item.get(\"purchase_qbo_id\")\n"
        "    qbo_line_id = item.get(\"qbo_line_id\")\n"
        "    if (\n"
        "        realm_id\n"
        "        and purchase_qbo_id\n"
        "        and qbo_line_id is not None\n"
        "        and str(qbo_line_id) != \"\"\n"
        "    ):\n"
        "        return f\"qbo:{realm_id}/{purchase_qbo_id}#{qbo_line_id}\"\n"
        "\n"
        "    coding_ref = item.get(\"coding_item_public_id\")\n"
        "    if coding_ref is not None:\n"
        "        return coding_ref\n"
        "\n"
        "    purchase_qbo_id = item.get(\"purchase_qbo_id\")\n"
    )
    mutated = original.replace(old, new, 1)
    assert _md5(mutated) != _md5(original)
    ns: dict = {}
    exec(
        mutated.split("class ExpenseService:")[0]
        + "\nresult = _uncoded_backfill_status_source_ref("
        + repr({**_FULL_ITEM, "coding_item_public_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"})
        + ")\n",
        ns,
    )
    with pytest.raises(AssertionError):
        assert ns["result"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ---------------------------------------------------------------------------
# 4 — missing QBO component does not emit a malformed qbo:… ref
# ---------------------------------------------------------------------------


def test_uncoded_backfill_missing_qbo_component_returns_none_not_malformed():
    for key in ("realm_id", "purchase_qbo_id", "qbo_line_id"):
        item = {**_FULL_ITEM, key: None}
        ref = _uncoded_backfill_status_source_ref(item)
        assert ref is None


def test_u504_mutation_s4_half_formed_qbo_ref_goes_red():
    original = EXPENSE_SERVICE_PY.read_text()
    mutated = original.replace(
        "    return None\n\n\nclass ExpenseService:",
        "    return (\n"
        '        f"qbo:{realm_id if realm_id is not None else \'\'}"\n'
        '        f"/{purchase_qbo_id if purchase_qbo_id is not None else \'\'}"\n'
        '        f"#{qbo_line_id if qbo_line_id is not None else \'\'}"\n'
        "    )\n\n\nclass ExpenseService:",
        1,
    )
    assert _md5(mutated) != _md5(original)
    ns: dict = {"Optional": __import__("typing").Optional}
    exec(  # noqa: S102
        mutated.split("class ExpenseService:")[0]
        + "\nresult = _uncoded_backfill_status_source_ref("
        + repr({**_FULL_ITEM, "realm_id": None})
        + ")\n",
        ns,
    )
    with pytest.raises(AssertionError):
        assert ns["result"] is None


# ---------------------------------------------------------------------------
# 5 — repo dict keys match what the service reads
# ---------------------------------------------------------------------------


def test_repo_uncoded_candidates_maps_provenance_keys_for_service():
    repo_src = EXPENSE_REPO_PY.read_text()
    for key in BACKFILL_PROVENANCE_ITEM_KEYS:
        assert f'"{key}":' in repo_src


def test_backfill_apply_passes_namespaced_ref_from_repo_shaped_item():
    svc = ExpenseService()
    svc.repo = MagicMock()
    svc.repo.read_uncoded_completed_candidates.return_value = [_FULL_ITEM]
    svc.repo.mark_draft_for_coding.return_value = MagicMock()

    svc.backfill_uncoded_to_draft(dry_run=False)

    svc.repo.mark_draft_for_coding.assert_called_once_with(
        _FULL_ITEM["id"],
        status_source_ref=_FULL_QBO_REF,
    )


def test_u504_mutation_s5_rename_repo_key_goes_red():
    original = EXPENSE_REPO_PY.read_text()
    mutated = original.replace('"realm_id":', '"realmId":', 1)
    assert _md5(mutated) != _md5(original)
    with pytest.raises(AssertionError):
        assert '"realm_id":' in mutated
