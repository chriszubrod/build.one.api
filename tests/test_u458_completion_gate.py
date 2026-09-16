"""U-458 — the completion gate: the lifecycle stops describing and starts enforcing.

Since U-443 every read has reported `status` and `review_status_kind`, and
nothing has stopped a document being completed while a review sat open in
someone's queue. This unit is the enforcing half — per-entity env flags read at
request time, flipped by `/em` without a deploy.

THE CENTRAL CLAIM, and the one most worth attacking: **shipping this performs
no new database work.** All four flags default to `off`, and `off` must mean not
just "no refusal" but "no query and no decision" — no review lookup, no
permission resolution. (Precisely: a closure is built, the adapter is called and
`os.environ` is read. An earlier draft of this docstring claimed "byte-identical",
which Codex correctly called an overstatement.) `test_off_does_not_even_resolve_the_review` is the test that makes
that a property rather than a hope; the whole 3,936-test suite passing unchanged
around the wiring is the corroboration.

Two deliberate deviations from the design text, both approved at Gate 1:

1. **422, not the spec's 409.** Identical reasoning to U-446b's `status_locked`:
   installed iOS routes 409 into its per-service CONFLICT path (reload-and-retry,
   built for optimistic-concurrency collisions) and treats other 4xx as terminal.
   A permanent refusal answered with 409 makes a queued action spin forever.
   Bills are not on the iOS tab bar today — which makes 409 a LATENT trap, the
   kind this workstream keeps discovering after it has shipped.

2. **The fast-path reads the frozen `review_kind`, not `ReviewService.is_approved`.**
   The design named `is_approved` as gaining its first caller here, but that
   predates U-455: `is_approved` derives from the LIVE `status_is_final` /
   `status_is_declined` flags, which is precisely the read U-455 froze away from
   because it makes stored history a function of current config.
"""

import inspect
import logging
import os
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business.model import ParentType
from shared.api.errors import ErrorCode
from shared.lifecycle.completion_gate import (
    COMPLETION_GATES,
    ENV_VAR_BY_PARENT,
    GATE_BLOCK_OPEN_REVIEW,
    GATE_OFF,
    GATE_REQUIRE_APPROVED,
    CompletionRefused,
    completion_gate_for,
    enforce_completion_gate,
    evaluate_completion,
)

ALL_PARENTS = sorted(ENV_VAR_BY_PARENT)


@pytest.fixture(autouse=True)
def _clean_gate_env(monkeypatch):
    """Every test starts from "nothing configured" — otherwise a developer's own
    exported flag silently rewrites the expectations."""
    for var in ENV_VAR_BY_PARENT.values():
        monkeypatch.delenv(var, raising=False)


def _review(kind):
    return SimpleNamespace(review_kind=kind)


# ---------------------------------------------------------------------------
# 1 — it ships OFF, and `off` costs nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parent", ALL_PARENTS)
def test_every_gate_ships_off(parent):
    assert completion_gate_for(parent) == GATE_OFF


@pytest.mark.parametrize("parent", ALL_PARENTS)
@pytest.mark.parametrize("kind", ["none", "submitted", "in_review", "approved", "declined"])
def test_off_allows_every_review_state(parent, kind):
    """`off` is today's behaviour: completion never consults a review at all."""
    assert evaluate_completion(parent_type=parent, review_kind=kind) is False


def test_off_does_not_even_resolve_the_review():
    """THE zero-behaviour-change property.

    If `off` still resolved the review, every completion in the system would pay
    a database round trip for a feature nobody enabled — and "ships off" would
    be a claim about refusals only. The early return in `enforce_completion_gate`
    is what makes it a claim about cost too.
    """
    resolve = MagicMock(side_effect=AssertionError("resolved the review under an off gate"))
    enforce_completion_gate(ParentType.BILL, resolve_review=resolve)
    resolve.assert_not_called()


def test_an_unset_flag_is_off_not_an_error():
    """A brand-new App Service with none of these settings must behave exactly
    like the one running today."""
    assert os.environ.get(ENV_VAR_BY_PARENT[ParentType.BILL]) is None
    assert completion_gate_for(ParentType.BILL) == GATE_OFF


# ---------------------------------------------------------------------------
# 2 — the env reader
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parent", ALL_PARENTS)
@pytest.mark.parametrize("value", COMPLETION_GATES)
def test_each_valid_value_round_trips(parent, value, monkeypatch):
    monkeypatch.setenv(ENV_VAR_BY_PARENT[parent], value)
    assert completion_gate_for(parent) == value


