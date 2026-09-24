"""U-513 ph3b: the `qbo.PhysicalAddress` staging package is DELETED, and the
projection connector that outlived it keeps its contract.

WHAT PH3B ACTUALLY DID
----------------------
`qbo.PhysicalAddress` was a write-then-read-back cache: the QBO pull wrote an
address into it, then read the same row straight back to project it onto
`dbo.Address`. The content was inline on the originating payload the whole time.
ph1/ph2/ph2.5 repointed every family onto the payload, ph3a stopped the writes,
and ph3b removes what is left:

  DELETED  the staging service, repository, model, API router + schemas, the
           external client and its schemas, the package's SQL (table + 7 sprocs)
           and the `sync_from_qbo_to_address` wrapper that read a staging row.
  KEPT     `PhysicalAddressAddressConnector.sync_address_from_external` -- the
           projection itself, which four call sites in three OTHER packages
           (customer x2, vendor, company_info) depend on and which never touched
           the staging table.

That split is what this file pins, from both sides: the deleted half must be
UNIMPORTABLE (not merely unused -- a dead-but-importable module is how a staging
write gets reinstated), and the kept half must be importable at the same path
with a byte-identical signature (three packages bind against that spelling, and
a `Mock` in each of their own suites would swallow a drift that only explodes in
a live QBO pull).

It also pins the DROP migration's shape. The migration is written by this unit
and applied by hand later, so nothing else executes it before an operator does;
reading it as text is the only automated check it will ever get.

Pure logic. Nothing here imports the app, opens a connection, or reads anything
outside the repo tree.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

from integrations.intuit.qbo.physical_address.connector.business.service import (
    PhysicalAddressAddressConnector,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "integrations" / "intuit" / "qbo" / "physical_address"
MIGRATION = REPO_ROOT / "scripts" / "migrations" / "u513_drop_qbo_physical_address.sql"

# Every module that existed only to serve the staging table. Spelled out one by
# one rather than derived from the directory listing: a test that enumerates
# what IS there can never notice that something came back.
DELETED_MODULES = [
    "integrations.intuit.qbo.physical_address.business.service",   # QboPhysicalAddressService
    "integrations.intuit.qbo.physical_address.business.model",     # QboPhysicalAddress dataclass
    "integrations.intuit.qbo.physical_address.persistence.repo",   # QboPhysicalAddressRepository
    "integrations.intuit.qbo.physical_address.api.router",         # the 2 read routes
    "integrations.intuit.qbo.physical_address.api.schemas",
    "integrations.intuit.qbo.physical_address.external.client",    # QboPhysicalAddressClient
    "integrations.intuit.qbo.physical_address.external.schemas",
    "integrations.intuit.qbo.physical_address.web.controller",
]

# The exact 7 sprocs the deleted package's SQL file defined, and the one table.
EXPECTED_SPROCS = frozenset({
    "CreateQboPhysicalAddress",
    "ReadQboPhysicalAddresses",
    "ReadQboPhysicalAddressById",
    "ReadQboPhysicalAddressByPublicId",
    "ReadQboPhysicalAddressByQboId",
    "UpdateQboPhysicalAddressById",
    "DeleteQboPhysicalAddressById",
})
EXPECTED_TABLE = "qbo.PhysicalAddress"

# The U-513 shared contract, verbatim. Written out as a literal rather than
# derived from the method: a test that rebuilds the value the same way the code
# does cannot catch the code changing.
CONTRACT_PARAMS = [
    "qbo_id",
    "realm_id",
    "line1",
    "line2",
    "city",
    "country_sub_division_code",
    "postal_code",
    "source_ref",
]


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _strip_sql_comments(text: str) -> str:
    """Executable T-SQL only. The migration's header deliberately NAMES the
    columns it refuses to drop and the writers it refuses to race, so scanning
    raw text would make the 'no DROP COLUMN' assertion fail on the very comment
    that promises there is none.

    INLINE `--` trailers are stripped too, not just whole comment lines: a
    mutation that guts the guard and leaves its old mechanism named in a trailing
    comment (`SELECT @RecentWrites = 1; -- was sp_executesql`) otherwise keeps
    every substring assertion below satisfied while the guard does nothing.
    Safe for this file specifically -- no string literal in it contains `--`.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("--"):
            continue
        lines.append(line.split("--", 1)[0] if "--" in line else line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. The staging half is gone -- unimportable, not merely unused.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", DELETED_MODULES)
def test_every_staging_module_is_unimportable(module_name):
    """`ModuleNotFoundError`, not `ImportError` on a symbol: the whole module is
    gone, so there is no file left for someone to add a class back into."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


@pytest.mark.parametrize(
    "subpackage", ["business", "persistence", "api", "external", "web", "sql"]
)
def test_every_staging_subpackage_directory_is_gone(subpackage):
    """The company_info sunset (U-505b) left `persistence/__init__.py` behind
    because that package still had live layers around it. Here only `connector/`
    survives, so an empty `business/` or `persistence/` directory would be pure
    residue -- and an `__init__.py` is enough to make `importlib` succeed, which
    would quietly weaken the test above."""
    assert not (PACKAGE_ROOT / subpackage).exists()


def test_the_package_contains_nothing_but_the_connector():
    """The inverse of the enumerated list above: whatever survived must be
    exactly the connector subtree. Catches a staging file re-added under a name
    DELETED_MODULES does not happen to list."""
    survivors = sorted(
        str(p.relative_to(PACKAGE_ROOT))
        for p in PACKAGE_ROOT.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    assert survivors == [
        "__init__.py",
        "connector/__init__.py",
        "connector/business/__init__.py",
        "connector/business/service.py",
    ]


def test_no_module_anywhere_still_imports_the_deleted_package():
    """A stale import in a module nothing imports at collection time would not
    surface as a test failure -- it would surface as a 500 in a QBO pull. Scan
    the source instead of relying on import side effects.

    Deliberately AST-based rather than a grep: the migration and this file both
    mention the dotted paths in prose, and a text scan would flag its own
    documentation."""
    offenders = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in {".venv", "__pycache__", ".worktrees"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            for name in names:
                if name in DELETED_MODULES or name.rsplit(".", 1)[0] in {
                    "integrations.intuit.qbo.physical_address.business",
                    "integrations.intuit.qbo.physical_address.persistence",
                    "integrations.intuit.qbo.physical_address.api",
                    "integrations.intuit.qbo.physical_address.external",
                    "integrations.intuit.qbo.physical_address.web",
                }:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> {name}")
    assert not offenders, "imports of the deleted staging package survive: " + ", ".join(offenders)


def test_the_app_no_longer_mounts_the_staging_router():
    """`app.py` imported and included the package's router. Both lines must be
    gone, or the app fails to start -- a failure mode the pure-logic suite would
    otherwise only discover at deploy."""
    app_source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    assert "intuit_qbo_physical_address_api_router" not in app_source
    assert "physical_address" not in app_source


def test_the_connector_no_longer_carries_the_staging_seam():
    """The two members that reached the staging table from inside the surviving
    class. Both must be gone from the CLASS, not merely unused: a live
    `qbo_physical_address_service` attribute would construct the deleted service
    on every instantiation."""
    assert not hasattr(PhysicalAddressAddressConnector, "sync_from_qbo_to_address")

    ctor = inspect.signature(PhysicalAddressAddressConnector.__init__)
    assert "qbo_physical_address_service" not in ctor.parameters
    assert list(ctor.parameters) == ["self", "address_service", "reconciliation_repo"]

    source = inspect.getsource(PhysicalAddressAddressConnector)
    assert "QboPhysicalAddressService" not in source
    assert "QboPhysicalAddressRepository" not in source


# ---------------------------------------------------------------------------
# 2. The surviving half keeps its contract, at its own import path.
# ---------------------------------------------------------------------------


def test_the_contract_is_importable_from_the_connectors_home():
    """Where the projection LIVES is part of the contract: four production call
    sites in three other packages bind `PhysicalAddressAddressConnector` by this
    exact dotted path, and two of those packages were being edited in parallel
    with this unit. Moving the module would break them with no local failure to
    warn whoever did it, so the path is pinned here alongside the signature."""
    module = importlib.import_module(
        "integrations.intuit.qbo.physical_address.connector.business.service"
    )
    assert module.PhysicalAddressAddressConnector is PhysicalAddressAddressConnector
    assert callable(PhysicalAddressAddressConnector.sync_address_from_external)


def test_sync_address_from_external_signature_is_unchanged():
    """THE regression this file exists for. Three packages call this; each of
    their own suites mocks it, and a `Mock` accepts a renamed keyword, a dropped
    default and a missing required argument identically. Only the real signature
    says no."""
    sig = inspect.signature(PhysicalAddressAddressConnector.sync_address_from_external)
    params = list(sig.parameters.values())

    assert params[0].name == "self"
    assert [p.name for p in params[1:]] == CONTRACT_PARAMS
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params[1:]), (
        "every caller-facing parameter must be keyword-only"
    )
    assert [p.name for p in params[1:] if p.default is not inspect.Parameter.empty] == [
        "source_ref"
    ], "source_ref is the ONLY optional parameter -- everything else must be supplied"
    assert sig.parameters["source_ref"].default is None


# ---------------------------------------------------------------------------
# 3. The DROP migration's shape.
# ---------------------------------------------------------------------------


def test_the_migration_file_exists():
    assert MIGRATION.is_file(), f"{MIGRATION} was not written"


def test_the_migration_drops_exactly_the_seven_sprocs():
    """Exactly: a missing one leaves a live sproc pointing at a dropped table
    (a confusing runtime error instead of an import-time failure), and an extra
    one would drop something belonging to a family still in service."""
    dropped = set(
        re.findall(
            r"DROP\s+PROCEDURE\s+IF\s+EXISTS\s+(?:dbo\.)?\[?(\w+)\]?",
            _strip_sql_comments(_migration_text()),
            flags=re.IGNORECASE,
        )
    )
    assert dropped == EXPECTED_SPROCS, (
        f"missing={sorted(EXPECTED_SPROCS - dropped)} extra={sorted(dropped - EXPECTED_SPROCS)}"
    )


def test_the_migration_drops_exactly_one_table_and_it_is_the_staging_one():
    body = _strip_sql_comments(_migration_text())
    tables = re.findall(
        r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?\[?(\w+)\]?\.\[?(\w+)\]?", body, flags=re.IGNORECASE
    )
    assert [f"{schema}.{name}" for schema, name in tables] == [EXPECTED_TABLE]


def test_the_migration_drops_the_sprocs_before_the_table():
    """Sprocs first. SQL Server does not require it -- an unbound sproc body
    errors only at EXECUTE -- but dropping the table first leaves a window in
    which 7 live sprocs point at nothing and a straggler caller can still be
    admitted."""
    body = _strip_sql_comments(_migration_text())
    last_sproc = max(m.start() for m in re.finditer(r"DROP\s+PROCEDURE", body, re.IGNORECASE))
    first_table = min(m.start() for m in re.finditer(r"DROP\s+TABLE", body, re.IGNORECASE))
    assert last_sproc < first_table


def test_the_migration_never_drops_a_column():
    """`qbo.Customer.BillAddrId` / `ShipAddrId` and `qbo.Vendor.BillAddrId` point
    into the dropped table but live on two SURVIVING staging tables whose sprocs
    reference them in 46 places. Removing them is a separate later phase;
    leaving them NULL and unread is harmless. Dropping one here would break both
    of those families' CRUD at the next pull."""
    body = _strip_sql_comments(_migration_text()).upper()
    assert "DROP COLUMN" not in body
    for survivor in ("QBO.CUSTOMER", "QBO.VENDOR", "[QBO].[CUSTOMER]", "[QBO].[VENDOR]"):
        assert survivor not in body, f"the migration's executable SQL touches {survivor}"


def test_the_migration_carries_a_pre_drop_write_guard_that_aborts():
    """The premise of the whole drop is that nothing writes the table any more.
    If something does, the migration must REFUSE rather than destroy rows a live
    writer is still producing -- there is no un-drop, only a restore from backup.

    The assertions pin CONTROL FLOW, not just ingredients. A guard whose parts
    are all present but whose condition is short-circuited (`IF 1 = 0`, or an
    `AND 1 = 0` on the abort) reads identically to a live one under any
    substring check, and is exactly the edit a hurried operator makes to get
    past a firing guard. So each of the three links is matched as a shape:
    reachable -> reads the table with a bounded window -> aborts on what it
    read. All of it must sit before the first DROP."""
    body = _strip_sql_comments(_migration_text())
    guard_end = body.upper().index("DROP PROCEDURE")
    guard = body[:guard_end]

    # Link 1: the guard block is entered exactly when the table exists -- no
    # constant-false condition standing in for the existence check.
    assert re.search(
        r"IF\s+OBJECT_ID\s*\(\s*'qbo\.PhysicalAddress'\s*,\s*'U'\s*\)\s+IS\s+NOT\s+NULL"
        r"\s*\n\s*BEGIN\s*\n\s*DECLARE\s+@RecentWrites",
        guard,
        re.IGNORECASE,
    ), "the guard block is not entered on the table's existence"

    # Link 2: it reads THAT table, through dynamic SQL (see the re-runnability
    # test), counting rows inside a bounded recency window.
    assert re.search(
        r"EXEC\s+sp_executesql\s+N'SELECT\s+@out\s*=\s*COUNT\(\*\)"
        r"\s*\n?\s*FROM\s+\[qbo\]\.\[PhysicalAddress\]",
        guard,
        re.IGNORECASE,
    ), "the guard never actually reads the table it is guarding"
    assert re.search(r"DATEADD\s*\(\s*HOUR\s*,\s*-\s*\d+", guard, re.IGNORECASE), (
        "the guard has no bounded recency window"
    )
    assert "COALESCE([ModifiedDatetime], [CreatedDatetime])" in guard, (
        "a bare ModifiedDatetime comparison misses a freshly inserted row whose "
        "ModifiedDatetime is NULL -- the exact row the guard exists to catch"
    )

    # Link 3: it aborts on what it read, with nothing conjoined onto the test.
    assert re.search(
        r"IF\s+@RecentWrites\s*>\s*0\s*\n\s*THROW\s+\d+\s*,",
        guard,
        re.IGNORECASE,
    ), "the guard does not abort on the count it just took"


def test_the_guard_message_says_what_to_check():
    """A guard that fires at 2am with 'aborting' and nothing else costs an hour.
    It must name where to look: this migration's failure has four plausible
    causes and the message enumerates them."""
    body = _migration_text()
    throw = re.search(r"THROW\s+\d+\s*,\s*'(.*?)'\s*,\s*\d+\s*;", body, re.DOTALL)
    assert throw, "no THROW with a message literal"
    message = throw.group(1)
    assert len(message) > 200, "the abort message is too terse to act on"
    for expected in ("qbo.PhysicalAddress", "Nothing has been dropped"):
        assert expected in message, f"the abort message never mentions {expected!r}"


def test_the_migration_is_re_runnable():
    """Every destructive statement is existence-guarded, so a re-run after a
    partial failure is safe and a re-run after a clean one is a no-op.

    The guard reads the table through `sp_executesql` for the same reason: an
    ad-hoc T-SQL batch resolves object names at COMPILE time, so a bare
    `SELECT ... FROM qbo.PhysicalAddress` -- even inside `IF OBJECT_ID(...) IS
    NOT NULL` -- would raise Msg 208 on the re-run where the table is already
    gone, turning an idempotent no-op into an error."""
    body = _strip_sql_comments(_migration_text())

    for statement in re.finditer(r"DROP\s+PROCEDURE\s+(?:IF\s+EXISTS\s+)?", body, re.IGNORECASE):
        assert "IF EXISTS" in statement.group(0).upper(), "an unguarded DROP PROCEDURE"

    # The existence check must WRAP the drop. Matching `IF OBJECT_ID(...)`
    # anywhere in the file would be satisfied by the pre-drop guard's own copy
    # while the DROP TABLE sat bare underneath it.
    assert re.search(
        r"IF\s+OBJECT_ID\s*\(\s*'qbo\.PhysicalAddress'\s*,\s*'U'\s*\)\s+IS\s+NOT\s+NULL"
        r"\s*\n\s*BEGIN\s*\n\s*DROP\s+TABLE\s+\[qbo\]\.\[PhysicalAddress\]",
        body,
        re.IGNORECASE,
    ), "the table drop is not existence-guarded"

    assert re.search(r"EXEC\s+sp_executesql\s+N'", body, re.IGNORECASE), (
        "the guard reads the dropped table directly, so a re-run fails to compile"
    )


def test_the_migration_documents_that_there_is_no_rollback():
    """The data is gone on commit. Saying so in the file is what stops the next
    reader from assuming a down-migration exists somewhere."""
    header = _migration_text()[: _migration_text().upper().index("DROP PROCEDURE")]
    assert "ROLLBACK: THERE IS NONE" in header
    assert "backup" in header.lower()
