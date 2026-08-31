"""
Tests for `create_mapping_then_stamp` (U-341, U-339 follow-up) — the shared helper
that makes "stamp dbo line identity ONLY when the mapping create succeeded" a
structural invariant instead of a per-connector hand-rolled try/except/else.

Two layers:
  1. Helper-level: both per-connector failure-policy SHAPES (warn-and-skip,
     compensating-delete-and-raise), the success path, and `catch` scoping.
  2. AST guard: no connector function may directly (in its own body, not a nested
     closure) call both a mapping-create and `stamp_line_identity_or_warn` — that
     flat shape is exactly the U-339 bug; the fix must route through the helper.
"""
import ast
import textwrap
from pathlib import Path
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.base.line_identity_stamp import create_mapping_then_stamp
from shared.database import DatabaseConstraintError, map_database_error


def _unique_violation() -> DatabaseConstraintError:
    raw = (
        "('23000', \"[23000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
        "Violation of UNIQUE KEY constraint 'UQ_Whatever'. Cannot insert duplicate "
        "key in object 'qbo.Whatever'. (2627)\")"
    )
    error = map_database_error(Exception(raw))
    assert isinstance(error, DatabaseConstraintError), f"fixture drifted: got {type(error)}"
    return error


# --------------------------------------------------------------------------- #
# Success path
# --------------------------------------------------------------------------- #


def test_stamp_called_on_success():
    create_mapping = Mock(return_value="the-mapping")
    stamp_identity = Mock()
    on_mapping_failure = Mock()

    result = create_mapping_then_stamp(
        create_mapping=create_mapping,
        stamp_identity=stamp_identity,
        on_mapping_failure=on_mapping_failure,
        catch=(Exception,),
    )

    assert result is None  # no connector call site consumes a return value
    create_mapping.assert_called_once_with()
    stamp_identity.assert_called_once_with()
    on_mapping_failure.assert_not_called()


# --------------------------------------------------------------------------- #
# Failure — policy shape 1: warn-and-skip (on_mapping_failure returns normally)
# --------------------------------------------------------------------------- #


def test_stamp_not_called_when_create_mapping_raises_and_policy_warns_and_skips():
    """bill / bill_credit shape: on_mapping_failure logs and returns — the helper
    must not call stamp_identity, and must not itself raise."""
    create_mapping = Mock(side_effect=ValueError("already mapped"))
    stamp_identity = Mock()
    on_mapping_failure = Mock()  # returns None — the warn-and-skip policy

    result = create_mapping_then_stamp(
        create_mapping=create_mapping,
        stamp_identity=stamp_identity,
        on_mapping_failure=on_mapping_failure,
        catch=(ValueError,),
    )

    assert result is None
    stamp_identity.assert_not_called()
    on_mapping_failure.assert_called_once()
    assert isinstance(on_mapping_failure.call_args.args[0], ValueError)


# --------------------------------------------------------------------------- #
# Failure — policy shape 2: compensating-delete-and-raise
# --------------------------------------------------------------------------- #


def test_stamp_not_called_when_create_mapping_raises_and_policy_reraises():
    """invoice / purchase shape: on_mapping_failure does cleanup then re-raises
    (bare `raise`) — the helper must propagate that exception and must never
    reach stamp_identity."""
    original = ValueError("mapping insert lost the race")
    create_mapping = Mock(side_effect=original)
    stamp_identity = Mock()

    def on_mapping_failure(exc):
        raise  # bare re-raise, exactly the invoice/purchase connector shape

    with pytest.raises(ValueError) as excinfo:
        create_mapping_then_stamp(
            create_mapping=create_mapping,
            stamp_identity=stamp_identity,
            on_mapping_failure=on_mapping_failure,
            catch=(ValueError,),
        )

    assert excinfo.value is original  # bare `raise` inside the callback re-raises the SAME exception
    stamp_identity.assert_not_called()


def test_stamp_not_called_when_policy_raises_a_new_chained_exception():
    """purchase's actual shape: `raise ValueError(...) from exc` — a NEW exception,
    not a bare re-raise. Must still propagate and must never reach stamp_identity."""
    original = RuntimeError("insert failed")
    create_mapping = Mock(side_effect=original)
    stamp_identity = Mock()

    def on_mapping_failure(exc):
        raise ValueError(f"wrapped: {exc}") from exc

    with pytest.raises(ValueError, match="wrapped: insert failed") as excinfo:
        create_mapping_then_stamp(
            create_mapping=create_mapping,
            stamp_identity=stamp_identity,
            on_mapping_failure=on_mapping_failure,
            catch=(RuntimeError,),
        )

    assert excinfo.value.__cause__ is original
    stamp_identity.assert_not_called()


# --------------------------------------------------------------------------- #
# `catch` scoping — an exception NOT in `catch` must propagate untouched
# --------------------------------------------------------------------------- #