@pytest.mark.parametrize("raw,expected", [
    ("  require_approved  ", GATE_REQUIRE_APPROVED),   # whitespace
    ("REQUIRE_APPROVED", GATE_REQUIRE_APPROVED),       # case
    ("Block_Open_Review", GATE_BLOCK_OPEN_REVIEW),
])
def test_the_reader_is_forgiving_about_case_and_whitespace(raw, expected, monkeypatch):
    """An App Service setting typed by a human, not a machine."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], raw)
    assert completion_gate_for(ParentType.BILL) == expected


def test_an_invalid_value_falls_back_to_off_and_WARNS(monkeypatch, caplog):
    """The asymmetry argument, pinned.

    A typo that wrongly ENABLES `require_approved` stops AP paying anything — a
    business stoppage that looks like a completion bug, not a bad env var. A typo
    that wrongly DISABLES the gate leaves the behaviour that shipped for years.
    So garbage means `off`. The WARNING is the compensating control, which makes
    it part of the contract rather than a nicety.
    """
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], "require-approved")  # hyphen, not underscore
    with caplog.at_level(logging.WARNING):
        assert completion_gate_for(ParentType.BILL) == GATE_OFF
    assert any(
        r.levelno == logging.WARNING and "LIFECYCLE_COMPLETION_GATE_BILL" in r.getMessage()
        for r in caplog.records
    ), "an unrecognised gate value must WARN — silence would hide a disabled money control"


def test_an_ungated_entity_is_off_without_warning(caplog):
    """ContractLabor/TimeEntry carry their gate in their own pipeline. Asking
    about them is legitimate, not a misconfiguration."""
    with caplog.at_level(logging.WARNING):
        assert completion_gate_for("contract_labor") == GATE_OFF
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_gate_keys_ARE_the_ParentType_members():
    """Drift guard. The gate speaks `entities/review`'s vocabulary; a module
    that quietly used PascalCase would look right and match nothing, reading
    every flag as unset and silently disabling the gate everywhere."""
    assert set(ENV_VAR_BY_PARENT) == {
        ParentType.BILL, ParentType.EXPENSE, ParentType.BILL_CREDIT, ParentType.INVOICE,
    }


# ---------------------------------------------------------------------------
# 3 — block_open_review
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parent", ALL_PARENTS)
@pytest.mark.parametrize("kind", ["submitted", "in_review"])
def test_block_open_review_refuses_an_open_review(parent, kind, monkeypatch):
    monkeypatch.setenv(ENV_VAR_BY_PARENT[parent], GATE_BLOCK_OPEN_REVIEW)
    with pytest.raises(CompletionRefused) as e:
        evaluate_completion(parent_type=parent, review_kind=kind)
    assert e.value.status_code == 422
    assert e.value.error_code == ErrorCode.REVIEW_OPEN


def test_block_open_review_refuses_a_declined_one_with_its_OWN_code(monkeypatch):
    """`declined` is not `open` — the operator's next action differs (resolve and
    resubmit vs wait for a decision), so the client must be able to tell them
    apart without parsing prose."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_BLOCK_OPEN_REVIEW)
    with pytest.raises(CompletionRefused) as e:
        evaluate_completion(parent_type=ParentType.BILL, review_kind="declined")
    assert e.value.error_code == ErrorCode.REVIEW_DECLINED


@pytest.mark.parametrize("kind", ["none", "approved", None])
def test_block_open_review_lets_never_submitted_and_approved_through(kind, monkeypatch):
    """`none` is the AP fast path the web labels "bypasses review"
    (`BillCreate.tsx:667`) — deliberately still allowed under this gate. A `None`
    review (no row at all) must read as `none`, not crash."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_BLOCK_OPEN_REVIEW)
    assert evaluate_completion(parent_type=ParentType.BILL, review_kind=kind) is False


# ---------------------------------------------------------------------------
# 4 — require_approved + the approver fast-path
# ---------------------------------------------------------------------------


def test_require_approved_lets_an_approved_document_through(monkeypatch):
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    assert evaluate_completion(parent_type=ParentType.BILL, review_kind="approved") is False


@pytest.mark.parametrize("kind", ["none", "submitted", "in_review"])
def test_require_approved_refuses_a_non_approver(kind, monkeypatch):
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    with pytest.raises(CompletionRefused) as e:
        evaluate_completion(parent_type=ParentType.BILL, review_kind=kind, actor_can_approve=False)
    assert e.value.status_code == 422
    assert e.value.error_code == ErrorCode.REVIEW_NOT_APPROVED


@pytest.mark.parametrize("kind", ["none", "submitted", "in_review"])
def test_require_approved_fast_paths_an_approver(kind, monkeypatch):
    """Returning True is the instruction "record the Approved row, then
    complete" — an actor who could approve in a separate click gains no control
    from being forced to make it."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    assert evaluate_completion(
        parent_type=ParentType.BILL, review_kind=kind, actor_can_approve=True
    ) is True


def test_a_declined_document_is_refused_even_for_an_approver(monkeypatch):
    """A decline is a decision a colleague made, not an open question — an
    approver must resubmit rather than silently overturn it.

    Refused in the PURE gate. The earlier shape returned "fast-path" for
    declined and left `build_fast_path_approval_payload` to raise
    `ReviewTransitionError` — which nothing on the completion path translates,
    so the caller got a **500** instead of the terminal 422 this gate promises
    (Codex P1, 2026-09-15). The builder still refuses too; that is defence in
    depth, not the contract.
    """
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    with pytest.raises(CompletionRefused) as e:
        evaluate_completion(
            parent_type=ParentType.BILL, review_kind="declined", actor_can_approve=True
        )
    assert e.value.status_code == 422
    assert e.value.error_code == ErrorCode.REVIEW_DECLINED


def test_the_builder_ALSO_refuses_a_declined_review():
    """Defence in depth: even called directly, it will not stamp an approval
    over a decline."""
    from entities.review.business.service import ReviewService, ReviewTransitionError

    svc = ReviewService()
    svc._resolve_parent = MagicMock(return_value=SimpleNamespace(id=5, status="draft", is_draft=True))
    svc._get_current_by_id = MagicMock(return_value=SimpleNamespace(review_kind="declined"))
    with pytest.raises(ReviewTransitionError, match="declined"):
        svc.build_fast_path_approval_payload(
            parent_type=ParentType.BILL, parent_public_id="pub", user_id=17,
        )


def test_the_builder_reads_the_FROZEN_kind_not_the_live_flags():
    """Codex P0, 2026-09-15 — and the sharpest finding of the unit.

    The builder used to test `status_is_final` / `status_is_declined`, which are
    joined from `dbo.ReviewStatus` and REWRITTEN whenever the final/initial role
    moves — while carrying a comment claiming it used frozen semantics. The
    comment asserted the opposite of the code.

    This fixture is the exact hazard: a row frozen `in_review` whose LIVE flags
    now say final-and-not-declined. Reading the flags returns None ("already
    approved, record nothing") and completion proceeds with NO approval on
    record. Reading the frozen kind records the approval correctly.
    """
    from entities.review.business.service import ReviewService

    svc = ReviewService()
    svc._resolve_parent = MagicMock(return_value=SimpleNamespace(id=5, status="draft", is_draft=True))
    svc._get_current_by_id = MagicMock(return_value=SimpleNamespace(
        review_kind="in_review",          # what was frozen at insert
        status_is_final=True,             # what the LIVE flags say now
        status_is_declined=False,
    ))
    svc.review_status_service = MagicMock()
    svc.review_status_service.get_approved_status.return_value = SimpleNamespace(id=99)

    payload = svc.build_fast_path_approval_payload(
        parent_type=ParentType.BILL, parent_public_id="pub", user_id=17,
    )
    assert payload is not None, (
        "the builder read the live flags: a reconfigured workflow made an "
        "in_review row look approved, and completion proceeded with no approval"
    )
    assert payload["review_status_id"] == 99


