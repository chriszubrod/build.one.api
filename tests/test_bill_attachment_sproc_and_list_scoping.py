"""Two Bill-entity fixes from the 2026-09-08 review, pinned against regression.

FIX 1 — `CreateBillLineItemAttachment` single-sourced.
    The entity base file carried a STALE 2-param duplicate (no
    `@CreatedByUserId`) of the sproc whose good body lived in
    `scripts/migrations/gap2_adjacent_threading.sql`. Base files use
    `CREATE OR ALTER` and are re-run routinely, so applying the base file
    reverted the Gap-2 threading; `BillLineItemAttachmentRepository.create`
    always sends `CreatedByUserId`, so the next call failed in the driver,
    and `BillService.create` rolls the whole Bill back when the attachment
    link fails — i.e. a base re-run broke EVERY Bill create carrying a PDF
    (the universal-PDF rule: web UI, agent, bill-folder). Exactly the incident
    `dbo.bill.sql` already fixed for `CreateBill` on 2026-07-12.

FIX 2 — the two unscoped `read_all` list paths.
    `BillLineItemService.read_all` and `BillLineItemAttachmentService.read_all`
    reached sprocs with no `UserCanAccessBill` predicate and passed no actor,
    so `GET /api/v1/get/bill_line_items` and
    `GET /api/v1/get/bill-line-item-attachments` returned every row in the
    database — amounts, descriptions, projects — to any caller holding module
    read. Every other read on both services was already gated.

These are file-content + call-contract pins, not DB tests: the v1 harness is
pure-logic / no-live-DB. Duplication itself is separately ratcheted by
`tests/test_sproc_single_source.py` via `sproc_drift_ledger.py`.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.authz import current_is_system_admin, current_user_id
from tests.sproc_text import REPO_ROOT, split_top_level as _split_top_level
from tests.sproc_text import sproc_body as _sproc_body
from tests.sproc_text import sproc_params as _sproc_params

BLIA_BASE = REPO_ROOT / "entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql"
BLI_BASE = REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql"
GAP2_MIGRATION = REPO_ROOT / "scripts/migrations/gap2_adjacent_threading.sql"


# ---------------------------------------------------------------------------
# FIX 1 — CreateBillLineItemAttachment
# ---------------------------------------------------------------------------


def test_base_file_create_sproc_threads_created_by_user_id():
    """The canonical copy must accept the param the repo layer always sends."""
    params = _sproc_params(BLIA_BASE, "CreateBillLineItemAttachment")
    assert re.search(r"@CreatedByUserId\s+BIGINT\s*=\s*NULL", params), (
        "The base file's CreateBillLineItemAttachment no longer DECLARES "
        "@CreatedByUserId. Re-running this file would revert the Gap-2 threading "
        "and break every BillLineItemAttachmentRepository.create — and with it "
        "every Bill create carrying a PDF. The `= NULL` default is part of the "
        "contract: it keeps older callers binding."
    )
    body = _sproc_body(BLIA_BASE, "CreateBillLineItemAttachment")
    assert "COALESCE(@CreatedByUserId, 17)" in body, (
        "The system-context fallback (User 17) must survive: scheduler / outbox "
        "callers pass no user id."
    )


def test_base_file_insert_list_and_values_stay_in_arity():
    """A param nobody INSERTs is worse than no param — pin both sides."""
    body = _sproc_body(BLIA_BASE, "CreateBillLineItemAttachment")
    insert_cols = re.search(
        r"INSERT INTO dbo\.\[BillLineItemAttachment\] \((.*?)\)", body, re.DOTALL
    )
    values = re.search(r"VALUES \((.*)\)\s*;", body, re.DOTALL)
    assert insert_cols and values
    columns = _split_top_level(insert_cols.group(1))
    bound = _split_top_level(values.group(1))
    assert "[CreatedByUserId]" in columns
    assert columns.index("[CreatedByUserId]") == bound.index("COALESCE(@CreatedByUserId, 17)"), (
        "CreatedByUserId must be bound in the same position it is declared"
    )
    assert len(columns) == len(bound)


def test_base_file_can_build_from_scratch():
    """The U-345 idempotent column-add must precede the sproc that references it.

    Without it a from-scratch build creates the table with no CreatedByUserId
    column and the INSERT above fails with SQL 207.
    """
    text = BLIA_BASE.read_text()
    assert "ADD [CreatedByUserId] BIGINT NOT NULL" in text
    assert text.index("ADD [CreatedByUserId]") < text.index(
        "CREATE OR ALTER PROCEDURE CreateBillLineItemAttachment"
    ), "the column-add must run before the sproc that INSERTs into it"


def test_migration_no_longer_carries_a_competing_body():
    """gap2_adjacent_threading.sql must be a pointer stub for this sproc."""
    text = GAP2_MIGRATION.read_text()
    assert not re.search(
        r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?CreateBillLineItemAttachment\b",
        text,
        re.IGNORECASE,
    ), (
        "A second definition is back in the migration. Whichever file ran last "
        "would win; the base file is the canonical home."
    )
    assert "entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql" in text, (
        "the stub must still point a reader at the canonical home"
    )


# ---------------------------------------------------------------------------
# FIX 2 — list-path scoping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql_path, sproc, alias_col",
    [
        (BLI_BASE, "ReadBillLineItems", "bli.[BillId]"),
        (BLIA_BASE, "ReadBillLineItemAttachments", "bli.[BillId]"),
    ],
)
def test_list_sprocs_filter_on_user_can_access_bill(sql_path, sproc, alias_col):
    params = _sproc_params(sql_path, sproc)
    assert "@ActorUserId" in params and "@ActorIsSystemAdmin" in params, (
        f"{sproc} must DECLARE the actor params, not merely reference them"
    )
    body = _sproc_body(sql_path, sproc)
    assert f"dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, {alias_col}) = 1" in body, (
        f"{sproc} lost its UserProject predicate — the list path leaks every row"
    )


@pytest.mark.parametrize(
    "service_module, service_name, repo_attr",
    [
        ("entities.bill_line_item.business.service", "BillLineItemService", "repo"),
        (
            "entities.bill_line_item_attachment.business.service",
            "BillLineItemAttachmentService",
            "repo",
        ),
    ],
)
def test_read_all_forwards_the_live_actor_context(service_module, service_name, repo_attr):
    """The service must pass the request's ContextVars, not defaults.

    Passing nothing is not merely unscoped — with the sproc's `= NULL`
    defaults it would be indistinguishable from an anonymous caller, and the
    pre-fix behaviour returned the whole table.
    """
    module = __import__(service_module, fromlist=[service_name])
    service_cls = getattr(module, service_name)

    uid_token = current_user_id.set(42)
    admin_token = current_is_system_admin.set(False)
    try:
        repo = MagicMock()
        repo.read_all.return_value = []
        service = service_cls(repo=repo)
        service.read_all()
    finally:
        current_user_id.reset(uid_token)
        current_is_system_admin.reset(admin_token)

    repo.read_all.assert_called_once_with(actor_user_id=42, actor_is_system_admin=False)


@pytest.mark.parametrize(
    "repo_module, repo_name",
    [
        ("entities.bill_line_item.persistence.repo", "BillLineItemRepository"),
        (
            "entities.bill_line_item_attachment.persistence.repo",
            "BillLineItemAttachmentRepository",
        ),
    ],
)
def test_repo_read_all_binds_the_actor_as_sql_bit(repo_module, repo_name):
    """`ActorIsSystemAdmin` must reach pyodbc as 0/1, never a Python bool."""
    module = __import__(repo_module, fromlist=[repo_name])
    repo = getattr(module, repo_name)()

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch.object(module, "get_connection") as get_conn, patch.object(
        module, "call_procedure"
    ) as call_proc:
        get_conn.return_value.__enter__.return_value = conn
        repo.read_all(actor_user_id=7, actor_is_system_admin=True)

    params = call_proc.call_args.kwargs["params"]
    assert params["ActorUserId"] == 7
    assert params["ActorIsSystemAdmin"] == 1
    assert not isinstance(params["ActorIsSystemAdmin"], bool)


def test_bit_helper_preserves_the_unset_actor():
    """None must stay None — collapsing it to 0 would change the SQL branch."""
    from entities.bill_line_item.persistence.repo import _bit

    assert _bit(None) is None
    assert _bit(True) == 1
    assert _bit(False) == 0
