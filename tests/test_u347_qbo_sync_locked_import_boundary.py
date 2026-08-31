"""
U-347 guard test: every QBO entity-sync entry point must acquire its lock
through the shared `integrations/intuit/qbo/base/locking.py` wrappers
(`qbo_sync_lock` / `qbo_sync_locked_route` / `qbo_sync_locked_cli`), never by
importing `qbo_app_lock` directly and re-deriving the `qbo_sync:<entity>`
resource string itself. `qbo_app_lock` is the low-level primitive; only
`locking.py` may import it for entity-sync purposes — a second call site
computing that resource string is exactly the class of bug U-337's Pass-1
finding was (a hand-typed key silently drifting from the shared one).

Two independent checks, so a future gap can't slip through either half:

1. **Positive discovery** (`test_every_live_sync_route_uses_the_shared_decorator`):
   glob every `integrations/intuit/qbo/*/api/router.py`, textually identify
   which ones define a live `/sync/qbo-*` route (a `@router.post("/sync/qbo`
   handler whose body calls `.sync_from_qbo(`), and assert each one is
   decorated `@qbo_sync_locked_route(`. This is the same discovery shape
   that would have caught `item`'s unlocked route automatically — Map for
   this unit found it by hand because U-340's own inventory was a fixed
   list, not a glob; this test doesn't repeat that mistake. A new sibling
   router that adds a live `/sync/qbo-*` handler and forgets the decorator
   fails this test the moment it's added, without anyone updating a list
   here first.
2. **Negative import-boundary check** (`test_no_entry_point_imports_qbo_app_lock_directly`):
   for the full entry-point set (the routers found above, `shared/api/
   admin.py`'s dispatcher, every `scripts/sync_qbo_*.py` CLI script via the
   shared `_iter_sync_script_paths()` enumerator, and `scripts/
   repair_invoice_line_duplicates.py`), assert none of them imports
   `qbo_app_lock` from `integrations.intuit.qbo.base.locking` — only
   `locking.py` itself may.
3. **CLI decorator-target check** (`test_qbo_sync_locked_cli_only_decorates_run_locked`,
   U-347 /simplify altitude finding): `@qbo_sync_locked_cli(entity)` MUST be
   applied to a script's dedicated `run_locked()` function, never to the
   shared `sync_qbo_<entity>()` function the admin dispatcher also calls —
   decorating the shared function would make the admin path nest a second
   applock acquire of the same resource from a second DB session and
   self-deadlock (see `qbo_sync_locked_cli`'s own docstring). Checks 1/2
   above wouldn't catch this misuse (the decorator IS imported, and IS
   applied to *something*) — this is a distinct, narrower AST check on what
   it's applied to.

Mutation-proof (verified manually): reverting `item`'s router (or any other
listed file) to import `qbo_app_lock`/`qbo_entity_sync_lock_resource`
directly and hand-write the lock ceremony inline — the exact pre-U-347
shape — turns `test_no_entry_point_imports_qbo_app_lock_directly` RED;
removing the `@qbo_sync_locked_route(...)` decorator from any live sync
route (without removing the direct import) turns
`test_every_live_sync_route_uses_the_shared_decorator` RED; moving
`@qbo_sync_locked_cli(...)` from a script's `run_locked` onto its
`sync_qbo_<entity>` function turns
`test_qbo_sync_locked_cli_only_decorates_run_locked` RED.
"""

import ast
from pathlib import Path

from test_qbo_watermark_runner import _iter_sync_script_paths

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCKING_MODULE = "integrations.intuit.qbo.base.locking"

# Files outside the auto-discovered set (admin dispatcher's entity is a
# runtime path param, not a per-router literal; the repair script isn't a
# router or a sync_qbo_*.py CLI script) — hand-listed since neither fits
# either glob, with the reason a comment at each site.
EXTRA_ENTRY_POINTS = [
    REPO_ROOT / "shared" / "api" / "admin.py",
    REPO_ROOT / "scripts" / "repair_invoice_line_duplicates.py",
]

_SHARED_WRAPPER_NAMES = frozenset(
    {"qbo_sync_lock", "qbo_sync_locked_route", "qbo_sync_locked_cli"}
)


