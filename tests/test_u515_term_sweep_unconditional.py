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
