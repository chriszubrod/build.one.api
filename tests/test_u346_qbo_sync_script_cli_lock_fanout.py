"""
U-346: fan out `sync_qbo_account.py`'s (U-340) `run_locked()` CLI-wrapper
pattern to the 10 sibling `scripts/sync_qbo_*.py` CLI-direct entry points —
bill, invoice, purchase, vendorcredit, vendor, customer, item, term,
company_info, reimburse_charge — booked as U-340's own follow-up in
`TODO.md` ("U-340 follow-up (QBO entity sync lock fan-out, 2026-08-31)"),
which named 9; `reimburse_charge` was a 10th script Codex Pass-1 caught with
the identical live admin-dispatcher entry point and gap (confirmed by direct
read: `"reimburse_charge"` is in `shared/api/admin.py::VALID_QBO_ENTITIES`
and handled by `_qbo_sync_fn`), fixed in this same unit rather than left for
a follow-up.

Before this unit each script called its `sync_qbo_*()` function directly
under `if __name__ == "__main__":` with no `qbo_app_lock` — a direct CLI run
of any of these could still race the (already-locked) admin `sync/qbo/
{entity}` dispatcher for the same entity+realm. Mirrors
`tests/test_u340_qbo_entity_sync_lock_fanout.py`'s two `run_locked`
assertions for `scripts/sync_qbo_account.py`, parametrized across all 10:
the lock is acquired and the sync call happens INSIDE it, keyed to the exact
same `qbo_sync:<entity>` resource string the admin dispatcher
(`shared/api/admin.py::_qbo_sync_fn`) computes for that entity; and a busy
lock short-circuits to a `{"status_code": 409, "result": {"success": False}}`
envelope without ever calling the underlying sync function.

Mutation-proof (verified manually per case): reverting any one script's
`run_locked()` back to calling `sync_qbo_<entity>()` directly turns that
case's `test_run_locked_denied_returns_409_and_skips_sync` RED (no lock to
deny) and drifting its `qbo_entity_sync_lock_resource(...)` key away from
the admin dispatcher's own key for that entity turns
`test_run_locked_key_matches_admin_dispatcher_key` RED.
"""

from contextlib import contextmanager

import pytest

import integrations.intuit.qbo.base.locking as locking
import scripts.sync_qbo_bill as bill_script
import scripts.sync_qbo_company_info as company_info_script
import scripts.sync_qbo_customer as customer_script
import scripts.sync_qbo_invoice as invoice_script
import scripts.sync_qbo_item as item_script
import scripts.sync_qbo_purchase as purchase_script
import scripts.sync_qbo_reimburse_charge as reimburse_charge_script
import scripts.sync_qbo_term as term_script
import scripts.sync_qbo_vendor as vendor_script
import scripts.sync_qbo_vendorcredit as vendorcredit_script
from conftest import mock_qbo_app_lock_denied
from test_qbo_watermark_runner import _iter_sync_script_paths

OK_RESULT = {"result": {"success": True}, "status_code": 200}

# Each case: (entity_key, module). `sync_fn_name` (f"sync_qbo_{entity_key}")
# and the run_locked() call are both derivable/uniform across every case, so
# they're computed inline in each test rather than carried as extra columns.
CASES = [
    ("bill", bill_script),
    ("invoice", invoice_script),
    ("purchase", purchase_script),
    ("vendorcredit", vendorcredit_script),
    ("vendor", vendor_script),
    ("customer", customer_script),
    ("item", item_script),
    ("term", term_script),
    ("company_info", company_info_script),
    ("reimburse_charge", reimburse_charge_script),
]
CASE_IDS = [c[0] for c in CASES]


