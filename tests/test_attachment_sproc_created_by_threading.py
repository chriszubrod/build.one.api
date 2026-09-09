"""Gap-2 `@CreatedByUserId` threading on the line-item attachment Create sprocs.

Found 2026-09-08 while fixing `CreateBillLineItemAttachment` (pinned separately
in `test_bill_attachment_sproc_and_list_scoping.py`, which also covers that
entity's list-path scoping). The Expense and Invoice siblings carried the
identical latent bug and are fixed and pinned here.

The bug shape, for all three:

    The good body — with `@CreatedByUserId BIGINT = NULL` — lived only in
    `scripts/migrations/gap2_adjacent_threading.sql`, while the entity's own
    base file carried a STALE 2-param duplicate. Base files use
    `CREATE OR ALTER` and get re-run routinely
    (`./.venv/bin/python scripts/run_sql.py <base file>`), so whichever ran last won.
    Applying a base file reverted the threading, and every
    `*LineItemAttachmentRepository.create` sends `CreatedByUserId`, so the next
    call failed in the driver with a "too many arguments" error.

Blast radius differs by entity, which is why the Expense one matters most:

    Bill      `BillService.create` rolls the Bill back when the attachment link
              fails → every Bill create carrying a PDF fails.
    Expense   `ExpenseService.create` does the same (and deletes the placeholder
              line item first) → every Expense create carrying a receipt fails.
    Invoice   Only reached via POST /api/v1/create/invoice-line-item-attachment.
              No parent rollback → a broken endpoint, not a lost parent row.

Live schema note: `gap2_created_by_user_id.sql` added the column to all three
tables and `gap2_created_by_user_id_finalize.sql` backfilled 17, applied
`DEFAULT (17)`, then tightened each to `NOT NULL` — so the idempotent
column-add these base files now carry (`BIGINT NOT NULL` + `DEFAULT (17)`)
matches prod exactly and is a no-op against it.

Duplication itself is separately ratcheted by `test_sproc_single_source.py` via
`sproc_drift_ledger.py`, from which both entries were removed.
"""

import pytest

from tests.sproc_text import (
    REPO_ROOT,
    defines_sproc,
    split_top_level,
    sproc_body,
    sproc_params,
)
import re

GAP2_MIGRATION = REPO_ROOT / "scripts/migrations/gap2_adjacent_threading.sql"

# (sproc, base file, table, parent-id param)
CASES = [
    (
        "CreateExpenseLineItemAttachment",
        REPO_ROOT / "entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql",
        "ExpenseLineItemAttachment",
        "@ExpenseLineItemId",
    ),
    (
        "CreateInvoiceLineItemAttachment",
        REPO_ROOT / "entities/invoice_line_item_attachment/sql/dbo.invoice_line_item_attachment.sql",
        "InvoiceLineItemAttachment",
        "@InvoiceLineItemId",
    ),
]

IDS = [c[0] for c in CASES]


@pytest.mark.parametrize("sproc, base_file, table, parent_param", CASES, ids=IDS)
def test_base_file_declares_created_by_user_id(sproc, base_file, table, parent_param):
    """The canonical copy must DECLARE the param the repo layer always sends.

    Asserted against the parameter list, not the body — see tests/sproc_text.py.
    """
    params = sproc_params(base_file, sproc)
    assert parent_param in params, f"{sproc} lost its parent id param"
    assert re.search(r"@CreatedByUserId\s+BIGINT\s*=\s*NULL", params), (
        f"{base_file.name}'s {sproc} no longer DECLARES @CreatedByUserId. "
        "Re-running that base file would revert the Gap-2 threading and break "
        f"every {table}Repository.create. The `= NULL` default is part of the "
        "contract: it keeps older callers binding."
    )


@pytest.mark.parametrize("sproc, base_file, table, parent_param", CASES, ids=IDS)
def test_base_file_binds_the_system_context_fallback(sproc, base_file, table, parent_param):
    """User 17 must survive: scheduler / outbox callers pass no user id."""
    body = sproc_body(base_file, sproc)
    assert "COALESCE(@CreatedByUserId, 17)" in body


