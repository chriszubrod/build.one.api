"""U-456 — the Box deep-link helpers U-446c deleted while leaving their callers.

`e2150fe8` (U-446c, deployed 2026-09-13) removed three module-level functions
from `entities/bill_line_item/business/service.py`. Two of them were still
called:

    out[line_item_id] = {
        "box_folder_url": _build_box_folder_url(folder_id) if folder_id else None,
        "box_workbook_url": _build_box_file_url(file_id) if file_id else None,
    }

so `get_box_links_by_bill_id` raised `NameError` — and the ONE route that calls
it, `GET /api/v1/get/bill_line_items/bill/{bill_id}`, 500'd unguarded. Every
bill's line items vanished from both the edit and the view page: the header
loaded fine, the lines never arrived. Live for ~a day before a human noticed.

Nothing caught it because nothing executed that method. The sproc and access
guards were tested; the six lines that format a URL were not, and a NameError
only fires at call time.

The deletion was collateral from a cascade refactor — the third function
removed alongside them, `_clear_legacy_bill_line_item_bill_line_mapping`, was
genuinely dead. Two of three being dead is exactly the shape that makes this
easy to get wrong, and exactly why the test below is generic rather than naming
these two.
"""

import ast
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_box_link_helpers_exist_and_build_the_documented_urls():
    from entities.bill_line_item.business.service import (
        _build_box_file_url,
        _build_box_folder_url,
    )

    assert _build_box_folder_url("123") == "https://app.box.com/folder/123"
    assert _build_box_file_url("456") == "https://app.box.com/file/456"


def test_get_box_links_by_bill_id_actually_runs():
    """The regression test proper: EXECUTE the method.

    A NameError is invisible to every structural check — the call site parses,
    imports cleanly, and type-checks. Only running it finds the missing
    definition, which is why this drives the real method with a stubbed repo
    rather than asserting on source text.
    """
    from unittest.mock import MagicMock, patch

    from entities.bill_line_item.business.service import BillLineItemService

    svc = BillLineItemService.__new__(BillLineItemService)
    svc.repo = MagicMock()
    svc.repo.read_box_links_by_bill_id.return_value = {
        7: {"box_invoices_folder_id": "f1", "box_workbook_file_id": "w1"},
        8: {"box_invoices_folder_id": None, "box_workbook_file_id": None},
    }

    with patch("entities.bill_line_item.business.service.assert_can_access_bill"):
        out = svc.get_box_links_by_bill_id(bill_id=55)

    assert out[7] == {
        "box_folder_url": "https://app.box.com/folder/f1",
        "box_workbook_url": "https://app.box.com/file/w1",
    }
    assert out[8] == {"box_folder_url": None, "box_workbook_url": None}, (
        "a line item with no Box mapping yields nulls, not a crash"
    )


def test_no_module_level_helper_is_called_without_being_defined():
    """The generic guard for the whole class.

    Deleting a helper and leaving one caller behind is not a Box-links problem;
    it is what happens whenever a refactor removes 'dead' code. This walks the
    first-party tree and fails on any call to an underscore-prefixed name that
    the module neither defines nor imports.
    """
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith((".venv/", "tests/")) or "__pycache__" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        bound: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.ImportFrom):
                bound.update(a.asname or a.name for a in node.names)
            elif isinstance(node, ast.Import):
                bound.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.Assign):
                bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, (ast.arg,)):
                bound.add(node.arg)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id.startswith("_")
                and not node.func.id.startswith("__")
                and node.func.id not in bound
            ):
                offenders.append(f"{rel}:{node.lineno} calls {node.func.id}()")

    assert offenders == [], (
        "these call a module-level helper that is neither defined nor imported "
        "— a NameError waiting for the first request that reaches it:\n  "
        + "\n  ".join(offenders)
    )