def test_the_builder_does_not_overwrite_a_decline_whose_live_flag_moved():
    """The mirror case, and the worse one: a historical decline whose live
    declined flag has moved would be silently stamped with an approval."""
    from entities.review.business.service import ReviewService, ReviewTransitionError

    svc = ReviewService()
    svc._resolve_parent = MagicMock(return_value=SimpleNamespace(id=5, status="draft", is_draft=True))
    svc._get_current_by_id = MagicMock(return_value=SimpleNamespace(
        review_kind="declined",           # frozen truth
        status_is_final=False,            # live flags no longer say declined
        status_is_declined=False,
    ))
    with pytest.raises(ReviewTransitionError, match="declined"):
        svc.build_fast_path_approval_payload(
            parent_type=ParentType.BILL, parent_public_id="pub", user_id=17,
        )


def test_the_fast_path_is_idempotent_on_an_already_approved_document():
    """Returns None -> nothing recorded.

    This is what makes a retried completion safe: the Bill route COALESCES
    concurrent jobs (`if job.was_created`), so the same completion can arrive
    twice, and the second must not mint a duplicate Approved row.
    """
    from entities.review.business.service import ReviewService

    svc = ReviewService()
    svc._resolve_parent = MagicMock(return_value=SimpleNamespace(id=5, status="draft", is_draft=True))
    svc._get_current_by_id = MagicMock(return_value=SimpleNamespace(review_kind="approved"))
    assert svc.build_fast_path_approval_payload(
        parent_type=ParentType.BILL, parent_public_id="pub", user_id=17,
    ) is None


def test_the_fast_path_targets_the_FINAL_status_not_the_next_one():
    """`build_advance_payload` steps one status; under submitted -> in_review ->
    approved that lands on `in_review`, which a `require_approved` gate would
    then refuse — a fast-path that cannot complete anything."""
    from entities.review.business.service import ReviewService

    svc = ReviewService()
    svc._resolve_parent = MagicMock(return_value=SimpleNamespace(id=5, status="draft", is_draft=True))
    svc._get_current_by_id = MagicMock(return_value=SimpleNamespace(review_kind="submitted"))
    svc.review_status_service = MagicMock()
    svc.review_status_service.get_approved_status.return_value = SimpleNamespace(id=99)

    payload = svc.build_fast_path_approval_payload(
        parent_type=ParentType.BILL, parent_public_id="pub", user_id=17,
    )
    assert payload["review_status_id"] == 99
    assert payload["bill_id"] == 5
    assert payload["user_id"] == 17
    svc.review_status_service.get_next_status.assert_not_called()


def test_the_fast_path_refuses_when_no_final_status_is_configured():
    from entities.review.business.service import ReviewService, ReviewTransitionError

    svc = ReviewService()
    svc._resolve_parent = MagicMock(return_value=SimpleNamespace(id=5, status="draft", is_draft=True))
    svc._get_current_by_id = MagicMock(return_value=None)
    svc.review_status_service = MagicMock()
    svc.review_status_service.get_approved_status.return_value = None
    with pytest.raises(ReviewTransitionError, match="final review status"):
        svc.build_fast_path_approval_payload(
            parent_type=ParentType.BILL, parent_public_id="pub", user_id=17,
        )


def test_get_approved_status_picks_the_single_active_final_row():
    """The shape rails guarantee exactly one; this pins that we read THAT one and
    not, say, the declined row (which is also terminal)."""
    from entities.review_status.business.service import ReviewStatusService

    rows = [
        SimpleNamespace(id=1, is_active=True, is_final=False, is_declined=False),
        SimpleNamespace(id=2, is_active=False, is_final=True, is_declined=False),   # inactive
        SimpleNamespace(id=3, is_active=True, is_final=False, is_declined=True),    # declined
        SimpleNamespace(id=4, is_active=True, is_final=True, is_declined=False),    # <- this one
    ]
    svc = ReviewStatusService(repo=MagicMock(read_all=MagicMock(return_value=rows)))
    assert svc.get_approved_status().id == 4


# ---------------------------------------------------------------------------
# 5 — exemptions
# ---------------------------------------------------------------------------


def test_a_system_caller_bypasses_the_gate(monkeypatch):
    """QBO-origin births and outbox re-drives would otherwise be refused for
    never having been reviewed by a human who was never involved."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    resolve = MagicMock(side_effect=AssertionError("resolved a review for a system caller"))
    with patch("shared.lifecycle.completion_gate.is_exempt", return_value=True):
        enforce_completion_gate(ParentType.BILL, resolve_review=resolve)
    resolve.assert_not_called()


def test_a_non_exempt_caller_under_a_live_gate_is_refused(monkeypatch):
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_BLOCK_OPEN_REVIEW)
    with pytest.raises(CompletionRefused):
        enforce_completion_gate(
            ParentType.BILL, resolve_review=lambda: _review("submitted")
        )


def test_a_fast_path_with_no_recorder_refuses_rather_than_completing(monkeypatch):
    """Failing closed. A wiring mistake must not silently complete an unapproved
    money document."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    with pytest.raises(AssertionError, match="refusing to complete"):
        enforce_completion_gate(
            ParentType.BILL,
            resolve_review=lambda: _review("submitted"),
            record_approval=None,
            actor_can_approve=True,
        )


def test_the_recorder_runs_before_completion_proceeds(monkeypatch):
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    rec = MagicMock()
    enforce_completion_gate(
        ParentType.BILL,
        resolve_review=lambda: _review("submitted"),
        record_approval=rec,
        actor_can_approve=True,
    )
    rec.assert_called_once_with()


