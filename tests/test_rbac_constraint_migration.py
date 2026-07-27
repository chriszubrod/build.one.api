"""U-053 guard: RBAC join-table integrity migration shape is safe to apply.

The migration at entities/role_module/sql/migrations/001_rbac_join_integrity_constraints.sql
creates four constraints (three on dbo.RoleModule, one on dbo.Module) that were
declared in base files but never reached prod due to the U-048 batch-trap. This
test statically asserts the migration is idempotent, data-guarded before each DDL,
self-verifying after apply, and free of sproc redefinitions — without touching a DB.

Batch splitting and the CREATE PROCEDURE pattern are reused from
tests.test_sync_sql_batch_boundaries rather than re-implemented here.
"""

import re
from functools import lru_cache
from pathlib import Path

from tests.test_sync_sql_batch_boundaries import _PROCEDURE_PATTERN, _split_sql_batches

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (
    REPO_ROOT
    / "entities"
    / "role_module"
    / "sql"
    / "migrations"
    / "001_rbac_join_integrity_constraints.sql"
)

_EXPECTED_CONSTRAINTS = (
    "FK_RoleModule_Role",
    "FK_RoleModule_Module",
    "UQ_RoleModule_RoleId_ModuleId",
    "UQ_Module_Name",
)

_LINE_COMMENT = re.compile(r"--[^\n]*")
_ADD_CONSTRAINT_PATTERN = re.compile(
    r"ADD\s+CONSTRAINT\s+\[(\w+)\]",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _migration_text() -> str:
    return MIGRATION_SQL.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _migration_batches() -> tuple[str, ...]:
    return tuple(_split_sql_batches(_migration_text()))


@lru_cache(maxsize=1)
def _verification_batch() -> str:
    """The migration's single post-apply verification batch.

    The banner comment is the whole selector: it appears exactly once, and the
    verification section runs from there to the file's final GO, so it is one
    batch. If the banner is ever reworded this fails loudly with the assertion
    below rather than silently checking the wrong batch.
    """
    batches = [batch for batch in _migration_batches() if "POST-APPLY VERIFICATION" in batch]
    assert batches, "Expected a post-apply verification batch"
    return batches[-1]


def _strip_line_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", text)


def _constraint_names_in_add_statements(batch: str) -> list[str]:
    return _ADD_CONSTRAINT_PATTERN.findall(_strip_line_comments(batch))


def _batch_has_if_not_exists_guard(batch: str, constraint_name: str) -> bool:
    executable = _strip_line_comments(batch)
    return (
        "IF NOT EXISTS" in executable.upper()
        and f"'{constraint_name}'" in executable
    )


def _first_add_constraint_index(batch: str) -> int | None:
    executable = _strip_line_comments(batch)
    match = _ADD_CONSTRAINT_PATTERN.search(executable)
    return match.start() if match else None


def _first_severity_16_raiserror_index(batch: str) -> int | None:
    executable = _strip_line_comments(batch)
    for match in re.finditer(r"RAISERROR\s*\(", executable, re.IGNORECASE):
        tail = executable[match.start() : match.start() + 2000]
        if re.search(r",\s*16\s*,", tail):
            return match.start()
    return None


def test_migration_file_exists():
    assert MIGRATION_SQL.is_file(), f"Missing migration: {MIGRATION_SQL.relative_to(REPO_ROOT)}"


def test_run_line_points_at_this_migration():
    rel_path = MIGRATION_SQL.relative_to(REPO_ROOT).as_posix()
    content = _migration_text()
    assert (
        f"scripts/run_sql.py {rel_path}" in content
        or f"run_sql.py {rel_path}" in content
    ), f"RUN line must reference {rel_path}"


def test_no_create_procedure_in_migration():
    executable = _strip_line_comments(_migration_text())
    assert _PROCEDURE_PATTERN.search(executable) is None, (
        "Migration must not redefine sprocs — found CREATE PROCEDURE statement(s)"
    )


def test_all_four_constraints_added_exactly_once():
    all_adds: list[str] = []
    for batch in _migration_batches():
        all_adds.extend(_constraint_names_in_add_statements(batch))

    for name in _EXPECTED_CONSTRAINTS:
        count = all_adds.count(name)
        assert count == 1, (
            f"Expected exactly one ADD CONSTRAINT for {name!r}, found {count}"
        )


def test_every_add_constraint_is_idempotent_and_data_guarded():
    violations: list[str] = []

    for index, batch in enumerate(_migration_batches(), start=1):
        add_names = _constraint_names_in_add_statements(batch)
        if not add_names:
            continue

        for name in add_names:
            if not _batch_has_if_not_exists_guard(batch, name):
                violations.append(
                    f"batch {index}: ADD CONSTRAINT [{name}] lacks IF NOT EXISTS guard"
                )

        alter_index = _first_add_constraint_index(batch)
        guard_index = _first_severity_16_raiserror_index(batch)
        if alter_index is not None and (
            guard_index is None or guard_index > alter_index
        ):
            violations.append(
                f"batch {index}: severity-16 RAISERROR must precede ADD CONSTRAINT"
            )

    assert violations == [], "Unguarded or unguarded-before-DDL constraints:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_post_apply_verification_batch():
    batch = _verification_batch()
    assert "ALTER TABLE" not in _strip_line_comments(batch).upper(), (
        "Post-apply verification batch must not contain ALTER TABLE"
    )
    for name in _EXPECTED_CONSTRAINTS:
        assert name in batch, f"Verification batch must reference {name!r}"
    assert _first_severity_16_raiserror_index(batch) is not None, (
        "Verification batch must contain RAISERROR at severity 16"
    )


def test_unique_index_verification_rejects_non_equivalent_indexes():
    """Reject a same-named filtered/disabled/IGNORE_DUP_KEY index passing verification.

    Without is_disabled, has_filter and ignore_dup_key predicates, a pre-existing
    index named UQ_RoleModule_RoleId_ModuleId or UQ_Module_Name that is unique in
    name only could satisfy verification while not enforcing global uniqueness.
    """
    executable = _strip_line_comments(_verification_batch())

    index_names = (
        "UQ_RoleModule_RoleId_ModuleId",
        "UQ_Module_Name",
    )
    required_predicates = (
        "is_unique = 1",
        "is_disabled = 0",
        "has_filter = 0",
        "ignore_dup_key = 0",
    )

    for index_name in index_names:
        name_pos = executable.index(index_name)
        select_pos = executable.rfind("SELECT", 0, name_pos)
        slice_end = executable.find(");", name_pos)
        slice_text = executable[select_pos:slice_end]

        for predicate in required_predicates:
            assert predicate in slice_text, (
                f"Verification SELECT for {index_name!r} must constrain {predicate!r}"
            )


def test_fk_verification_requires_no_action_referential_integrity():
    """Reject a same-named ON DELETE CASCADE FK passing post-apply verification.

    Without delete_referential_action, update_referential_action, and
    is_not_for_replication predicates, a same-named FK_RoleModule_Role created
    ON DELETE CASCADE would verify as correct while silently destroying RBAC
    permission grants on role deletion instead of failing with SQL 547.
    """
    executable = _strip_line_comments(_verification_batch())

    declare_pos = executable.index("DECLARE @TrustedFks")
    select_pos = executable.index("SELECT", declare_pos)
    slice_end = executable.find(");", select_pos)
    trusted_fks_slice = executable[select_pos:slice_end]

    required_predicates = (
        "is_disabled = 0",
        "is_not_trusted = 0",
        "delete_referential_action = 0",
        "update_referential_action = 0",
        "is_not_for_replication = 0",
    )
    for predicate in required_predicates:
        assert predicate in trusted_fks_slice, (
            f"@TrustedFks verification SELECT must constrain {predicate!r}"
        )

    assert "@FkColumnPairs" in executable, (
        "Verification batch must count foreign-key column pairs via @FkColumnPairs"
    )
    assert "sys.foreign_key_columns" in executable, (
        "Verification batch must join sys.foreign_key_columns for column-pair count"
    )