def test_exception_outside_catch_propagates_without_invoking_policy():
    """bill's real shape: catch=(ValueError,) only — a DatabaseConstraintError
    race must propagate directly (not through on_mapping_failure at all), so the
    caller's own per-line handler (rollback / watermark-hold) sees it."""
    create_mapping = Mock(side_effect=_unique_violation())
    stamp_identity = Mock()
    on_mapping_failure = Mock()

    with pytest.raises(DatabaseConstraintError):
        create_mapping_then_stamp(
            create_mapping=create_mapping,
            stamp_identity=stamp_identity,
            on_mapping_failure=on_mapping_failure,
            catch=(ValueError,),
        )

    on_mapping_failure.assert_not_called()
    stamp_identity.assert_not_called()


def test_broad_catch_matches_any_exception():
    """bill_credit / purchase shape: catch=(Exception,) — anything is routed to policy."""
    create_mapping = Mock(side_effect=KeyError("boom"))
    stamp_identity = Mock()
    on_mapping_failure = Mock()

    result = create_mapping_then_stamp(
        create_mapping=create_mapping,
        stamp_identity=stamp_identity,
        on_mapping_failure=on_mapping_failure,
        catch=(Exception,),
    )

    assert result is None
    stamp_identity.assert_not_called()
    on_mapping_failure.assert_called_once()


# --------------------------------------------------------------------------- #
# Ordering — create_mapping must run to completion before stamp_identity is
# even considered
# --------------------------------------------------------------------------- #


def test_create_mapping_runs_before_stamp_identity():
    order = []
    create_mapping = Mock(side_effect=lambda: order.append("create"))
    stamp_identity = Mock(side_effect=lambda: order.append("stamp"))

    create_mapping_then_stamp(
        create_mapping=create_mapping,
        stamp_identity=stamp_identity,
        on_mapping_failure=Mock(),
        catch=(Exception,),
    )

    assert order == ["create", "stamp"]


# =========================================================================== #
# AST guard — no connector may hand-roll the create-then-stamp shape
# =========================================================================== #

REPO_ROOT = Path(__file__).resolve().parents[1]

# Glob-discovered, not hand-listed: every QBO connector lives at this same
# `.../connector/<name>/business/service.py` shape (15 today, only 4 of which
# touch stamp_line_identity_or_warn at all — the other 11 are header
# connectors and simply never trip the guard below). A 5th LINE connector
# added later is picked up automatically; nobody has to remember to append
# its path here — that "remember to update a list" gap is exactly the kind of
# convention-not-construction hole this unit exists to close.
CONNECTOR_FILES = sorted(REPO_ROOT.glob("integrations/intuit/qbo/*/connector/*/business/service.py"))

STAMP_CALL_NAME = "stamp_line_identity_or_warn"


def _is_call_to(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == name
    if isinstance(func, ast.Attribute):
        return func.attr == name
    return False


def _is_mapping_create_call(node: ast.AST) -> bool:
    """A call that creates the QBO<->dbo mapping row: `self.create_mapping(...)`
    (bill/invoice/purchase's own wrapper) or any method called directly on
    `self.mapping_repo` (`.create(...)`, bill_credit's shape) — scoped to the
    mapping repo specifically, not just any object's `.create(...)`, so this
    can't false-positive on an unrelated `<entity>_service.create(...)` call
    (e.g. creating the line item itself) sitting in the same function."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "create_mapping":
        return True
    return isinstance(func.value, ast.Attribute) and func.value.attr == "mapping_repo"


class _OwnScopeCallCollector(ast.NodeVisitor):
    """Collects Call nodes reachable from a function's OWN body — does NOT
    descend into nested function/lambda defs (each gets checked separately as
    its own scope). This is what makes a closure passed to
    create_mapping_then_stamp structurally invisible to its enclosing
    function's own-scope check."""

    def __init__(self):
        self.calls: list[ast.Call] = []

    def visit_FunctionDef(self, node):  # boundary — do not descend
        pass

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):  # boundary — do not descend
        pass

    def visit_Call(self, node):
        self.calls.append(node)
        self.generic_visit(node)


def _own_scope_calls(func_node) -> list[ast.Call]:
    collector = _OwnScopeCallCollector()
    for stmt in func_node.body:
        collector.visit(stmt)
    return collector.calls


def _creates_mapping_and_stamps_in_own_scope(func_node) -> bool:
    calls = _own_scope_calls(func_node)
    creates_mapping = any(_is_mapping_create_call(c) for c in calls)
    stamps_identity = any(_is_call_to(c, STAMP_CALL_NAME) for c in calls)
    return creates_mapping and stamps_identity