@pytest.mark.parametrize("entity_key,module", CASES, ids=CASE_IDS)
def test_run_locked_acquires_lock_and_calls_sync_inside_it(
    monkeypatch, entity_key, module
):
    """`run_locked` locks on `qbo_sync:<entity>` and only calls the
    underlying `sync_qbo_*()` once the lock is held."""
    entered = []

    @contextmanager
    def _tracking_lock(resource_name, timeout_ms=15000):
        assert resource_name == f"qbo_sync:{entity_key}"
        entered.append("enter")
        yield True
        entered.append("exit")

    monkeypatch.setattr(locking, "qbo_app_lock", _tracking_lock)

    def _fake_sync(*args, **kwargs):
        assert entered == ["enter"]
        return OK_RESULT

    monkeypatch.setattr(module, f"sync_qbo_{entity_key}", _fake_sync)

    result = module.run_locked()

    assert entered == ["enter", "exit"]
    assert result == OK_RESULT


@pytest.mark.parametrize("entity_key,module", CASES, ids=CASE_IDS)
def test_run_locked_denied_returns_409_and_skips_sync(monkeypatch, entity_key, module):
    """A busy lock must short-circuit to a failure envelope (so
    `exit_nonzero_on_sync_failure` exits non-zero for cron/CLI callers)
    without ever calling the underlying `sync_qbo_*()`."""
    monkeypatch.setattr(locking, "qbo_app_lock", mock_qbo_app_lock_denied)
    called = []
    monkeypatch.setattr(
        module, f"sync_qbo_{entity_key}", lambda *a, **kw: called.append((a, kw))
    )

    result = module.run_locked()

    assert result["status_code"] == 409
    assert result["result"]["success"] is False
    assert called == []


@pytest.mark.parametrize("entity_key,module", CASES, ids=CASE_IDS)
def test_run_locked_key_matches_admin_dispatcher_key(monkeypatch, entity_key, module):
    """The CLI script's lock resource must be the exact string the admin
    `sync/qbo/{entity}` dispatcher computes for the same entity — a mismatch
    here is exactly the U-337 Pass-1 P1 that let two locked-individually
    entry points still race each other unlocked."""
    seen = []

    @contextmanager
    def _recording_lock(resource_name, timeout_ms=15000):
        seen.append(resource_name)
        yield True

    monkeypatch.setattr(locking, "qbo_app_lock", _recording_lock)
    monkeypatch.setattr(module, f"sync_qbo_{entity_key}", lambda *a, **kw: OK_RESULT)
    module.run_locked()

    admin_key = locking.qbo_entity_sync_lock_resource(entity_key)
    assert seen == [admin_key]


# --- repo-wide sweep: every sync_qbo_*.py's __main__ actually calls run_locked ---- #
#
# Codex Pass-1 (round 1) flagged two related gaps: (1) the CASES table above
# covers only the 9 scripts this unit set out to fix, missing that
# `sync_qbo_reimburse_charge.py` is a 10th script with the identical live
# admin-dispatcher entry (`"reimburse_charge"` is in `VALID_QBO_ENTITIES` and
# `_qbo_sync_fn` in shared/api/admin.py) and the identical unlocked-CLI gap;
# (2) the CASES tests call `run_locked()` directly, so they'd stay green even
# if a script's actual `if __name__ == "__main__":` block silently reverted to
# calling `sync_qbo_<entity>()` instead of `run_locked()`. This sweep closes
# both at once: it reuses `_iter_sync_script_paths()` (already the shared
# `scripts/sync_qbo_*.py` enumerator in `test_qbo_watermark_runner.py`, so a
# future 11th script can't slip through unlocked) and asserts each one's
# __main__ block textually calls `run_locked(` — proven RED against
# `sync_qbo_reimburse_charge.py` before that script was fixed in this same unit.


def _main_block_text(path) -> str:
    text = path.read_text(encoding="utf-8")
    marker = 'if __name__ == "__main__":'
    idx = text.index(marker)
    return text[idx:]


def test_every_sync_qbo_script_main_block_calls_run_locked():
    offenders = []
    for path in _iter_sync_script_paths():
        main_block = _main_block_text(path)
        if "run_locked(" not in main_block:
            offenders.append(path.name)
    assert not offenders, (
        "every sync_qbo_*.py's __main__ must route through a lock-wrapped "
        "run_locked() entry point (mirrors sync_qbo_account.py's U-340 "
        "pattern) so a direct CLI run can't race the admin dispatcher / "
        "scheduler for the same entity+realm. Offenders: " + ", ".join(offenders)
    )