def test_an_approved_document_does_not_re_record(monkeypatch):
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    rec = MagicMock()
    enforce_completion_gate(
        ParentType.BILL,
        resolve_review=lambda: _review("approved"),
        record_approval=rec,
        actor_can_approve=True,
    )
    rec.assert_not_called()


def test_a_missing_review_row_reads_as_none_not_a_crash(monkeypatch):
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_BLOCK_OPEN_REVIEW)
    enforce_completion_gate(ParentType.BILL, resolve_review=lambda: None)


# ---------------------------------------------------------------------------
# 6 — the four routes actually wire it (the U-457 lesson)
# ---------------------------------------------------------------------------
#
# U-457 shipped helpers that every unit test exercised and that NO test asserted
# the routes called; a mutation reverting a route to a bare `to_dict()` stayed
# green. The same gap here would mean a gate that is perfectly correct and
# enforces nothing, on a money path.

ROUTES = [
    ("entities.bill.api.router", "complete_bill_router", ParentType.BILL),
    ("entities.expense.api.router", "complete_expense_router", ParentType.EXPENSE),
    ("entities.bill_credit.api.router", "complete_bill_credit_router", ParentType.BILL_CREDIT),
    ("entities.invoice.api.router", "complete_invoice_router", ParentType.INVOICE),
]


@pytest.mark.parametrize("module,fn_name,parent", ROUTES)
def test_the_complete_route_calls_the_gate(module, fn_name, parent):
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, fn_name))
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "gate_completion(" in executable, (
        f"{fn_name} does not call the completion gate — the gate enforces "
        "nothing on this entity no matter how it is configured"
    )
    assert parent in executable, f"{fn_name} must gate on ParentType {parent!r}"


@pytest.mark.parametrize("module,fn_name,parent", ROUTES)
def test_the_gate_runs_BEFORE_the_job_is_enqueued(module, fn_name, parent):
    """A refused completion must leave no CompletionJob row. If the gate ran
    after the enqueue, every refusal would strand a job for the reclaim watchdog
    to re-drive — turning a clean 422 into a retry storm against a document that
    will never pass."""
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, fn_name))
    gate_at = src.index("gate_completion(")
    enqueue_at = src.index(".enqueue(")
    assert gate_at < enqueue_at, (
        f"{fn_name} enqueues a CompletionJob before consulting the gate"
    )


@pytest.mark.parametrize("module,fn_name,parent", ROUTES)
def test_the_route_gates_on_its_OWN_entity(module, fn_name, parent):
    """Copy-paste across four routes is exactly how one ends up gating Bill's
    flag on the Invoice route.

    NB the `\\b` is load-bearing, and this test caught itself without it:
    `ParentType.BILL` is a SUBSTRING of `ParentType.BILL_CREDIT`, so a plain
    `in` check reports the bill-credit route as gating on Bill. Same shape as
    `P.[UserId]` inside `UP.[UserId]` and `CreateReview` inside
    `CreateReviewStatus` — the third time this exact trap has appeared in this
    workstream. `_` is a word character, so `\\bParentType\\.BILL\\b` does not
    match `ParentType.BILL_CREDIT`.
    """
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, fn_name))
    others = {ParentType.BILL, ParentType.EXPENSE, ParentType.BILL_CREDIT, ParentType.INVOICE} - {parent}
    gate_call = src[src.index("gate_completion("):]
    gate_call = gate_call[:gate_call.index(")\n")]
    for other in others:
        assert not re.search(rf"\bParentType\.{other.upper()}\b", gate_call), (
            f"{fn_name} passes ParentType.{other.upper()} to its gate"
        )


# ---------------------------------------------------------------------------
# 7 — the RBAC refactor did not drift
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("permission", ["can_read", "can_complete", "can_approve"])
@pytest.mark.parametrize("granted", [True, False])
def test_has_module_permission_agrees_with_the_enforcing_version(permission, granted):
    """U-458 split one decision into a raising and a non-raising consumer. If
    they ever disagree, the gate's "could this actor approve?" answer stops
    matching what `/approve/review/*` would actually allow."""
    from fastapi import HTTPException

    from shared.rbac import _enforce_module_permission, has_module_permission

    role_module = SimpleNamespace(can_read=granted, can_complete=granted, can_approve=granted)
    with patch("shared.rbac._get_user_permissions", return_value={"Bills": role_module}):
        boolean = has_module_permission({}, "Bills", permission)
        try:
            _enforce_module_permission({}, "Bills", permission)
            raised = False
        except HTTPException:
            raised = True
    assert boolean is granted
    assert boolean is not raised, "the two consumers disagree about the same decision"


def test_has_module_permission_still_honours_the_system_admin_bypass():
    from shared.rbac import SYSTEM_ADMIN_GRANT, has_module_permission

    with patch("shared.rbac._get_user_permissions", return_value=SYSTEM_ADMIN_GRANT):
        assert has_module_permission({}, "Bills", "can_approve") is True


def test_has_module_permission_rejects_an_invalid_permission_name():
    """Still a ValueError, not a silent False — a typo'd permission must not
    read as "this actor cannot approve" and quietly disable the fast-path."""
    from shared.rbac import has_module_permission

    with pytest.raises(ValueError):
        has_module_permission({}, "Bills", "can_frobnicate")


# ---------------------------------------------------------------------------
# 8 — status-code contract
# ---------------------------------------------------------------------------


def test_every_refusal_is_422_and_never_409(monkeypatch):
    """The U-446b rule, applied to this unit's three codes."""
    seen = []
    for gate, kind in [
        (GATE_BLOCK_OPEN_REVIEW, "submitted"),
        (GATE_BLOCK_OPEN_REVIEW, "declined"),
        (GATE_REQUIRE_APPROVED, "none"),
    ]:
        monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], gate)
        with pytest.raises(CompletionRefused) as e:
            evaluate_completion(parent_type=ParentType.BILL, review_kind=kind)
        assert e.value.status_code == 422, f"{gate}/{kind} answered {e.value.status_code}"
        seen.append(e.value.error_code)

    assert seen == [ErrorCode.REVIEW_OPEN, ErrorCode.REVIEW_DECLINED, ErrorCode.REVIEW_NOT_APPROVED]