def _iter_qbo_router_paths() -> list[Path]:
    return sorted((REPO_ROOT / "integrations" / "intuit" / "qbo").glob("*/api/router.py"))


def _looks_like_live_sync_route(text: str) -> bool:
    """A router file defines a live QBO entity-sync route if it POSTs a
    `/sync/qbo-*` path AND actually calls `.sync_from_qbo(` somewhere in the
    file — excludes a route that exists but is dead code (e.g. invoice's,
    which logs and returns `[]` without ever calling `sync_from_qbo`)."""
    return '@router.post("/sync/qbo' in text and ".sync_from_qbo(" in text


def _is_live_sync_route_file(path: Path) -> bool:
    return _looks_like_live_sync_route(path.read_text(encoding="utf-8"))


def _locking_imports(path: Path) -> set[str]:
    """Names imported from `integrations.intuit.qbo.base.locking` (or its
    relative-import equivalents) via `from ... import ...` in this file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith(
            "base.locking"
        ):
            imported.update(alias.name for alias in node.names)
    return imported


def test_every_live_sync_route_uses_the_shared_decorator():
    offenders = []
    for path in _iter_qbo_router_paths():
        text = path.read_text(encoding="utf-8")
        if not _looks_like_live_sync_route(text):
            continue
        if "@qbo_sync_locked_route(" not in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "every router file with a live '/sync/qbo-*' route (one that actually "
        "calls .sync_from_qbo(...)) must decorate that handler with "
        "@qbo_sync_locked_route(<entity>) — found live, undecorated route(s) "
        "in: " + ", ".join(offenders)
    )


def test_no_entry_point_imports_qbo_app_lock_directly():
    entry_points = list(_iter_qbo_router_paths()) + list(_iter_sync_script_paths()) + EXTRA_ENTRY_POINTS
    offenders = []
    for path in entry_points:
        imported = _locking_imports(path)
        if "qbo_app_lock" in imported:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "only integrations/intuit/qbo/base/locking.py may import qbo_app_lock "
        "directly for entity-sync purposes — every other entry point must go "
        "through qbo_sync_lock / qbo_sync_locked_route / qbo_sync_locked_cli, "
        "so the qbo_sync:<entity> resource string has exactly one call site. "
        "Offenders (importing qbo_app_lock directly): " + ", ".join(offenders)
    )


def test_every_live_sync_entry_point_imports_a_shared_wrapper():
    """Complements the two checks above: confirms each entry point isn't
    merely *not* importing qbo_app_lock (which would be trivially true for
    an unrelated file) but is actually wired through one of the shared
    wrappers."""
    live_routers = [p for p in _iter_qbo_router_paths() if _is_live_sync_route_file(p)]
    entry_points = live_routers + list(_iter_sync_script_paths()) + EXTRA_ENTRY_POINTS
    offenders = []
    for path in entry_points:
        imported = _locking_imports(path)
        if not (imported & _SHARED_WRAPPER_NAMES):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "every QBO entity-sync entry point must import at least one of "
        f"{sorted(_SHARED_WRAPPER_NAMES)} from locking.py. Offenders (none "
        "found): " + ", ".join(offenders)
    )


def _qbo_sync_locked_cli_targets(path: Path) -> list[str]:
    """Names of every function `@qbo_sync_locked_cli(...)` decorates in this file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "qbo_sync_locked_cli"
            ):
                targets.append(node.name)
    return targets


def test_qbo_sync_locked_cli_only_decorates_run_locked():
    offenders = []
    for path in _iter_sync_script_paths():
        for target_name in _qbo_sync_locked_cli_targets(path):
            if target_name != "run_locked":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{target_name}")
    assert not offenders, (
        "@qbo_sync_locked_cli(...) must only ever decorate a script's "
        "dedicated run_locked() entry point — decorating the shared "
        "sync_qbo_<entity>() function instead makes the admin dispatcher "
        "(which calls that shared function directly while already holding "
        "the same lock) nest a second acquire and self-deadlock. "
        "Offenders (path:function_name): " + ", ".join(offenders)
    )
