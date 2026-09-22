"""U-498 — one shared test for Expense↔Purchase↔PurchaseLine↔ExpenseLineItem identity legs.

Pure source pins (no prod objects, no cross-schema view). Three shapes:
  A — 6 legs, expense-driven (payment-type SQL, ReadUncodedCompletedExpenseCandidates)
  B — 7 legs, coding-item-driven (+ pl.Id = eci.QboPurchaseLineId)
  C — worker two-hop dbo read_by_qbo_identity (no staging SQL joins)

Site 4 legs are taken only from ReadExpenseCodingStateByExpenseIds so unrelated
edits elsewhere in dbo.expense_coding_item.sql do not affect extraction.
If U-497b changes shape B's staging-PK entry leg (pl.Id = eci.QboPurchaseLineId),
revisit SHAPE_B_ENTRY and test_shape_b_sites_carry_shared_six_plus_entry_leg.
"""

from __future__ import annotations

import hashlib
import inspect
import re
from pathlib import Path

import pytest

from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

EXPENSE_SERVICE_PY = REPO_ROOT / "entities/expense/business/service.py"
EXPENSE_SQL = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
EXPENSE_CODING_SQL = REPO_ROOT / "entities/expense_coding_item/sql/dbo.expense_coding_item.sql"

READ_UNCODED_SPROC = "ReadUncodedCompletedExpenseCandidates"
READ_STATE_SPROC = "ReadExpenseCodingStateByExpenseIds"

# Canonical unordered column pairs (table alias lowercased, bracket column names).
Leg = tuple[tuple[str, str], tuple[str, str]]

SHARED_SIX: frozenset[Leg] = frozenset(
    {
        (("e", "Id"), ("eli", "ExpenseId")),
        (("e", "QboId"), ("p", "QboId")),
        (("e", "RealmId"), ("p", "RealmId")),
        (("p", "Id"), ("pl", "QboPurchaseId")),
        (("eli", "QboId"), ("pl", "QboLineId")),
        (("eli", "RealmId"), ("p", "RealmId")),
    }
)

SHAPE_B_ENTRY: Leg = (("eci", "QboPurchaseLineId"), ("pl", "Id"))
SHAPE_A_LEGS = SHARED_SIX
SHAPE_B_LEGS = SHARED_SIX | {SHAPE_B_ENTRY}