def test_the_gate_module_never_mentions_409():
    """Asserted on the EXECUTABLE text: the module docstring explains at length
    why 409 is wrong, so an un-stripped scan matches the prose and passes while
    the code raises 409."""
    import shared.lifecycle.completion_gate as mod

    src = inspect.getsource(mod)
    body = src.split('"""', 2)[2] if src.count('"""') >= 2 else src
    executable = "\n".join(l.split("#")[0] for l in body.splitlines())
    assert not re.search(r"\b409\b", executable), "the gate raises a 409 somewhere"


# ---------------------------------------------------------------------------
# 9 — the ADAPTER's own early return (a mutation survivor, found and closed)
# ---------------------------------------------------------------------------
#
# `test_off_does_not_even_resolve_the_review` covers the PURE function. It does
# not cover `gate_completion`, which has its own early return — and a mutation
# deleting that one survived the whole battery. Without it, `has_module_permission`
# runs on EVERY completion in the system, gate or no gate: a permission-cache (and
# on a cold key, database) round trip bought for a feature nobody enabled. That is
# precisely the half of "ships off" that is about cost rather than refusals.
#
# Same shape as the gap U-457 found: the helpers were tested exhaustively and
# nothing asserted what the caller did with them.


def _exploding_user():
    class _U(dict):
        def get(self, *a, **k):  # noqa: D102
            raise AssertionError("touched current_user under an off gate")
    return _U()


def test_gate_completion_resolves_NOTHING_while_the_gate_is_off():
    from entities.review.business import completion as adapter

    resolve = MagicMock(side_effect=AssertionError("resolved the review under an off gate"))
    with patch.object(adapter, "has_module_permission",
                      side_effect=AssertionError("resolved permissions under an off gate")) as perms:
        adapter.gate_completion(
            parent_type=ParentType.BILL,
            parent_public_id="pub-1",
            module_name="Bills",
            current_user={},
            resolve_review=resolve,
        )
    perms.assert_not_called()
    resolve.assert_not_called()


@pytest.mark.parametrize("parent,module", [
    (ParentType.BILL, "Bills"),
    (ParentType.EXPENSE, "Expenses"),
    (ParentType.BILL_CREDIT, "Bill Credits"),
    (ParentType.INVOICE, "Invoices"),
])
def test_every_entity_is_free_while_its_gate_is_off(parent, module):
    """Per entity, because a fourth-entity omission is this workstream's
    signature defect."""
    from entities.review.business import completion as adapter

    with patch.object(adapter, "has_module_permission",
                      side_effect=AssertionError(f"{parent}: permissions resolved under an off gate")):
        adapter.gate_completion(
            parent_type=parent,
            parent_public_id="pub-1",
            module_name=module,
            current_user={},
            resolve_review=lambda: (_ for _ in ()).throw(
                AssertionError(f"{parent}: review resolved under an off gate")
            ),
        )


def test_gate_completion_DOES_resolve_permissions_once_the_gate_is_live(monkeypatch):
    """The complement — otherwise the test above is satisfied by a gate that
    never does anything at all."""
    from entities.review.business import completion as adapter

    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_BLOCK_OPEN_REVIEW)
    with patch.object(adapter, "has_module_permission", return_value=False) as perms:
        adapter.gate_completion(
            parent_type=ParentType.BILL,
            parent_public_id="pub-1",
            module_name="Bills",
            current_user={},
            resolve_review=lambda: _review("approved"),
        )
    perms.assert_called_once_with({}, "Bills", "can_approve")


# ---------------------------------------------------------------------------
# 10 — malformed review state must REFUSE, not degrade (Codex P2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gate", [GATE_BLOCK_OPEN_REVIEW, GATE_REQUIRE_APPROVED])
@pytest.mark.parametrize("bad", ["", "  ", "Submitted?", "pending", "APPROVED_", "1"])
def test_an_unrecognised_review_kind_refuses(gate, bad, monkeypatch):
    """Collapsing an unknown value into `none` is fail-OPEN: a malformed row on
    a document sitting in someone's queue would complete straight through."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], gate)
    with pytest.raises(CompletionRefused) as e:
        evaluate_completion(parent_type=ParentType.BILL, review_kind=bad)
    assert e.value.status_code == 422
    assert e.value.error_code == ErrorCode.REVIEW_STATE_UNKNOWN


def test_an_absent_review_row_still_means_never_submitted(monkeypatch):
    """The case that must NOT be swept up by the refusal above — `none` is the
    AP fast path, and breaking it would refuse every bill that was never sent
    for review."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_BLOCK_OPEN_REVIEW)
    assert evaluate_completion(parent_type=ParentType.BILL, review_kind=None) is False


def test_a_review_row_with_a_NULL_kind_refuses_rather_than_reading_as_none(monkeypatch):
    """The distinction only works if `enforce_completion_gate` keeps "no row"
    and "row with no kind" apart — they both arrive as None otherwise."""
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_BLOCK_OPEN_REVIEW)
    with pytest.raises(CompletionRefused) as e:
        enforce_completion_gate(
            ParentType.BILL, resolve_review=lambda: SimpleNamespace(review_kind=None)
        )
    assert e.value.error_code == ErrorCode.REVIEW_STATE_UNKNOWN


def test_get_approved_status_REFUSES_when_two_finals_are_configured():
    """Codex P1: picking the first would stamp an arbitrary status onto an
    approval audit row, chosen by whatever order the sproc returned."""
    from entities.review_status.business.service import (
        ReviewStatusService,
        ReviewStatusShapeError,
    )

    rows = [
        SimpleNamespace(id=4, name="Approved", is_active=True, is_final=True, is_declined=False),
        SimpleNamespace(id=5, name="Authorised", is_active=True, is_final=True, is_declined=False),
    ]
    svc = ReviewStatusService(repo=MagicMock(read_all=MagicMock(return_value=rows)))
    with pytest.raises(ReviewStatusShapeError, match="exactly one"):
        svc.get_approved_status()


