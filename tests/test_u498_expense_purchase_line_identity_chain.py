"""U-498 — one shared test for Expense↔Purchase↔PurchaseLine↔ExpenseLineItem identity legs.

Pure source pins (no prod objects, no cross-schema view). Three shapes:
  A — 6 legs, expense-driven (payment-type SQL, ReadUncodedCompletedExpenseCandidates)
  B — 7 legs, coding-item-driven (+ cs purchase identity on p.QboId / p.RealmId)
  C — worker two-hop dbo read_by_qbo_identity (no staging SQL joins)

Site 4 legs are taken only from ReadCodingSuggestionStateByExpenseIds so unrelated
edits elsewhere in dbo.expense_coding_item.sql do not affect extraction.
If U-497b changes shape B's staging-PK entry leg (pl.Id = cs.QboPurchaseLineId),
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
EXPENSE_CODING_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
READ_UNCODED_SPROC = "ReadUncodedCompletedExpenseCandidates"
READ_STATE_SPROC = "ReadCodingSuggestionStateByExpenseIds"

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

SHAPE_B_ENTRY: Leg = (("cs", "QboPurchaseQboId"), ("e", "QboId"))
SHAPE_B_CS_EXPENSE_LEGS: frozenset[Leg] = frozenset(
    {
        (("cs", "ExpenseId"), ("e", "Id")),
        (("cs", "RealmId"), ("e", "RealmId")),
    }
)
SHAPE_A_LEGS = SHARED_SIX
SHAPE_B_LEGS = SHARED_SIX | {SHAPE_B_ENTRY} | SHAPE_B_CS_EXPENSE_LEGS

IDENTITY_ALIASES = frozenset({"e", "eli", "p", "pl", "cs"})


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


_CS_EXPENSE_OR = re.compile(
    r"\(\s*"
    r"cs\.\[ExpenseId\]\s+IS\s+NOT\s+NULL\s+AND\s+e\.\[Id\]\s*=\s*cs\.\[ExpenseId\]\s*"
    r"\)\s+OR\s+\(\s*"
    r"cs\.\[ExpenseId\]\s+IS\s+NULL\s+AND\s+cs\.\[QboPurchaseQboId\]\s+IS\s+NOT\s+NULL\s+AND\s+"
    r"cs\.\[RealmId\]\s+IS\s+NOT\s+NULL\s+AND\s+e\.\[QboId\]\s*=\s*cs\.\[QboPurchaseQboId\]\s+AND\s+"
    r"e\.\[RealmId\]\s*=\s*cs\.\[RealmId\]\s*"
    r"\)",
    re.IGNORECASE | re.DOTALL,
)


def _on_clause_or_is_permitted(on_clause: str, join_alias: str) -> bool:
    """An ON clause with any OR is accepted ONLY when the whole clause is exactly the permitted
    cs->e fallback (ExpenseId, else purchase identity). A substring match would let an extra
    `OR <anything>` branch through and make the join non-restrictive (U-652 round 3)."""
    if not re.search(r"\bor\b", on_clause, re.IGNORECASE):
        return True
    return join_alias.lower() == "e" and _CS_EXPENSE_OR.fullmatch(on_clause.strip()) is not None


def _flatten_nested(block: str) -> str:
    """Blank out everything inside nested parentheses, keeping the top level of `block`."""
    out = []
    depth = 0
    for ch in block:
        if ch == "(":
            depth += 1
            out.append(" ")
        elif ch == ")":
            depth -= 1
            out.append(" ")
        else:
            out.append(ch if depth == 0 else " ")
    return "".join(out)


def _is_keyword_at(text: str, i: int, word: str) -> bool:
    n = len(word)
    if text[i : i + n].upper() != word:
        return False
    before_ok = i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_[.")
    after_ok = i + n >= len(text) or not (text[i + n].isalnum() or text[i + n] in "_]")
    return before_ok and after_ok


def _correlation_is_restrictive(text: str, pos: int, end: int) -> bool:
    """True when the term text[pos:end] restricts its WHERE/ON predicate: walking outward through
    every enclosing parenthesis level up to the WHERE/ON keyword, no level may hold an OR, and no
    level may apply NOT to the term or to the group containing it (U-652 round 3)."""
    lo, hi = pos, end
    while True:
        depth = 0
        start = None
        keyword_start = False
        i = lo - 1
        while i >= 0:
            ch = text[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                if depth == 0:
                    start = i + 1
                    break
                depth -= 1
            elif depth == 0 and (_is_keyword_at(text, i, "WHERE") or _is_keyword_at(text, i, "ON")):
                start = i + (5 if _is_keyword_at(text, i, "WHERE") else 2)
                keyword_start = True
                break
            i -= 1
        if start is None:
            start = 0
            keyword_start = True
        depth = 0
        stop = len(text)
        for j in range(hi, len(text)):
            ch = text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    stop = j
                    break
                depth -= 1
            elif depth == 0 and keyword_start and any(
                _is_keyword_at(text, j, kw) for kw in ("GROUP", "ORDER", "INNER", "LEFT", "RIGHT", "CROSS", "UNION")
            ):
                stop = j
                break
        flat = _flatten_nested(text[start:stop])
        if re.search(r"\bOR\b", flat, re.IGNORECASE):
            return False
        prefix = _flatten_nested(text[start:lo]).rstrip()
        if re.search(r"\bNOT$", prefix, re.IGNORECASE):
            return False
        if keyword_start:
            return True
        # step out: the member at the next level is the parenthesised group itself
        lo, hi = start - 1, stop + 1


def extract_inner_identity_legs(sql: str) -> frozenset[Leg]:
    """Identity legs from INNER JOIN ON clauses among e/eli/p/pl/cs only."""
    normalized = strip_sql_comments(sql)
    legs: set[Leg] = set()
    for join_match in re.finditer(
        r"(?:INNER|LEFT)\s+JOIN\s+(?:dbo\.|qbo\.)?\[\w+\]\s+(\w+)\s+ON\s+",
        normalized,
        re.IGNORECASE,
    ):
        join_alias = join_match.group(1)
        start = join_match.end()
        rest = normalized[start:]
        end_match = re.search(
            r"\s+(?:INNER|LEFT|RIGHT|FULL|CROSS)\s+JOIN\b|\s+WHERE\b",
            rest,
            re.IGNORECASE,
        )
        on_clause = rest[: end_match.start()] if end_match else rest
        if not _on_clause_or_is_permitted(on_clause, join_alias):
            return frozenset()
        if join_alias.lower() == "e" and _CS_EXPENSE_OR.search(on_clause):
            legs.add((("cs", "ExpenseId"), ("e", "Id")))
            legs.add((("cs", "QboPurchaseQboId"), ("e", "QboId")))
            legs.add((("cs", "RealmId"), ("e", "RealmId")))
        for fragment in _split_top_level_and(on_clause):
            leg = _parse_simple_equality_leg(fragment)
            if leg is None:
                continue
            (t1, _), (t2, _) = leg
            if t1 in IDENTITY_ALIASES and t2 in IDENTITY_ALIASES:
                legs.add(leg)
    for m in re.finditer(
        r"eli\.\[ExpenseId\]\s*=\s*e\.\[Id\]",
        normalized,
        re.IGNORECASE,
    ):
        # Only a restrictive correlation counts: an OR beside it in the same predicate
        # (e.g. `eli.[ExpenseId] = e.[Id] OR 1=1`) constrains nothing.
        if _correlation_is_restrictive(normalized, m.start(), m.end()):
            legs.add((("e", "Id"), ("eli", "ExpenseId")))
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
        EXPENSE_SERVICE_PY.read_text(),
        "_resolve_dominant_expense_coding_for_submit",
    )


def _read_uncoded_identity_sql() -> str:
    body = sproc_body(EXPENSE_SQL, READ_UNCODED_SPROC)
    match = re.search(
        r"FROM\s+dbo\.\[Expense\]\s+e(.*?)LEFT\s+JOIN\s+dbo\.\[CodingSuggestion\]",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"{READ_UNCODED_SPROC}: could not isolate inner-join block"
    return "FROM dbo.[Expense] e" + match.group(1)


def _read_state_identity_sql() -> str:
    body = sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC)
    head = re.search(
        r"FROM\s+dbo\.\[CodingSuggestion\]\s+cs(.*?)INNER\s+JOIN\s+STRING_SPLIT",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    exists_block = re.search(
        r"WHERE\s+EXISTS\s*\((.*?)\)\s+AND\s+\(\s*@ActorIsSystemAdmin",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert head is not None, (
        f"{READ_STATE_SPROC}: could not isolate identity joins before STRING_SPLIT"
    )
    assert exists_block is not None, (
        f"{READ_STATE_SPROC}: could not isolate EXISTS line-eligibility subquery"
    )
    return (
        "FROM dbo.[CodingSuggestion] cs"
        + head.group(1)
        + " INNER JOIN EXISTS_SUBQUERY ON 1=1 "
        + exists_block.group(1)
    )


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
    """Submit path resolves lines via dominant SQL; cs purchase identity lives on read-state."""
    assert_identity_legs(
        "_resolve_dominant_expense_coding_for_submit",
        _enqueue_recode_sql(),
        expected=SHAPE_A_LEGS,
    )
    assert_identity_legs(
        READ_STATE_SPROC,
        _read_state_identity_sql(),
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
        ("_resolve_dominant_expense_coding_for_submit", _enqueue_recode_sql()),
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
        r"read_by_qbo_identity\s*\(\s*coerce_id\s*\(\s*expense\.id\s*\)\s*,\s*target_qbo_line_id\s*\)",
        src,
    ), "line hop must parent-scope with coerce_id(expense.id)"


def _assert_worker_line_hop_parent_scoped(src: str) -> None:
    assert re.search(
        r"read_by_qbo_identity\s*\(\s*coerce_id\s*\(\s*expense\.id\s*\)\s*,\s*target_qbo_line_id\s*\)",
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


# --- U-652 fix round 3: the identity checker must reject non-restrictive predicates ----------

_CS_E_JOIN_OK = (
    "FROM dbo.[CodingSuggestion] cs\n"
    "INNER JOIN dbo.[Expense] e\n"
    "    ON (cs.[ExpenseId] IS NOT NULL AND e.[Id] = cs.[ExpenseId])\n"
    "    OR (cs.[ExpenseId] IS NULL AND cs.[QboPurchaseQboId] IS NOT NULL AND cs.[RealmId] IS NOT NULL\n"
    "        AND e.[QboId] = cs.[QboPurchaseQboId] AND e.[RealmId] = cs.[RealmId])\n"
    "WHERE e.[Id] > 0\n"
)


def test_permitted_cs_expense_or_join_yields_its_legs():
    legs = extract_inner_identity_legs(_CS_E_JOIN_OK)
    assert (("cs", "ExpenseId"), ("e", "Id")) in legs
    assert (("cs", "QboPurchaseQboId"), ("e", "QboId")) in legs


def test_permitted_or_join_with_an_extra_or_branch_is_rejected():
    mutated = _CS_E_JOIN_OK.replace("WHERE e.[Id] > 0", "    OR 2=2\nWHERE e.[Id] > 0")
    assert extract_inner_identity_legs(mutated) == frozenset()


def test_eli_expense_correlation_is_a_leg_only_when_restrictive():
    ok = "WHERE EXISTS (SELECT 1 FROM dbo.[ExpenseLineItem] eli WHERE eli.[ExpenseId] = e.[Id]\n)"
    assert (("e", "Id"), ("eli", "ExpenseId")) in extract_inner_identity_legs(ok)
    anded = "WHERE eli.[ExpenseId] = e.[Id] AND eli.[RealmId] = e.[RealmId]"
    assert (("e", "Id"), ("eli", "ExpenseId")) in extract_inner_identity_legs(anded)
    ored = "WHERE eli.[ExpenseId] = e.[Id] OR 1=1"
    assert (("e", "Id"), ("eli", "ExpenseId")) not in extract_inner_identity_legs(ored)


@pytest.mark.parametrize(
    "replacement",
    [
        "WHERE (eli.[ExpenseId] = e.[Id]) OR 1=1",
        "WHERE NOT (eli.[ExpenseId] = e.[Id])",
        "WHERE ((eli.[ExpenseId] = e.[Id])) OR 1=1",
        "WHERE NOT eli.[ExpenseId] = e.[Id]",
    ],
)
def test_real_read_state_correlation_rejects_enclosing_or_and_not(replacement):
    """U-652 round 3b: mutate the REAL read-state procedure; an enclosing OR or a NOT around the
    eli->e correlation must drop the leg, so the site no longer carries SHAPE_B_LEGS."""
    sql = _read_state_identity_sql()
    original = re.search(r"WHERE\s+eli\.\[ExpenseId\]\s*=\s*e\.\[Id\]", sql, re.IGNORECASE)
    assert original, "real procedure must correlate eli.[ExpenseId] = e.[Id]"
    mutated = sql[: original.start()] + replacement + sql[original.end():]
    assert extract_inner_identity_legs(sql) >= SHAPE_B_LEGS
    assert (("e", "Id"), ("eli", "ExpenseId")) not in extract_inner_identity_legs(mutated)


# --- U-652 round 4: pin the read-state line-eligibility predicate to its exact text ----------
# A regex SQL parser always has another hole (NOT EXISTS wrappers, `EXISTS (...) OR 1=1`, CASE,
# `= e.[Id] + 1`). This site's whole WHERE is pinned instead: any change to it fails here and must
# be reviewed on purpose.

_CANONICAL_READ_STATE_WHERE = (
    "WHERE EXISTS ( SELECT 1 FROM dbo.[ExpenseLineItem] eli "
    "INNER JOIN qbo.[Purchase] p ON p.[QboId] = e.[QboId] AND p.[RealmId] = e.[RealmId] "
    "INNER JOIN qbo.[PurchaseLine] pl ON pl.[QboPurchaseId] = p.[Id] "
    "AND pl.[QboLineId] = eli.[QboId] AND eli.[RealmId] = p.[RealmId] "
    "WHERE eli.[ExpenseId] = e.[Id] ) "
    "AND ( @ActorIsSystemAdmin = 1 "
    "OR COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId]) IS NULL "
    "OR dbo.UserCanAccessProject( @ActorUserId, @ActorIsSystemAdmin, "
    "COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId])) = 1 );"
)


def _read_state_where_clause(body: str) -> str:
    """The outer WHERE of the read-state procedure (after the STRING_SPLIT join), whitespace-normalised."""
    text = strip_sql_comments(body)
    split_at = re.search(r"STRING_SPLIT\(", text, re.IGNORECASE)
    assert split_at, "read-state must filter by STRING_SPLIT(@ExpenseIds)"
    where_at = re.search(r"\bWHERE\b", text[split_at.end():], re.IGNORECASE)
    assert where_at, "read-state must have an outer WHERE after the STRING_SPLIT join"
    tail = text[split_at.end() + where_at.start():]
    end = re.search(r"\bEND\s*;?\s*$", tail, re.IGNORECASE)
    clause = tail[: end.start()] if end else tail
    return re.sub(r"\s+", " ", clause).strip()


def test_read_state_line_eligibility_where_is_pinned_exactly():
    body = sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC)
    assert _read_state_where_clause(body) == _CANONICAL_READ_STATE_WHERE


@pytest.mark.parametrize(
    "replacement",
    [
        "WHERE (eli.[ExpenseId] = e.[Id]) OR 1=1",
        "WHERE NOT (eli.[ExpenseId] = e.[Id])",
        "WHERE eli.[ExpenseId] = e.[Id] + 1",
        "WHERE CASE WHEN eli.[ExpenseId] = e.[Id] THEN 0 ELSE 1 END = 1",
    ],
)
def test_read_state_pin_rejects_correlation_mutations(replacement):
    body = sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC)
    mutated = re.sub(r"WHERE\s+eli\.\[ExpenseId\]\s*=\s*e\.\[Id\]", replacement, body, count=1)
    assert mutated != body
    assert _read_state_where_clause(mutated) != _CANONICAL_READ_STATE_WHERE


@pytest.mark.parametrize(
    "outer",
    [
        ("WHERE\n      EXISTS (", "WHERE\n      NOT EXISTS ("),
        ("      )\n      AND (\n          @ActorIsSystemAdmin", "      ) OR 1=1\n      AND (\n          @ActorIsSystemAdmin"),
    ],
)
def test_read_state_pin_rejects_exists_wrapper_mutations(outer):
    body = sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC)
    old, new = outer
    assert body.count(old) == 1, old
    assert _read_state_where_clause(body.replace(old, new)) != _CANONICAL_READ_STATE_WHERE


# --- U-652 round 5: pin every identity site's WHOLE query -------------------------------------
# Round 4 pinned only read-state's outer WHERE; an added UNION branch before it, or an arithmetic
# change in an ON join (`= e.[Id] + 1`), still slipped past the leg parser. Each site's complete
# normalised query lives in tests/fixtures/u498_identity_sites/<site>.sql. A deliberate change to a
# site must update its fixture in the same diff, where the reviewer sees the exact SQL change.

_SITE_FIXTURES = Path(__file__).parent / "fixtures" / "u498_identity_sites"


_SQL_STRING_LITERAL = re.compile(r"(N?'(?:[^']|'')*')")


def _normalised(sql: str) -> str:
    """Collapse whitespace OUTSIDE string literals only: a literal's contents (e.g. a LIKE pattern)
    change what the query matches, so they must stay byte-exact in the pin."""
    pieces = _SQL_STRING_LITERAL.split(strip_sql_comments(sql))
    return "".join(
        piece if i % 2 else re.sub(r"\s+", " ", piece) for i, piece in enumerate(pieces)
    ).strip()


def _site_sql(site: str) -> str:
    return {
        "payment_type": _payment_type_sql,
        "enqueue_recode": _enqueue_recode_sql,
        "read_uncoded": lambda: sproc_body(EXPENSE_SQL, READ_UNCODED_SPROC),
        "read_state": lambda: sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC),
    }[site]()


_SITES = ["payment_type", "enqueue_recode", "read_uncoded", "read_state"]


@pytest.mark.parametrize("site", _SITES)
def test_identity_site_query_is_pinned_exactly(site):
    expected = (_SITE_FIXTURES / f"{site}.sql").read_text().strip()
    assert _normalised(_site_sql(site)) == expected, (
        f"{site}: query changed. If deliberate, re-review its identity joins and update "
        f"tests/fixtures/u498_identity_sites/{site}.sql in the same change."
    )


@pytest.mark.parametrize("site", [s for s in _SITES if s != "payment_type"])
def test_identity_site_pin_rejects_arithmetic_on_the_expense_correlation(site):
    sql = _site_sql(site)
    mutated, n = re.subn(r"(eli\.\[ExpenseId\]\s*=\s*e\.\[Id\])", r"\1 + 1", sql, count=1)
    assert n == 1, f"{site}: expected an eli->e correlation to mutate"
    assert _normalised(mutated) != (_SITE_FIXTURES / f"{site}.sql").read_text().strip()


def test_read_state_pin_rejects_a_preceding_union_branch():
    body = sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC)
    m = re.search(r"\bSELECT\b", body, re.IGNORECASE)
    injected = (
        body[: m.start()]
        + "SELECT legacy.[ExpenseId], legacy.[PublicId], legacy.[Status], NULL, NULL, NULL, NULL, NULL, NULL "
        "FROM dbo.[CodingSuggestion] legacy WHERE legacy.[ExpenseId] = TRY_CAST(@ExpenseIds AS BIGINT) UNION ALL "
        + body[m.start():]
    )
    assert _normalised(injected) != (_SITE_FIXTURES / "read_state.sql").read_text().strip()


@pytest.mark.parametrize("site", ["enqueue_recode", "read_uncoded"])
def test_identity_site_pin_sees_whitespace_inside_string_literals(site):
    """`'%NEED TO  CATEGORIZE%'` matches different rows than `'%NEED TO CATEGORIZE%'`; the pin's
    whitespace normalisation must leave literal contents untouched (U-652 round 6)."""
    sql = _site_sql(site)
    mutated, n = re.subn(r"NEED TO CATEGORIZE", "NEED TO  CATEGORIZE", sql, count=1)
    assert n == 1, f"{site}: expected the uncategorized-account literal"
    assert _normalised(mutated) != (_SITE_FIXTURES / f"{site}.sql").read_text().strip()