@pytest.mark.parametrize("sproc, base_file, table, parent_param", CASES, ids=IDS)
def test_insert_list_and_values_stay_in_arity(sproc, base_file, table, parent_param):
    """A param nobody INSERTs is worse than no param — pin both sides."""
    body = sproc_body(base_file, sproc)
    insert_cols = re.search(rf"INSERT INTO dbo\.\[{table}\] \((.*?)\)", body, re.DOTALL)
    values = re.search(r"VALUES \((.*)\)\s*;", body, re.DOTALL)
    assert insert_cols and values, f"could not parse {sproc}'s INSERT"
    columns = split_top_level(insert_cols.group(1))
    bound = split_top_level(values.group(1))
    assert "[CreatedByUserId]" in columns
    assert columns.index("[CreatedByUserId]") == bound.index("COALESCE(@CreatedByUserId, 17)"), (
        "CreatedByUserId must be bound in the same position it is declared"
    )
    assert len(columns) == len(bound)


@pytest.mark.parametrize("sproc, base_file, table, parent_param", CASES, ids=IDS)
def test_base_file_can_build_from_scratch(sproc, base_file, table, parent_param):
    """The idempotent column-add must precede the sproc that references it.

    Without it a from-scratch build creates the table with no CreatedByUserId
    column and the INSERT fails with SQL 207 at CREATE PROCEDURE time.
    """
    text = base_file.read_text()
    add = f"ALTER TABLE [dbo].[{table}] ADD [CreatedByUserId] BIGINT NOT NULL"
    assert add in text, f"{base_file.name} has no idempotent CreatedByUserId column-add"
    assert f"DF_{table}_CreatedByUserId" in text, "must carry the DEFAULT (17) live shape"
    assert text.index(add) < text.index(f"CREATE OR ALTER PROCEDURE {sproc}"), (
        "the column-add must run before the sproc that INSERTs into it"
    )


@pytest.mark.parametrize("sproc, base_file, table, parent_param", CASES, ids=IDS)
def test_migration_no_longer_carries_a_competing_body(sproc, base_file, table, parent_param):
    """gap2_adjacent_threading.sql must be a pointer stub for each sproc."""
    assert not defines_sproc(GAP2_MIGRATION, sproc), (
        f"A second definition of {sproc} is back in the migration. Whichever "
        "file ran last would win; the entity base file is the canonical home."
    )
    assert str(base_file.relative_to(REPO_ROOT)) in GAP2_MIGRATION.read_text(), (
        "the stub must still point a reader at the canonical home"
    )


@pytest.mark.parametrize("sproc, base_file, table, parent_param", CASES, ids=IDS)
def test_sproc_is_absent_from_the_drift_ledger(sproc, base_file, table, parent_param):
    """Single-sourced sprocs must be removed from the frozen debt baseline.

    `sproc_drift_ledger.py`'s docstring: entries may ONLY be deleted (when a dup
    is single-sourced) or shrunk. Leaving a retired entry would let a future
    re-duplication pass the ratchet silently.
    """
    from tests.sproc_drift_ledger import SPROC_DRIFT_LEDGER

    assert sproc not in SPROC_DRIFT_LEDGER


@pytest.mark.parametrize(
    "repo_module, repo_name",
    [
        (
            "entities.expense_line_item_attachment.persistence.repo",
            "ExpenseLineItemAttachmentRepository",
        ),
        (
            "entities.invoice_line_item_attachment.persistence.repo",
            "InvoiceLineItemAttachmentRepository",
        ),
    ],
)
def test_repo_still_sends_the_param_the_sproc_declares(repo_module, repo_name):
    """The half of the contract that lives in Python.

    If a future change drops `CreatedByUserId` from the repo call instead of
    from the sproc, the sproc pins above stay green while attribution silently
    reverts to the User-17 default for every row.
    """
    import inspect

    module = __import__(repo_module, fromlist=[repo_name])
    source = inspect.getsource(getattr(module, repo_name).create)
    assert '"CreatedByUserId": created_by_user_id' in source