# ---------------------------------------------------------------------------
# 11 — the bypass, closed (Codex P0-3)
# ---------------------------------------------------------------------------
#
# A gate on POST /complete/* is worth nothing if a document can be marked
# completed some other way. Codex found two live doors on Expense, BillCredit
# and Invoice:
#
#   1. `PUT /update/*` accepted `is_draft: false` under `can_update`, and the
#      sprocs wrote it — so a user WITHOUT `can_complete` could complete a money
#      document, with no review consulted.
#   2. The agent `update_{expense,bill_credit,invoice}` tools exposed `is_draft`
#      as a writable field, while the agent PROMPT said "do NOT just flip
#      is_draft" — an instruction where an enforcement belonged. Same class as
#      the U-446d P0 on `create_bill`.
#
# Bill was never exposed: U-446 made its `IsDraft` a PERSISTED COMPUTED column
# over `Status`, so it is not writable at all. Expense joined it in U-467.
# These tests hold BillCredit and Invoice to the same property at the edge.
#
# NOT closed here, and booked: BillCredit and Invoice completion still write
# `is_draft=False` through the service layer, so an INTERNAL caller could still
# flip it. Expense completion now goes through `FinalizeExpenseById` (U-467).
# The structural fix for the remaining two is a `Finalize*ById` transition
# sproc per entity (what U-434 gave Bill) — which arrives with LS-03b/d.
# The threat model closed here is external callers: `can_update` users and agents.

BYPASS_SURFACES = [
    ("entities.bill.api.schemas", "BillUpdate", "entities.bill.intelligence.tools", "UpdateBillArgs"),
    ("entities.expense.api.schemas", "ExpenseUpdate", "entities.expense.intelligence.tools", "UpdateExpenseArgs"),
    ("entities.bill_credit.api.schemas", "BillCreditUpdate", "entities.bill_credit.intelligence.tools", "UpdateBillCreditArgs"),
    ("entities.invoice.api.schemas", "InvoiceUpdate", "entities.invoice.intelligence.tools", "UpdateInvoiceArgs"),
]


@pytest.mark.parametrize("schema_mod,schema_cls,tools_mod,args_cls", BYPASS_SURFACES)
def test_the_update_schema_no_longer_accepts_is_draft(schema_mod, schema_cls, tools_mod, args_cls):
    import importlib

    model = getattr(importlib.import_module(schema_mod), schema_cls)
    assert "is_draft" not in model.model_fields, (
        f"{schema_cls} still accepts is_draft — PUT /update/* can complete the "
        "document without can_complete and without consulting the gate"
    )


@pytest.mark.parametrize("schema_mod,schema_cls,tools_mod,args_cls", BYPASS_SURFACES)
def test_the_agent_update_tool_no_longer_accepts_is_draft(schema_mod, schema_cls, tools_mod, args_cls):
    import importlib

    model = getattr(importlib.import_module(tools_mod), args_cls)
    assert "is_draft" not in model.model_fields, (
        f"{args_cls} still exposes is_draft — the agent fleet keeps a lever the "
        "prompt tells it not to pull"
    )


@pytest.mark.parametrize("schema_mod,schema_cls,tools_mod,args_cls", BYPASS_SURFACES)
def test_a_client_that_still_sends_is_draft_gets_a_200_not_a_422(
    schema_mod, schema_cls, tools_mod, args_cls
):
    """build.one.web echoes the stored `is_draft` on EVERY save (all three edit
    pages load `is_draft: item.is_draft` and send `is_draft: form.is_draft`).
    Rejecting the field would break every save in the app; ignoring it is the
    LS-03a behaviour ("PUT/POST ignoring is_draft") and is what pydantic does by
    default. This test is the reason `extra="forbid"` must never be added to
    these three models without revisiting the web.
    """
    import importlib

    model = getattr(importlib.import_module(schema_mod), schema_cls)
    # Minimal valid body: every required field filled with a plausible dummy,
    # plus the is_draft a real client still sends.
    dummy = {"str": "x", "int": 1, "float": 1.0, "bool": True}
    payload = {}
    for name, f in model.model_fields.items():
        if not f.is_required():
            continue
        ann = str(f.annotation)
        payload[name] = next(
            (v for k, v in dummy.items() if k in ann.lower()), "x"
        )
    payload["is_draft"] = False

    parsed = model(**payload)  # must NOT raise on the extra field
    assert not hasattr(parsed, "is_draft"), (
        f"{schema_cls} still carries is_draft through to the service"
    )


@pytest.mark.parametrize("module,fn_name", [
    ("entities.bill.api.router", "update_bill_by_public_id_router"),
    ("entities.expense.api.router", "update_expense_by_public_id_router"),
    ("entities.bill_credit.api.router", "update_bill_credit_by_public_id_router"),
    ("entities.invoice.api.router", "update_invoice_by_public_id_router"),
])
def test_the_update_route_does_not_forward_is_draft(module, fn_name):
    """Belt to the schema's braces: even if the field came back, the route must
    not pass a caller-supplied value through to the sproc."""
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, fn_name))
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "body.is_draft" not in executable, (
        f"{fn_name} forwards a caller-supplied is_draft to the update sproc"
    )