IDENTITY_ALIASES = frozenset({"e", "eli", "p", "pl", "eci"})


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _split_top_level_and(expr: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    i = 0
    s = expr
    while i < len(s):
        if s[i] == "(":
            depth += 1
            buf.append(s[i])
            i += 1
            continue
        if s[i] == ")":
            depth -= 1
            buf.append(s[i])
            i += 1
            continue
        if depth == 0 and s[i : i + 3].upper() == "AND":
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            i += 3
            continue
        buf.append(s[i])
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_simple_equality_leg(fragment: str) -> Leg | None:
    m = re.match(
        r"(\w+)\.\[(\w+)\]\s*=\s*(\w+)\.\[(\w+)\]\s*$",
        fragment.strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    left = (m.group(1).lower(), m.group(2))
    right = (m.group(3).lower(), m.group(4))
    return tuple(sorted((left, right)))


def extract_inner_identity_legs(sql: str) -> frozenset[Leg]:
    """Identity legs from INNER JOIN ON clauses among e/eli/p/pl/eci only."""
    normalized = strip_sql_comments(sql)
    legs: set[Leg] = set()
    for join_match in re.finditer(
        r"INNER\s+JOIN\s+(?:dbo\.|qbo\.)?\[\w+\]\s+(\w+)\s+ON\s+",
        normalized,
        re.IGNORECASE,
    ):
        start = join_match.end()
        rest = normalized[start:]
        end_match = re.search(
            r"\s+(?:INNER|LEFT|RIGHT|FULL|CROSS)\s+JOIN\b|\s+WHERE\b",
            rest,
            re.IGNORECASE,
        )
        on_clause = rest[: end_match.start()] if end_match else rest
        for fragment in _split_top_level_and(on_clause):
            leg = _parse_simple_equality_leg(fragment)
            if leg is None:
                continue
            (t1, _), (t2, _) = leg
            if t1 in IDENTITY_ALIASES and t2 in IDENTITY_ALIASES:
                legs.add(leg)
    return frozenset(legs)


def _execute_sql_in_function(source: str, func_name: str) -> str:
    match = re.search(
        rf"def {func_name}\b.*?cursor\.execute\(\s*\"\"\"\s*\n(.*?)\"\"\"",
        source,
        re.DOTALL,
    )
    assert match is not None, f"no cursor.execute SQL in {func_name}"
    return match.group(1)


def _payment_type_sql() -> str:
    return _execute_sql_in_function(
        EXPENSE_SERVICE_PY.read_text(), "_read_qbo_purchase_payment_type"
    )


def _enqueue_recode_sql() -> str:
    return _execute_sql_in_function(
        EXPENSE_SERVICE_PY.read_text(), "enqueue_coding_recode_on_submit"
    )


def _read_uncoded_identity_sql() -> str:
    body = sproc_body(EXPENSE_SQL, READ_UNCODED_SPROC)
    match = re.search(
        r"FROM\s+dbo\.\[Expense\]\s+e(.*?)LEFT\s+JOIN\s+dbo\.\[ExpenseCodingItem\]",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"{READ_UNCODED_SPROC}: could not isolate inner-join block"
    return "FROM dbo.[Expense] e" + match.group(1)


def _read_state_identity_sql() -> str:
    body = sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC)
    match = re.search(
        r"FROM\s+dbo\.\[ExpenseCodingItem\]\s+eci(.*?)INNER\s+JOIN\s+STRING_SPLIT",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, (
        f"{READ_STATE_SPROC}: could not isolate identity joins before STRING_SPLIT"
    )
    return "FROM dbo.[ExpenseCodingItem] eci" + match.group(1)


def _leg_check_result(
    site_label: str,
    legs: frozenset[Leg],
    *,
    expected: frozenset[Leg],
) -> tuple[bool, str]:
    missing = expected - legs
    extra = legs - expected
    if not missing and not extra:
        return True, ""
    parts = [f"site {site_label} diverged"]
    if missing:
        parts.append(f"missing legs: {sorted(missing)!r}")
    if extra:
        parts.append(f"extra legs: {sorted(extra)!r}")
    return False, "; ".join(parts)


def assert_identity_legs(
    site_label: str,
    sql: str,
    *,
    expected: frozenset[Leg],
) -> None:
    legs = extract_inner_identity_legs(sql)
    ok, msg = _leg_check_result(site_label, legs, expected=expected)
    assert ok, msg


# ---------------------------------------------------------------------------
# Spec 1 — shared six (exact set per shape) on all four SQL sites
# ---------------------------------------------------------------------------


def test_shape_a_site_payment_type_carries_shared_six_only():
    assert_identity_legs(
        "_read_qbo_purchase_payment_type",
        _payment_type_sql(),
        expected=SHAPE_A_LEGS,
    )


def test_shape_a_site_read_uncoded_carries_shared_six_only():
    assert_identity_legs(
        READ_UNCODED_SPROC,
        _read_uncoded_identity_sql(),
        expected=SHAPE_A_LEGS,
    )


def test_shape_b_site_enqueue_recode_carries_shared_six_plus_entry_leg():
    assert_identity_legs(
        "enqueue_coding_recode_on_submit",
        _enqueue_recode_sql(),
        expected=SHAPE_B_LEGS,
    )


def test_shape_b_site_read_state_carries_shared_six_plus_entry_leg():
    assert_identity_legs(
        READ_STATE_SPROC,
        _read_state_identity_sql(),
        expected=SHAPE_B_LEGS,
    )


def test_all_four_sql_sites_include_shared_six():
    sites = [
        ("_read_qbo_purchase_payment_type", _payment_type_sql()),
        (READ_UNCODED_SPROC, _read_uncoded_identity_sql()),
        ("enqueue_coding_recode_on_submit", _enqueue_recode_sql()),
        (READ_STATE_SPROC, _read_state_identity_sql()),
    ]
    for label, sql in sites:
        legs = extract_inner_identity_legs(sql)
        missing = SHARED_SIX - legs
        assert not missing, f"site {label} missing shared leg(s): {sorted(missing)!r}"


# ---------------------------------------------------------------------------
# Spec 1 / 3 / 2 — mutation proofs on extracted SQL (no repo edits)
# ---------------------------------------------------------------------------


def _mutated_site3_drop_realm_leg() -> str:
    original = _read_uncoded_identity_sql()
    mutated = re.sub(
        r"\s+AND\s+eli\.\[RealmId\]\s*=\s*p\.\[RealmId\]",
        "",
        original,
        count=1,
        flags=re.IGNORECASE,
    )
    assert mutated != original, "mutation did not apply"
    assert _md5(mutated) != _md5(original)
    return mutated


def test_mutation_spec1_drop_realm_leg_site3_goes_red():
    sql = _mutated_site3_drop_realm_leg()
    legs = extract_inner_identity_legs(sql)
    ok, msg = _leg_check_result(READ_UNCODED_SPROC, legs, expected=SHAPE_A_LEGS)
    assert not ok, "expected RED when realm leg dropped from site 3"
    assert READ_UNCODED_SPROC in msg
    assert (("eli", "RealmId"), ("p", "RealmId")) in (SHAPE_A_LEGS - legs)


def _mutated_site1_reverse_purchase_id_leg() -> str:
    original = _payment_type_sql()
    mutated = original.replace(
        "ON pl.[QboPurchaseId] = p.[Id]",
        "ON p.[Id] = pl.[QboPurchaseId]",
        1,
    )
    assert mutated != original, "mutation did not apply"
    assert _md5(mutated) != _md5(original)
    return mutated


def test_mutation_spec2_reversed_leg_operand_order_stays_green():
    sql = _mutated_site1_reverse_purchase_id_leg()
    assert_identity_legs(
        "_read_qbo_purchase_payment_type (reversed pl↔p leg)",
        sql,
        expected=SHAPE_A_LEGS,
    )


def _mutated_site3_spurious_seventh_leg() -> str:
    original = _read_uncoded_identity_sql()
    mutated = original.replace(
        "AND pl.[QboLineId]     = eli.[QboId]",
        "AND pl.[QboLineId]     = eli.[QboId]\n           AND pl.[Id] = eli.[Id]",
        1,
    )
    assert mutated != original, "mutation did not apply"
    assert _md5(mutated) != _md5(original)
    return mutated


def test_mutation_spec3_spurious_seventh_leg_site3_goes_red():
    sql = _mutated_site3_spurious_seventh_leg()
    legs = extract_inner_identity_legs(sql)
    ok, msg = _leg_check_result(READ_UNCODED_SPROC, legs, expected=SHAPE_A_LEGS)
    assert not ok, "expected RED when spurious leg added to site 3"
    assert "extra legs" in msg
    assert READ_UNCODED_SPROC in msg


# ---------------------------------------------------------------------------
# Spec 4 — shape C worker parent scope (expense.id, not staging PK)
# ---------------------------------------------------------------------------


def _worker_resolve_source() -> str:
    return inspect.getsource(QboOutboxWorker._resolve_recode_line_economics)


def test_shape_c_worker_two_hop_read_by_qbo_identity():
    src = _worker_resolve_source()
    assert "ExpenseService().read_by_qbo_identity" in src
    assert "ExpenseLineItemService().read_by_qbo_identity" in src
    assert re.search(
        r"read_by_qbo_identity\s*\(\s*item\.qbo_purchase_qbo_id\s*,\s*row\.realm_id\s*\)",
        src,
    ), "expense hop must use purchase QboId + realm"
    assert re.search(
        r"read_by_qbo_identity\s*\(\s*coerce_id\s*\(\s*expense\.id\s*\)\s*,\s*item\.qbo_line_id\s*\)",
        src,
    ), "line hop must parent-scope with coerce_id(expense.id)"


def _assert_worker_line_hop_parent_scoped(src: str) -> None:
    assert re.search(
        r"read_by_qbo_identity\s*\(\s*coerce_id\s*\(\s*expense\.id\s*\)\s*,\s*item\.qbo_line_id\s*\)",
        src,
    ), "line hop must parent-scope with coerce_id(expense.id)"


def test_mutation_spec4_worker_staging_pk_parent_scope_goes_red():
    original = _worker_resolve_source()
    mutated = original.replace(
        "coerce_id(expense.id)",
        "item.qbo_purchase_line_id",
        1,
    )
    assert mutated != original, "mutation did not apply"
    assert _md5(mutated) != _md5(original)
    with pytest.raises(AssertionError, match="line hop must parent-scope"):
        _assert_worker_line_hop_parent_scoped(mutated)
