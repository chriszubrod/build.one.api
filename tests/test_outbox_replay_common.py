"""Pure-logic tests for scripts/outbox_replay_common.py and replay script wrappers."""

from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.outbox_replay_common import (
    build_replay_parser,
    fetch_dead_letter_rows,
    preview_dead_letters,
    print_dead_letter_preview,
    validate_replay_limit,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(script_name: str):
    path = _REPO_ROOT / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(script_name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_rows(count: int = 1, *, kind: str = "sync_bill_to_qbo"):
    return [
        (
            10 + i,
            f"pid-{10 + i}",
            kind,
            "Bill",
            f"ent-{10 + i}",
            5,
            "2026-08-01",
            "err",
        )
        for i in range(count)
    ]


def test_validate_replay_limit_rejects_out_of_range():
    assert validate_replay_limit(0) is True
    assert validate_replay_limit(3000, max_limit=2098) is True
    assert validate_replay_limit(100) is False
    assert validate_replay_limit(2098, max_limit=2098) is False


def test_fetch_dead_letter_rows_builds_kind_filter():
    cursor = MagicMock()
    cursor.fetchall.return_value = _fake_rows()

    rows = fetch_dead_letter_rows(
        cursor,
        schema_table="qbo.Outbox",
        limit=50,
        kinds=["sync_bill_to_qbo"],
    )

    assert len(rows) == 1
    sql = cursor.execute.call_args.args[0]
    params = cursor.execute.call_args.args[1:]
    assert "FROM qbo.Outbox" in sql
    assert "Kind IN (?)" in sql
    assert params == ("sync_bill_to_qbo",)


def test_print_dead_letter_preview_qbo_blind_reset_warning(capsys):
    rows = _fake_rows()
    print_dead_letter_preview(rows, [], qbo_blind_reset_warning=True)
    out = capsys.readouterr().out
    assert "(ALL kinds)" in out
    assert "WARNING: No --kind filter" in out


def test_preview_dead_letters_dry_run_returns_none(capsys):
    cursor = MagicMock()
    cursor.fetchall.return_value = _fake_rows()

    rows, exit_code = preview_dead_letters(
        cursor,
        schema_table="ms.Outbox",
        limit=10,
        kinds=[],
        apply=False,
    )

    assert rows is None
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "DRY-RUN: no rows modified" in out


def test_build_replay_parser_includes_limit_help_with_max():
    parser = build_replay_parser("desc", max_limit=2098)
    limit_action = next(a for a in parser._actions if a.dest == "limit")
    assert "max 2098" in limit_action.help


@pytest.mark.parametrize(
    "script_name,schema_table,apply_message",
    [
        (
            "retry_qbo_outbox_dead_letters.py",
            "qbo.Outbox",
            "RequestId, LastError, and DeadLetteredAt preserved",
        ),
        (
            "retry_ms_outbox_dead_letters.py",
            "ms.Outbox",
            "Worker will pick them up within ~5s",
        ),
        (
            "retry_box_outbox_dead_letters.py",
            "box.Outbox",
            "Worker will pick them up on the next drain tick",
        ),
    ],
)
def test_replay_scripts_dry_run_commit_nothing(script_name, schema_table, apply_message):
    module = _load_script_module(script_name)
    cursor = MagicMock()
    cursor.fetchall.return_value = _fake_rows(
        kind="sync_bill_to_qbo" if schema_table == "qbo.Outbox" else "upload_sharepoint_file"
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_get_connection():
        yield conn

    argv = [script_name]
    if schema_table == "qbo.Outbox":
        argv.extend(["--kind", "sync_bill_to_qbo"])

    with patch.object(module, "get_connection", fake_get_connection), patch(
        "sys.argv", argv
    ):
        assert module.main() == 0

    conn.commit.assert_not_called()
    update_calls = [
        c for c in cursor.execute.call_args_list if "UPDATE" in c.args[0]
    ]
    assert update_calls == []


def test_ms_replay_script_apply_nulls_last_error_fields():
    module = _load_script_module("retry_ms_outbox_dead_letters.py")
    cursor = MagicMock()
    cursor.fetchall.return_value = _fake_rows(kind="upload_sharepoint_file")
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_get_connection():
        yield conn

    with patch.object(module, "get_connection", fake_get_connection), patch(
        "sys.argv",
        ["retry_ms_outbox_dead_letters.py", "--apply"],
    ):
        assert module.main() == 0

    update_call = next(
        c for c in cursor.execute.call_args_list if "UPDATE ms.Outbox" in c.args[0]
    )
    assert "LastError = NULL" in update_call.args[0]
    assert "DeadLetteredAt = NULL" in update_call.args[0]
    assert "AND Status = 'dead_letter'" not in update_call.args[0]
    conn.commit.assert_called_once()