def test_bill_was_NOT_immune_and_its_door_is_closed_too():
    """A correction to an earlier claim in this unit.

    I asserted Bill was structurally immune because U-446 made `IsDraft` a
    PERSISTED COMPUTED column, so it cannot be written. The column part is true
    and the conclusion was wrong: `UpdateBillById` carries a U-445 COMPAT
    TRANSLATION that maps `@IsDraft = 0` to `Status = 'completed'` — kept
    deliberately for the QBO pull connectors and queued iOS PUTs, which call the
    SERVICE directly. The computed column blocks the direct write; the
    translation routes around it. So `PUT /update/bill` with `is_draft: false`
    completed a bill exactly like the other three, on the busiest entity.

    This test pins BOTH halves: the translation still exists (internal callers
    depend on it) and the HTTP route can no longer reach it.
    """
    from pathlib import Path

    sql = Path(__file__).resolve().parents[1] / "entities/bill/sql/dbo.bill.sql"
    executable = "\n".join(l.split("--")[0] for l in sql.read_text().splitlines())
    assert "WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN 'completed'" in executable, (
        "the U-445 compat translation is gone — the QBO pull connectors and any "
        "queued iOS PUT that still sends only @IsDraft would now fail the "
        "CK_Bill_Status_IsDraft check"
    )

    from entities.bill.api.schemas import BillUpdate

    assert "is_draft" not in BillUpdate.model_fields, (
        "PUT /update/bill accepts is_draft again — the compat translation turns "
        "it into Status='completed', bypassing the completion gate"
    )


# ---------------------------------------------------------------------------
# 12 — config validated at STARTUP (Codex P1-3, Chris's call)
# ---------------------------------------------------------------------------


def test_valid_and_unset_configurations_boot(monkeypatch):
    from shared.lifecycle.completion_gate import assert_completion_gates_valid

    assert_completion_gates_valid()  # nothing set
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.INVOICE], GATE_BLOCK_OPEN_REVIEW)
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.EXPENSE], GATE_OFF)
    assert_completion_gates_valid()


@pytest.mark.parametrize("parent", ALL_PARENTS)
def test_a_typo_refuses_to_boot(parent, monkeypatch):
    """Loud, immediate, attributable to the deploy that caused it — instead of
    silently disabling the control (a warning is not a control) or halting AP
    mid-request with an error that reads like a completion bug."""
    from shared.lifecycle.completion_gate import (
        InvalidCompletionGateConfig,
        assert_completion_gates_valid,
    )

    monkeypatch.setenv(ENV_VAR_BY_PARENT[parent], "require-approved")  # hyphen
    with pytest.raises(InvalidCompletionGateConfig) as e:
        assert_completion_gates_valid()
    assert ENV_VAR_BY_PARENT[parent] in str(e.value)
    assert "require-approved" in str(e.value)


def test_the_boot_error_names_EVERY_bad_flag_not_just_the_first(monkeypatch):
    """One restart per typo is a bad loop to put an operator in."""
    from shared.lifecycle.completion_gate import (
        InvalidCompletionGateConfig,
        assert_completion_gates_valid,
    )

    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], "nope")
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.INVOICE], "also-nope")
    with pytest.raises(InvalidCompletionGateConfig) as e:
        assert_completion_gates_valid()
    assert ENV_VAR_BY_PARENT[ParentType.BILL] in str(e.value)
    assert ENV_VAR_BY_PARENT[ParentType.INVOICE] in str(e.value)


