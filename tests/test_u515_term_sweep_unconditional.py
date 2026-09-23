"""
U-515: the qbo.Term -> PaymentTerm catch-up sweep is deliberately unconditional.

`sync_qbo_term` used to declare `resync_existing: bool = False`, documented as
gating step 2. Nothing read it, no caller passed it, and the module has no
argparse -- so it was False on 100% of invocations while the sweep ran anyway.
The parameter was deleted rather than honored, because honoring it would have
turned the sweep into dead code on every automated path:

  * scripts/sync_qbo_term.py's __main__ -> run_locked() -> no args
  * shared/scheduler.py's ("term", sync_qbo_term) -> no args
  * shared/api/admin.py's dispatcher    -> no args

and the sweep is the ONLY recovery path for a term whose projection failed
once. A projection failure holds the watermark, but the term timer fires every
4h (`0 40 */4 * * *`) against a 7200s default hold bound -- the next tick is
already past the bound, so it force-advances and the incremental query never
covers that term's window again.

These tests pin that decision against a future re-gating.
"""
import ast
from unittest.mock import MagicMock, patch
import inspect
from pathlib import Path

import scripts.sync_qbo_term as term_script

SCRIPT = Path(term_script.__file__)


def _fn(tree, name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_sweep_is_not_nested_in_any_conditional():
    """The whole point of choosing deletion over honoring the flag. If someone
    re-introduces `if resync_existing:` (or any other gate) around the sweep,
    this fails."""
    tree = ast.parse(SCRIPT.read_text())
    fn = _fn(tree, "sync_qbo_term")

    gated = []
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "sync_existing_terms_to_payment_terms"):
                    gated.append(node.lineno)

    assert not gated, (
        f"sync_existing_terms_to_payment_terms is inside a conditional at line(s) {gated}. "
        "It must stay unconditional: it is the only recovery path for a term whose "
        "projection failed, because term's 4h cadence exceeds the 7200s hold bound so "
        "the watermark force-advances past the failure before the next incremental pull."
    )


def test_sweep_is_actually_called():
    """Guards the test above from passing vacuously -- a deleted call is also
    'not inside a conditional'."""
    tree = ast.parse(SCRIPT.read_text())
    fn = _fn(tree, "sync_qbo_term")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "sync_existing_terms_to_payment_terms"]
    assert len(calls) == 1, f"expected exactly one sweep call, found {len(calls)}"


def test_entry_points_take_no_resync_parameter():
    """The deleted parameter stays deleted. Re-adding it without wiring argparse
    and every caller would recreate the always-False lie."""
    for fn in (term_script.sync_qbo_term, term_script.run_locked):
        params = inspect.signature(fn).parameters
        assert "resync_existing" not in params, (
            f"{fn.__name__} re-declares resync_existing. If a gate is genuinely "
            "wanted, wire argparse AND the scheduler/admin call sites, and revisit "
            "the force-advance argument in this module's docstring first."
        )


# ── Behavioural guard (added after an adversarial mutation audit, 2026-09-23) ──
#
# The AST tests above pin SYNTAX: "the sweep call is not lexically inside an
# `ast.If`". A mutation audit defeated that trivially, twice, with the suite
# staying green:
#
#     if not os.environ.get("QBO_TERM_RESYNC_EXISTING"):
#         return {...}                      # early-return BEFORE the call
#     existing_sync_result = sync_existing_terms_to_payment_terms(...)
#
#     existing_sync_result = False and sync_existing_terms_to_payment_terms(...)
#
# Neither puts the call inside an `if`, so neither test fired -- i.e. the exact
# "flag nobody sets" this unit deleted could be reintroduced under a different
# spelling. Syntax tests cannot pin a runtime fact; this one does.
def test_the_sweep_actually_runs_on_a_normal_invocation():
    """Drive the real `sync_qbo_term()` and assert the sweep was INVOKED.

    Mutation guard: add any early return, `False and ...`, or environment gate
    before the sweep call and this goes RED where the AST tests stay green.
    """
    import scripts.sync_qbo_term as mod

    calls = []

    def _spy(**kwargs):
        calls.append(kwargs)
        return {"synced": 0, "skipped": 0}

    with (
        patch.object(mod, "sync_existing_terms_to_payment_terms", _spy),
        patch.object(mod, "QboTermRepository", MagicMock()),
        patch.object(mod, "TermPaymentTermConnector", MagicMock()),
        patch.object(mod, "QboTermService", MagicMock()),
        patch.object(mod, "QboAuthService", MagicMock()),
        patch.object(mod, "SyncService", MagicMock()),
        patch.object(mod, "WatermarkRun", MagicMock()),
    ):
        try:
            mod.sync_qbo_term()
        except Exception:
            # The sweep is called before the parts we did not stub can fail; what
            # this test pins is that it was REACHED, not that the whole run
            # succeeds against a fully mocked world.
            pass

    assert calls, (
        "sync_existing_terms_to_payment_terms was never invoked. The sweep is "
        "UNCONDITIONAL by design: term's cron (14,400s) exceeds the 7,200s "
        "watermark hold bound, so a projection failure force-advances and only "
        "this sweep recovers the unmapped rows. Gating it -- by a flag, an env "
        "var, or an early return -- reintroduces a silent recovery gap."
    )