@pytest.mark.parametrize("path", CONNECTOR_FILES, ids=lambda p: p.parts[-3])
def test_no_hand_rolled_mapping_create_then_stamp(path):
    """No function in any QBO connector may directly call both a mapping-create
    and stamp_line_identity_or_warn in its own body (excluding nested closures)
    — that flat shape is exactly the U-339 bug. Post-U-341, both calls only ever
    co-occur inside create_mapping_then_stamp itself, never in a connector."""
    tree = ast.parse(path.read_text())
    offenders = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _creates_mapping_and_stamps_in_own_scope(node)
    ]
    assert offenders == [], (
        f"{path.name}: function(s) {offenders} directly call both a mapping-create "
        f"and stamp_line_identity_or_warn — route this through create_mapping_then_stamp "
        f"instead of a hand-rolled try/except."
    )


def test_connector_files_glob_finds_all_four_line_connectors():
    """Sanity check on the glob itself: if the directory shape ever changes,
    this fails loudly instead of the parametrized guard above silently running
    over zero/fewer files."""
    names = {p.parts[-3] for p in CONNECTOR_FILES}
    assert {
        "bill_line_item", "bill_credit_line_item", "invoice_line_item", "expense_line_item",
    } <= names


def test_guard_detects_the_actual_u339_bug_shape():
    """Proves the detector fires on the literal pre-fix bill_line_item pattern
    (the U-339 bug: try/except ValueError -> warn / else -> stamp, both in one
    flat function, mapping created via self.create_mapping) — this is the
    guard's own mutation-proof, since the real files no longer contain this
    shape to revert-and-check against."""
    bad_source = textwrap.dedent(
        """
        def sync_from_qbo_x_line(self):
            try:
                mapping = self.create_mapping(x=1)
            except ValueError as e:
                logger.warning(f"nope: {e}")
            else:
                stamp_line_identity_or_warn(repo, id=1, qbo_id="x", realm_id="r", context="c")
            return None
        """
    )
    func = ast.parse(bad_source).body[0]
    assert _creates_mapping_and_stamps_in_own_scope(func)


def test_guard_detects_the_mapping_repo_create_bug_shape():
    """Same bug, bill_credit's pre-fix shape: mapping created via a direct
    `self.mapping_repo.create(...)` call rather than a `self.create_mapping`
    wrapper — the detector must catch both spellings."""
    bad_source = textwrap.dedent(
        """
        def sync_from_qbo_x_line(self):
            try:
                self.mapping_repo.create(x=1)
            except Exception as e:
                logger.warning(f"nope: {e}")
            else:
                stamp_line_identity_or_warn(repo, id=1, qbo_id="x", realm_id="r", context="c")
        """
    )
    func = ast.parse(bad_source).body[0]
    assert _creates_mapping_and_stamps_in_own_scope(func)


def test_guard_does_not_flag_an_update_only_closure():
    """An UPDATE-path closure (no mapping-create involved, just re-stamping an
    already-mapped row on every touch) must NOT be flagged — it never had the
    U-339 hazard in the first place."""
    update_source = textwrap.dedent(
        """
        def _apply_line_fields(self, direct, *, path_label):
            updated = self.service.update_by_public_id(direct.public_id)
            stamp_line_identity_or_warn(repo, id=updated.id, qbo_id="x", realm_id="r", context="c")
            return updated
        """
    )
    func = ast.parse(update_source).body[0]
    assert not _creates_mapping_and_stamps_in_own_scope(func)


def test_guard_does_not_flag_an_unrelated_create_call_alongside_a_stamp():
    """An unrelated `<entity>_service.create(...)` call (creating the line item
    itself, not the mapping) must NOT trip the guard even if it coexists with a
    stamp call in the same scope — only self.create_mapping / self.mapping_repo.*
    count as a mapping-create."""
    source = textwrap.dedent(
        """
        def sync_from_qbo_x_line(self):
            line_item = self.expense_line_item_service.create(x=1)
            stamp_line_identity_or_warn(repo, id=line_item.id, qbo_id="x", realm_id="r", context="c")
            return line_item
        """
    )
    func = ast.parse(source).body[0]
    assert not _creates_mapping_and_stamps_in_own_scope(func)


def test_guard_does_not_flag_the_helper_wired_shape():
    """The POST-U-341 shape: create/stamp/on_failure each live in their own
    nested closure, and the outer function only calls create_mapping_then_stamp.
    Must NOT be flagged."""
    good_source = textwrap.dedent(
        """
        def sync_from_qbo_x_line(self):
            line_item_id = 1

            def _create_mapping():
                self.create_mapping(x=line_item_id)

            def _on_mapping_failure(exc):
                logger.warning(f"nope: {exc}")

            def _stamp_identity():
                stamp_line_identity_or_warn(repo, id=line_item_id, qbo_id="x", realm_id="r", context="c")

            create_mapping_then_stamp(
                create_mapping=_create_mapping,
                stamp_identity=_stamp_identity,
                on_mapping_failure=_on_mapping_failure,
                catch=(ValueError,),
            )
            return None
        """
    )
    func = ast.parse(good_source).body[0]
    assert not _creates_mapping_and_stamps_in_own_scope(func)