def test_app_calls_the_validator_at_import():
    """The validator is worthless if nothing runs it — the U-457 wiring lesson."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "assert_completion_gates_valid()" in executable, (
        "app.py does not validate the completion gate configuration at startup"
    )
    assert executable.index("assert_completion_gates_valid()") < executable.index("app = FastAPI()"), (
        "validation must run BEFORE the app is constructed"
    )


# ---------------------------------------------------------------------------
# 13 — the CREATE door (Codex P0, round 2)
# ---------------------------------------------------------------------------
#
# Closing the UPDATE door was not enough: every create schema accepted
# `is_draft: false`, so a `can_create` caller could mint a document that was
# ALREADY completed — never completed through the pipeline, so its AP never
# reached QBO / SharePoint / Excel / Box, and no gate ever saw it. The three
# agent create tools carried the same field, on handlers that already hard-coded
# `True` in the payload: an advertised lever that did nothing.
#
# This is not scope creep. Design §4.2 already says create-as-completed is
# "accepted ONLY under system_authz" — the QBO pull connectors and CLI sync,
# which reach it through the SERVICE layer and keep their parameter. The HTTP
# and agent surfaces were simply never brought into line. Same class as the
# U-446d P0 on `create_bill`.

CREATE_SURFACES = [
    ("entities.bill.api.schemas", "BillCreate"),
    ("entities.expense.api.schemas", "ExpenseCreate"),
    ("entities.bill_credit.api.schemas", "BillCreditCreate"),
    ("entities.invoice.api.schemas", "InvoiceCreate"),
]


@pytest.mark.parametrize("mod,cls", CREATE_SURFACES)
def test_the_create_schema_cannot_mint_a_completed_document(mod, cls):
    import importlib

    model = getattr(importlib.import_module(mod), cls)
    assert "is_draft" not in model.model_fields, (
        f"{cls} accepts is_draft — a can_create caller can mint an already-"
        "completed document whose AP never reaches QBO/SharePoint/Excel/Box"
    )


@pytest.mark.parametrize("mod,cls", [
    ("entities.expense.intelligence.tools", "CreateExpenseArgs"),
    ("entities.bill_credit.intelligence.tools", "CreateBillCreditArgs"),
    ("entities.invoice.intelligence.tools", "CreateInvoiceArgs"),
])
def test_the_agent_create_tool_cannot_mint_a_completed_document(mod, cls):
    import importlib

    model = getattr(importlib.import_module(mod), cls)
    assert "is_draft" not in model.model_fields, f"{cls} still exposes is_draft"


@pytest.mark.parametrize("module,fn_name", [
    ("entities.bill.api.router", "create_bill_router"),
    ("entities.expense.api.router", "create_expense_router"),
    ("entities.bill_credit.api.router", "create_bill_credit_router"),
    ("entities.invoice.api.router", "create_invoice_router"),
])
def test_the_create_route_hardcodes_draft(module, fn_name):
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, fn_name))
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "body.is_draft" not in executable, (
        f"{fn_name} still reads a caller-supplied is_draft"
    )
    # the `"?` matches both the kwarg form (`is_draft=True`) and the payload-dict
    # form (`"is_draft": True`) the four routes use between them
    assert re.search(r'is_draft"?\s*[=:]\s*True', executable), (
        f"{fn_name} must create as a draft unconditionally"
    )


def test_the_service_layer_KEEPS_its_is_draft_parameter():
    """The complement, and the thing that would break if the fix were applied
    one layer too deep. QBO pull connectors legitimately create completed
    documents under `system_authz` (design §4.2), and completion itself writes
    `is_draft=False` there — all through the service, never the route."""
    import inspect as _i

    from entities.expense.business.service import ExpenseService

    assert "is_draft" in _i.signature(ExpenseService.create).parameters, (
        "the service lost its is_draft parameter — the QBO pull can no longer "
        "record an already-paid QBO purchase as completed"
    )


# ---------------------------------------------------------------------------
# 14 — the fast-path authorises on BOTH permissions (Codex P1, round 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("approve,submit,expected", [
    (True, True, True),     # both -> may fast-path
    (True, False, False),   # design says yes, live review API says no -> NO
    (False, True, False),   # live API says yes, design says no -> NO
    (False, False, False),
])
def test_the_fast_path_needs_can_approve_AND_can_submit(approve, submit, expected, monkeypatch):
    """The fast-path WRITES a Review row. Every route that writes one today
    (/submit, /advance, /decline) requires `can_submit` — pinned by
    `tests/test_review_route_rbac.py`. Authorising on `can_approve` alone would
    let it record an approval that `POST /advance/review/*` would refuse from the
    same actor. Requiring both means the fast-path is never more permissive than
    either the design's rule or the live API's.
    """
    from entities.review.business import completion as adapter

    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    perms = {"can_approve": approve, "can_submit": submit}
    rec = MagicMock()

    with patch.object(adapter, "has_module_permission",
                      side_effect=lambda u, m, p: perms[p]), \
         patch.object(adapter, "enforce_completion_gate") as enforce:
        adapter.gate_completion(
            parent_type=ParentType.BILL,
            parent_public_id="pub-1",
            module_name="Bills",
            current_user={},
            resolve_review=lambda: _review("submitted"),
        )
    assert enforce.call_args.kwargs["actor_can_approve"] is expected


def test_the_review_routes_still_require_can_submit():
    """If this ever flips to `can_approve` (LS-06c), the second clause in the
    adapter becomes redundant and should be dropped — but not before."""
    from entities.review.api import router as review_router

    src = inspect.getsource(review_router)
    assert '"can_submit"' in src, (
        "the review routes no longer require can_submit — revisit the "
        "fast-path's dual-permission check in entities/review/business/completion.py"
    )


# ---------------------------------------------------------------------------
# 15 — require_approved warns about what it does NOT yet guarantee
# ---------------------------------------------------------------------------
#
# Codex's central argument across three rounds, and it is right: `require_approved`
# checks that an APPROVED REVIEW EXISTS, not that an approver created it. The gate
# is only ever as strong as the weakest writer of that row, and today two writers
# are weaker than `can_approve`:
#
#   * POST /advance/review/* requires only `can_submit`, and advancing from the
#     last intermediate status lands on the final (approved) one. Design §4.2
#     specifies can_approve for that transition; unification is LS-06c.
#   * POST /bill/{id}/apply-reviewer-decision requires only `can_update` and
#     takes `reviewer_email` from the BODY, writing an approval attributed to
#     that person — nothing binds the authenticated caller to the asserted
#     reviewer. (Verified directly, not taken on the reviewer's word.)
#
# Neither was introduced by U-458. The flag is designed to flip WITHOUT a deploy,
# so no code review happens at the moment it takes effect — this warning is the
# only thing that reaches the person making that call. It is not a refusal:
# running a partial control is an operational decision, not this module's.


def test_enabling_require_approved_warns_about_the_ungated_writers(monkeypatch, caplog):
    from shared.lifecycle.completion_gate import assert_completion_gates_valid

    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    with caplog.at_level(logging.WARNING):
        assert_completion_gates_valid()

    msg = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert "LIFECYCLE_COMPLETION_GATE_BILL" in msg
    assert "can_submit" in msg, "the /advance/review/* gap must be named"
    assert "apply-reviewer-decision" in msg, "the impersonation path must be named"
    assert "PREFLIGHT" in msg or "preflight" in msg, "the atomicity residual must be named"


@pytest.mark.parametrize("gate", [GATE_OFF, GATE_BLOCK_OPEN_REVIEW])
def test_the_other_gates_do_not_warn(gate, monkeypatch, caplog):
    """`block_open_review` asks a question no one can forge an answer to — it
    refuses while a review is OPEN, and the ungated writers can only move a
    review forward, not hide one. Only `require_approved` depends on who wrote
    the row."""
    from shared.lifecycle.completion_gate import assert_completion_gates_valid

    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], gate)
    with caplog.at_level(logging.WARNING):
        assert_completion_gates_valid()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_warning_names_every_entity_set_to_require_approved(monkeypatch, caplog):
    from shared.lifecycle.completion_gate import assert_completion_gates_valid

    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL], GATE_REQUIRE_APPROVED)
    monkeypatch.setenv(ENV_VAR_BY_PARENT[ParentType.BILL_CREDIT], GATE_REQUIRE_APPROVED)
    with caplog.at_level(logging.WARNING):
        assert_completion_gates_valid()
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert ENV_VAR_BY_PARENT[ParentType.BILL] in msg
    assert ENV_VAR_BY_PARENT[ParentType.BILL_CREDIT] in msg


def test_apply_reviewer_decision_still_does_not_bind_the_caller():
    """Pins the finding itself, so closing it is a deliberate act that updates
    this test rather than something that quietly drifts.

    If this ever starts binding `reviewer_email` to the authenticated user, the
    warning above can drop its second clause.
    """
    import entities.bill.api.router as bill_router

    src = inspect.getsource(bill_router.apply_reviewer_decision_router)
    assert '"can_update"' in src, (
        "apply-reviewer-decision's permission changed — re-check whether the "
        "require_approved warning still needs to name it"
    )
