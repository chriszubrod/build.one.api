"""U-535l — mask taxpayer IDs in persisted agent history at repo write chokepoints."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from intelligence.api.router import ApprovalBody, approve_agent_action
from intelligence.api.replay import tail_session
from intelligence.persistence.approval_repo import (
    AgentApprovalRequest,
    AgentApprovalRequestRepo,
)
from intelligence.persistence.history import load_chain_history
from intelligence.persistence.redaction import mask_agent_text
from intelligence.persistence.session_repo import (
    AgentSession,
    AgentSessionRepo,
    AgentToolCall,
    AgentToolCallRepo,
    AgentTurn,
    AgentTurnRepo,
)
from intelligence.loop.events import ToolCallStart
from intelligence.messages.types import ToolUse
from scripts import remask_agent_history as remask_mod
from scripts.remask_agent_history import (
    _ColumnTarget,
    _TARGETS,
    _update_sql,
    main as remask_main,
    remask_column,
)

# Test-only taxpayer-id shapes (not real identifiers).
_SSN = "123-45-6789"
_SSN_MASKED = "***-**-6789"
_EIN = "12-3456789"
_EIN_MASKED = "**-***6789"
_BARE9 = 123456789
_BARE9_MASKED = "*********"

_UNMASKED_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")


def _assert_no_raw_ssn_in(s: str) -> None:
    assert not _UNMASKED_SSN.search(s)
    assert _SSN not in s


def _mock_session_row(**overrides):
    base = dict(
        Id=1,
        PublicId="aaaaaaaa-0000-0000-0000-000000000001",
        RowVersion=b"\x00\x01",
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-01 00:00:00",
        AgentName="test",
        AgentUserId=1,
        RequestingUserId=1,
        ParentSessionId=None,
        PreviousSessionId=None,
        Model="m",
        Provider="p",
        UserMessage="hi",
        SystemPrompt=None,
        Status="running",
        TerminationReason=None,
        TotalInputTokens=0,
        TotalOutputTokens=0,
        TotalCacheCreationInputTokens=0,
        TotalCacheReadInputTokens=0,
        TotalCostUsd=None,
        StartedAt="2026-01-01 00:00:00",
        CompletedAt=None,
        ErrorMessage=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_turn_row(**overrides):
    base = dict(
        Id=1,
        PublicId="bbbbbbbb-0000-0000-0000-000000000001",
        RowVersion=b"\x00\x01",
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-01 00:00:00",
        SessionId=1,
        TurnNumber=1,
        Model="m",
        InputTokens=0,
        OutputTokens=0,
        CacheCreationInputTokens=0,
        CacheReadInputTokens=0,
        CostUsd=None,
        StopReason=None,
        AssistantText=None,
        StartedAt="2026-01-01 00:00:00",
        CompletedAt=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_tool_call_row(**overrides):
    base = dict(
        Id=1,
        PublicId="cccccccc-0000-0000-0000-000000000001",
        RowVersion=b"\x00\x01",
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-01 00:00:00",
        TurnId=1,
        ToolUseId="toolu_1",
        ToolName="n",
        ToolInput="{}",
        ToolOutput=None,
        IsError=0,
        StartedAt="2026-01-01 00:00:00",
        CompletedAt=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_approval_row(**overrides):
    base = dict(
        Id=1,
        PublicId="dddddddd-0000-0000-0000-000000000001",
        RowVersion=b"\x00\x01",
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-01 00:00:00",
        SessionId=1,
        TurnId=1,
        RequestId="req_1",
        ToolName="n",
        Summary=None,
        ProposedInput="{}",
        Status="pending",
        FinalInput=None,
        DecidedByUserId=None,
        DecidedAt=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- mask_agent_text ------------------------------------------------------ #


def test_mask_agent_text_none_passthrough():
    assert mask_agent_text(None) is None


def test_mask_agent_text_plain_ssn():
    out = mask_agent_text(f"Vendor TIN {_SSN} please")
    assert out == f"Vendor TIN {_SSN_MASKED} please"


def test_mask_agent_text_invalid_json_fallback():
    out = mask_agent_text(f"not-json {_SSN}")
    assert out == f"not-json {_SSN_MASKED}"


def test_mask_agent_text_json_string_ssn_in_value():
    raw = json.dumps({"note": f"contact {_SSN}"})
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    assert parsed["note"] == f"contact {_SSN_MASKED}"
    _assert_no_raw_ssn_in(out)


def test_mask_agent_text_unicode_escaped_digits_in_json():
    # Arabic-Indic digits — stored as \\u066* escapes when ensure_ascii=True.
    arabic_ssn = "\u0661\u0662\u0663-\u0664\u0665-\u0666\u0667\u0668\u0669"
    raw = json.dumps({"note": f"SSN {arabic_ssn}"}, ensure_ascii=True)
    assert "\\u066" in raw
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    assert "*" in parsed["note"]
    assert arabic_ssn not in parsed["note"]


def test_mask_agent_text_nested_and_list():
    raw = json.dumps({"items": [{"memo": _EIN}], "tags": [_SSN]})
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    assert parsed["items"][0]["memo"] == _EIN_MASKED
    assert parsed["tags"][0] == _SSN_MASKED


@pytest.mark.parametrize(
    "payload, key_fragment",
    [
        ({"taxpayer_id_number": "123456789"}, "*********"),
        ({"TIN": _BARE9}, _BARE9_MASKED),
        ({"ssn": "123 45 6789"}, "spaces"),
    ],
)
def test_mask_agent_text_tin_keys(payload, key_fragment):
    raw = json.dumps(payload)
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    val = str(next(iter(parsed.values())))
    if key_fragment == "spaces":
        assert " " in val
        assert not any(c.isdigit() for c in val)
    else:
        assert key_fragment in val
    _assert_no_raw_ssn_in(out)


def test_mask_agent_text_tin_key_masks_nested_container_scalars():
    raw = json.dumps(
        {"tin": ["123456789", 123456789, {"value": "123456789"}]}
    )
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    assert parsed["tin"][0] == "*********"
    assert parsed["tin"][1] == "*********"
    assert parsed["tin"][2]["value"] == "*********"


def test_mask_agent_text_ssn_shaped_json_keys_survive():
    raw = json.dumps({"123-45-6789": "found"})
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    assert parsed[_SSN_MASKED] == "found"
    assert _SSN not in out


def test_mask_agent_text_colliding_masked_keys_get_suffixes():
    raw = json.dumps(
        {
            "123-45-6789": "first",
            "223-45-6789": "second",
        }
    )
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    assert parsed[_SSN_MASKED] == "first"
    assert f"{_SSN_MASKED}#b" in parsed
    assert parsed[f"{_SSN_MASKED}#b"] == "second"


def test_mask_agent_text_taxpayer_id_fk_integer_unchanged():
    raw = json.dumps({"taxpayer_id": 42, "memo": "ok"})
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    assert parsed["taxpayer_id"] == 42


def test_mask_agent_text_non_tin_fields_untouched():
    raw = json.dumps(
        {
            "amount": 1500.25,
            "project_id": 42,
            "invoice_date": "2025-10-13",
            "phone": "615-555-1234",
            "memo": "OK",
        }
    )
    out = mask_agent_text(raw)
    parsed = json.loads(out)
    assert parsed["amount"] == 1500.25
    assert parsed["project_id"] == 42
    assert parsed["invoice_date"] == "2025-10-13"
    assert parsed["phone"] == "615-555-1234"


def test_mask_agent_text_output_is_json_loadsable_when_input_was_json():
    raw = json.dumps({"a": 1, "b": _SSN})
    out = mask_agent_text(raw)
    assert isinstance(out, str)
    json.loads(out)


_MASK_AGENT_TEXT_GOLDEN: tuple[tuple[str | None, str | None], ...] = (
    (None, None),
    ("Vendor TIN 123-45-6789 please", "Vendor TIN ***-**-6789 please"),
    ("not-json 123-45-6789", "not-json ***-**-6789"),
    ('{"note": "contact 123-45-6789"}', '{"note": "contact ***-**-6789"}'),
    (
        '{"note": "SSN \\u0661\\u0662\\u0663-\\u0664\\u0665-\\u0666\\u0667\\u0668\\u0669"}',
        '{"note": "SSN ***-**-\\u0666\\u0667\\u0668\\u0669"}',
    ),
    (
        '{"items": [{"memo": "12-3456789"}], "tags": ["123-45-6789"]}',
        '{"items": [{"memo": "**-***6789"}], "tags": ["***-**-6789"]}',
    ),
    ('{"taxpayer_id_number": "123456789"}', '{"taxpayer_id_number": "*********"}'),
    ('{"TIN": 123456789}', '{"TIN": "*********"}'),
    ('{"ssn": "123 45 6789"}', '{"ssn": "*** ** ****"}'),
    (
        '{"tin": ["123456789", 123456789, {"value": "123456789"}]}',
        '{"tin": ["*********", "*********", {"value": "*********"}]}',
    ),
    ('{"123-45-6789": "found"}', '{"***-**-6789": "found"}'),
    (
        '{"123-45-6789": "first", "223-45-6789": "second"}',
        '{"***-**-6789": "first", "***-**-6789#b": "second"}',
    ),
    ('{"taxpayer_id": 42, "memo": "ok"}', '{"taxpayer_id": 42, "memo": "ok"}'),
    (
        '{"amount": 1500.25, "project_id": 42, "invoice_date": "2025-10-13", "phone": "615-555-1234", "memo": "OK"}',
        '{"amount": 1500.25, "project_id": 42, "invoice_date": "2025-10-13", "phone": "615-555-1234", "memo": "OK"}',
    ),
    ('{"a": 1, "b": "123-45-6789"}', '{"a": 1, "b": "***-**-6789"}'),
    ('{"ssn": "123-45-6789", "x": 1}', '{"ssn": "***-**-****", "x": 1}'),
    ('{"memo": "12-3456789", "n": 1}', '{"memo": "**-***6789", "n": 1}'),
    ('{"ssn": "123-45-6789"}', '{"ssn": "***-**-****"}'),
    ('{"tin": {"123456789": "owner"}}', '{"tin": {"*********": "owner"}}'),
    ('{"lookup": {"123456789": "row"}}', '{"lookup": {"123456789": "row"}}'),
    (
        '{"123-45-6789": 1, "987-65-6789": 2, "***-**-6789#2": 3}',
        '{"***-**-6789": 1, "***-**-6789#b": 2, "***-**-6789#2": 3}',
    ),
    (
        '{"tin": {"last4": "6789", "value1": "one", "value2": "two", "type": "SSN"}}',
        '{"tin": {"last4": "****", "value1": "one", "value2": "two", "type": "SSN"}}',
    ),
    (
        '{"tin": {"123456789": "a", "12-3456789": "b"}}',
        '{"tin": {"*********": "a", "**-*******": "b"}}',
    ),
    (
        '{"tin": {"123456789": "a", "223456789": "b", "323456789": "c"}}',
        '{"tin": {"*********": "a", "*********#b": "b", "*********#c": "c"}}',
    ),
    (
        '{"123-45-6789": 1, "223-45-6789": 2, "***-**-6789#b": 3}',
        '{"***-**-6789": 1, "***-**-6789#c": 2, "***-**-6789#b": 3}',
    ),
    (
        '{"tin": ["123456789", {"value": 123456789}], "note": "SSN 123-45-6789"}',
        '{"tin": ["*********", {"value": "*********"}], "note": "SSN ***-**-6789"}',
    ),
    ("", ""),
    ("short", "short"),
    ("123456789", "123456789"),
    ("12-3456789", "**-***6789"),
    ("[]", "[]"),
    ("{}", "{}"),
    (
        '{"nested": {"deep": {"ssn": "123-45-6789"}}}',
        '{"nested": {"deep": {"ssn": "***-**-****"}}}',
    ),
    (
        '{"ein": "12-3456789", "child": {"fein": "98-7654321"}}',
        '{"ein": "**-*******", "child": {"fein": "**-*******"}}',
    ),
    ('["123-45-6789", "12-3456789"]', '["***-**-6789", "**-***6789"]'),
    ('{"tin": true}', '{"tin": true}'),
    ('{"tin": null}', '{"tin": null}'),
    ('{"tin": 123456789}', '{"tin": "*********"}'),
    ('{"tin": "123456789"}', '{"tin": "*********"}'),
    ('{"memo": "no tin here"}', '{"memo": "no tin here"}'),
    ('{"bill_number": "12-3456789"}', '{"bill_number": "**-***6789"}'),
    ("{broken json 123-45-6789", "{broken json ***-**-6789"),
    (
        '{"unicode": "digits \\uff11\\uff12\\uff13-\\uff14\\uff15-\\uff16\\uff17\\uff18\\uff19"}',
        '{"unicode": "digits ***-**-\\uff16\\uff17\\uff18\\uff19"}',
    ),
    (
        '{"123-45-6789": "a", "223-45-6789": "b", "323-45-6789": "c"}',
        '{"***-**-6789": "a", "***-**-6789#b": "b", "***-**-6789#c": "c"}',
    ),
    (
        '{"tin": {"123456789": "x", "223456789": "y"}}',
        '{"tin": {"*********": "x", "*********#b": "y"}}',
    ),
    (
        '{"social_security_number": "123-45-6789"}',
        '{"social_security_number": "***-**-****"}',
    ),
    (
        '{"employer_identification_number": "12-3456789"}',
        '{"employer_identification_number": "**-*******"}',
    ),
    (
        '{"taxpayer_identification_number": "123456789"}',
        '{"taxpayer_identification_number": "*********"}',
    ),
    ('{"ITIN": "912-34-5678"}', '{"ITIN": "***-**-****"}'),
    ('{"note": "phone 615-555-1234 only"}', '{"note": "phone 615-555-1234 only"}'),
    ('{"value": 0}', '{"value": 0}'),
    ('{"value": false}', '{"value": false}'),
    ('{"tin": {"value1": "123456789"}}', '{"tin": {"value1": "*********"}}'),
    ('{"tags": ["123-45-6789"]}', '{"tags": ["***-**-6789"]}'),
    ('{"tin": {"tags": ["123456789"]}}', '{"tin": {"tags": ["*********"]}}'),
    (
        '"plain string json value with 123-45-6789"',
        '"plain string json value with ***-**-6789"',
    ),
    ('{"a\\\\u0000b": "x"}', '{"a\\\\u0000b": "x"}'),
)


@pytest.mark.parametrize("raw, expected", _MASK_AGENT_TEXT_GOLDEN)
def test_mask_agent_text_byte_identity_golden(raw, expected):
    assert mask_agent_text(raw) == expected


# --- repo write masking --------------------------------------------------- #


@patch("intelligence.persistence.session_repo.call_procedure")
@patch("intelligence.persistence.session_repo.get_connection")
def test_session_create_masks_user_message(mock_conn, mock_proc):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_session_row()
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    AgentSessionRepo().create(
        agent_name="a",
        model="m",
        provider="p",
        user_message=f"Process W-9 {_SSN}",
    )
    params = mock_proc.call_args.kwargs["params"]
    assert _SSN not in params["UserMessage"]
    assert _SSN_MASKED in params["UserMessage"]


@patch("intelligence.persistence.session_repo.call_procedure")
@patch("intelligence.persistence.session_repo.get_connection")
def test_session_fail_masks_error_message(mock_conn, mock_proc):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_session_row(Status="failed", ErrorMessage="x")
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    AgentSessionRepo().fail(id=1, error_message=f"bad tin {_SSN}")
    params = mock_proc.call_args.kwargs["params"]
    assert _SSN_MASKED in params["ErrorMessage"]


@patch("intelligence.persistence.session_repo.call_procedure")
@patch("intelligence.persistence.session_repo.get_connection")
def test_turn_complete_masks_assistant_text(mock_conn, mock_proc):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_turn_row()
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    AgentTurnRepo().complete(
        id=1,
        input_tokens=1,
        output_tokens=1,
        assistant_text=f"Found EIN {_EIN}",
    )
    params = mock_proc.call_args.kwargs["params"]
    assert _EIN_MASKED in params["AssistantText"]


@patch("intelligence.persistence.session_repo.call_procedure")
@patch("intelligence.persistence.session_repo.get_connection")
def test_tool_call_create_masks_tool_input(mock_conn, mock_proc):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_tool_call_row()
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    payload = json.dumps({"ssn": _SSN, "qty": 2})
    AgentToolCallRepo().create(
        turn_id=1,
        tool_use_id="t",
        tool_name="n",
        tool_input=payload,
    )
    params = mock_proc.call_args.kwargs["params"]
    stored = json.loads(params["ToolInput"])
    assert stored["ssn"] != _SSN
    assert stored["qty"] == 2


@patch("intelligence.persistence.session_repo.call_procedure")
@patch("intelligence.persistence.session_repo.get_connection")
def test_tool_call_complete_masks_tool_output(mock_conn, mock_proc):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_tool_call_row(ToolOutput="x")
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    AgentToolCallRepo().complete(
        id=1,
        tool_output=f"result {_SSN}",
        is_error=False,
    )
    params = mock_proc.call_args.kwargs["params"]
    assert _SSN_MASKED in params["ToolOutput"]


@patch("intelligence.persistence.approval_repo.call_procedure")
@patch("intelligence.persistence.approval_repo.get_connection")
def test_approval_create_masks_summary_only_not_proposed_input(mock_conn, mock_proc):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_approval_row()
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    proposed = json.dumps({"bill_number": _EIN, "qty": 1})
    AgentApprovalRequestRepo().create(
        session_id=1,
        request_id="r",
        tool_name="n",
        proposed_input=proposed,
        summary=f"Approve {_SSN}",
    )
    params = mock_proc.call_args.kwargs["params"]
    assert params["ProposedInput"] == proposed
    assert _EIN in params["ProposedInput"]
    assert _SSN_MASKED in params["Summary"]


@patch("intelligence.persistence.approval_repo.call_procedure")
@patch("intelligence.persistence.approval_repo.get_connection")
def test_approval_set_decision_preserves_final_input(mock_conn, mock_proc):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_approval_row(Status="approved")
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    final_payload = json.dumps({"bill_number": _EIN})
    AgentApprovalRequestRepo().set_decision(
        id=1,
        status="approved",
        final_input=final_payload,
    )
    params = mock_proc.call_args.kwargs["params"]
    assert params["FinalInput"] == final_payload


def test_replay_tail_parses_masked_tool_input():
    async def _run():
        masked_in = mask_agent_text(json.dumps({"ssn": _SSN, "x": 1}))
        session = AgentSession(
            id=1,
            public_id="replay-pub",
            status="completed",
            termination_reason="end_turn",
        )
        turn = AgentTurn(
            id=2,
            session_id=1,
            turn_number=1,
            stop_reason="end_turn",
        )
        tool_call = AgentToolCall(
            id=3,
            turn_id=2,
            tool_use_id="toolu_replay",
            tool_name="demo_tool",
            tool_input=masked_in,
            completed_at="2026-01-01 00:00:01",
        )

        with (
            patch("intelligence.api.replay.AgentSessionRepo") as s_repo,
            patch("intelligence.api.replay.AgentTurnRepo") as t_repo,
            patch("intelligence.api.replay.AgentToolCallRepo") as tc_repo,
            patch("intelligence.api.replay.AgentApprovalRequestRepo") as a_repo,
        ):
            s_repo.return_value.read_by_public_id.return_value = session
            t_repo.return_value.read_by_session_id.return_value = [turn]
            tc_repo.return_value.read_by_turn_id.return_value = [tool_call]
            a_repo.return_value.read_by_session_id.return_value = []

            events = [ev async for ev in tail_session("replay-pub")]

            starts = [ev for ev in events if isinstance(ev, ToolCallStart)]
            assert len(starts) == 1
            assert starts[0].input["x"] == 1
            assert starts[0].input["ssn"] != _SSN

    asyncio.run(_run())


def test_history_load_chain_parses_masked_tool_input():
    async def _run():
        masked_in = mask_agent_text(json.dumps({"memo": _EIN, "n": 1}))
        session = AgentSession(
            id=5,
            user_message="hello",
            previous_session_id=None,
        )
        turn = AgentTurn(id=6, session_id=5)
        tool_call = AgentToolCall(
            id=7,
            turn_id=6,
            tool_use_id="toolu_hist",
            tool_name="demo_tool",
            tool_input=masked_in,
        )

        with (
            patch(
                "intelligence.persistence.history.AgentSessionRepo"
            ) as s_repo,
            patch("intelligence.persistence.history.AgentTurnRepo") as t_repo,
            patch(
                "intelligence.persistence.history.AgentToolCallRepo"
            ) as tc_repo,
        ):
            s_repo.return_value.read_by_id.return_value = session
            t_repo.return_value.read_by_session_id.return_value = [turn]
            tc_repo.return_value.read_by_turn_id.return_value = [tool_call]

            messages = await load_chain_history(5)

            tool_uses = [
                block
                for msg in messages
                if msg.role == "assistant"
                for block in msg.content
                if isinstance(block, ToolUse)
            ]
            assert len(tool_uses) == 1
            assert tool_uses[0].input["n"] == 1
            assert tool_uses[0].input["memo"] == _EIN_MASKED

    asyncio.run(_run())


# --- remask script -------------------------------------------------------- #


def test_update_sql_uses_rowversion_optimistic_predicate():
    tables = {t.table for t in _TARGETS}
    for table in tables:
        sql = _update_sql(table, "ToolInput")
        assert "ModifiedDatetime = SYSUTCDATETIME()" in sql
        assert "WHERE Id = ? AND RowVersion = ?" in sql
        assert "VARBINARY" not in sql


class _Row:
    def __init__(self, id_: int, col: str, row_version: bytes = b"\x00\x01"):
        self.Id = id_
        self.RowVersion = row_version
        self._col = col

    def __getitem__(self, idx: int):
        if idx == 2:
            return self._col
        raise IndexError(idx)


class _FakeCursor:
    def __init__(self, batches: list[list[tuple]], rowcounts: list[int] | None = None):
        self._batches = batches
        self._batch_idx = 0
        self._rows: list[_Row] = []
        self.executed: list[tuple] = []
        self.rowcount = 0
        self._rowcounts = rowcounts or []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT TOP" in sql:
            if self._batch_idx < len(self._batches):
                batch = self._batches[self._batch_idx]
                self._rows = [
                    _Row(row_id, col, rv) for row_id, rv, col in batch
                ]
                self._batch_idx += 1
            else:
                self._rows = []
        elif sql.strip().upper().startswith("UPDATE"):
            self.rowcount = self._rowcounts.pop(0) if self._rowcounts else 1

    def fetchall(self):
        return list(self._rows)


def _fake_conn_factory(cursor: _FakeCursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = None
    return lambda: conn


def test_remask_dry_run_writes_nothing(capsys):
    raw = json.dumps({"ssn": _SSN})
    masked_once = mask_agent_text(raw)
    cur = _FakeCursor(batches=[[(1, b"\xab\xcd", raw)]])

    target = _ColumnTarget("dbo.AgentToolCall", "ToolInput", "tool_call")
    stats = remask_column(
        target=target,
        apply=False,
        limit=None,
        connection_factory=_fake_conn_factory(cur),
    )
    assert stats.would_change == 1
    assert stats.changed == 0
    assert not any("UPDATE" in (e[0] or "") for e in cur.executed if isinstance(e[0], str))
    captured = capsys.readouterr()
    assert _SSN not in captured.out and _SSN not in captured.err
    assert masked_once != raw


def test_remask_apply_updates_with_rowversion_predicate():
    raw = json.dumps({"ssn": _SSN})
    rv = b"\x01\x02"
    cur = _FakeCursor(batches=[[(5, rv, raw)]], rowcounts=[1])

    target = _ColumnTarget("dbo.AgentToolCall", "ToolInput", "tool_call")
    stats = remask_column(
        target=target,
        apply=True,
        limit=None,
        connection_factory=_fake_conn_factory(cur),
    )
    assert stats.changed == 1
    update_calls = [e for e in cur.executed if e[0] and "UPDATE" in e[0]]
    assert len(update_calls) == 1
    sql, params = update_calls[0]
    assert "RowVersion = ?" in sql
    assert params[1] == 5
    assert params[2] == rv
    assert json.loads(params[0])["ssn"] != _SSN


def test_remask_update_failures_count_toward_abort(capsys):
    raw = json.dumps({"ssn": _SSN})
    cur = _FakeCursor(
        batches=[
            [
                (1, b"\x01", raw),
                (2, b"\x02", raw),
                (3, b"\x03", raw),
                (4, b"\x04", raw),
                (5, b"\x05", raw),
                (6, b"\x06", raw),
            ],
        ],
    )
    base_execute = cur.execute

    def execute(sql, params=None):
        if sql.strip().upper().startswith("UPDATE"):
            raise RuntimeError("secret db message")
        return base_execute(sql, params)

    cur.execute = execute  # type: ignore[method-assign]

    target = _ColumnTarget("dbo.AgentToolCall", "ToolInput", "tool_call")
    stats = remask_column(
        target=target,
        apply=True,
        limit=None,
        connection_factory=_fake_conn_factory(cur),
    )
    assert stats.failed == 5
    assert stats.aborted is True
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.out
    assert "secret" not in captured.out


def test_remask_read_error_sanitized_and_main_nonzero(capsys, monkeypatch):
    def _boom_factory():
        raise ConnectionError("connection string leaked")

    stats = remask_column(
        target=_ColumnTarget("dbo.AgentToolCall", "ToolInput", "tool_call"),
        apply=False,
        limit=1,
        connection_factory=_boom_factory,
    )
    assert stats.failed == 1
    captured = capsys.readouterr()
    assert "ConnectionError" in captured.out
    assert "connection string" not in captured.out

    monkeypatch.setattr(
        remask_mod,
        "remask_agent_history",
        lambda **kw: {("dbo.AgentToolCall", "ToolInput"): stats},
    )
    assert remask_main([]) == 1


def test_remask_main_dry_run_exit_zero(capsys, monkeypatch):
    monkeypatch.setattr(remask_mod, "remask_agent_history", lambda **kw: {})
    assert remask_main([]) == 0
    assert "DRY RUN" in capsys.readouterr().out


# --- remask targets & JSON key collisions --------------------------------- #


def test_backfill_never_targets_executable_approval_inputs():
    approval_cols = {t.column for t in _TARGETS if t.table == "dbo.AgentApprovalRequest"}
    assert approval_cols == {"Summary"}


def test_bare_tin_key_inside_tin_container_is_masked():
    out = json.loads(mask_agent_text(json.dumps({"tin": {"123456789": "owner"}})))
    (key,) = out["tin"].keys()
    assert not any(c.isdigit() for c in key)
    assert out["tin"][key] == "owner"


def test_bare_nine_digit_key_outside_tin_context_is_untouched():
    out = json.loads(mask_agent_text(json.dumps({"lookup": {"123456789": "row"}})))
    assert out == {"lookup": {"123456789": "row"}}


@pytest.mark.parametrize(
    "members",
    [
        [("123-45-6789", 1), ("987-65-6789", 2), ("***-**-6789#b", 3)],
        [("***-**-6789#b", 3), ("123-45-6789", 1), ("987-65-6789", 2)],
        [
            ("123-45-6789", 1),
            ("***-**-6789#b", 3),
            ("987-65-6789", 2),
            ("555-55-6789", 4),
        ],
    ],
)
def test_colliding_masked_keys_never_drop_members(members):
    raw = json.dumps(dict(members))
    out = json.loads(mask_agent_text(raw))
    assert len(out) == len(members)
    assert sorted(out.values()) == sorted(v for _, v in members)
    assert not _UNMASKED_SSN.search(json.dumps(out))


# --- idempotency & ordinary key names under TIN containers ---------------- #


def test_ordinary_field_names_under_tin_container_are_not_mangled():
    out = json.loads(mask_agent_text(json.dumps({"tin": {"last4": "6789", "value1": "one", "value2": "two", "type": "SSN"}})))
    assert set(out["tin"]) == {"last4", "value1", "value2", "type"}
    assert out["tin"]["value1"] == "one" and out["tin"]["type"] == "SSN"
    assert not any(c.isdigit() for c in out["tin"]["last4"])  # scalar under a TIN key: masked


def test_id_like_keys_under_tin_container_are_masked():
    out = json.loads(mask_agent_text(json.dumps({"tin": {"123456789": "a", "12-3456789": "b"}})))
    assert all(sum(c.isdigit() for c in k) == 0 for k in out["tin"])
    assert len(out["tin"]) == 2 and sorted(out["tin"].values()) == ["a", "b"]


@pytest.mark.parametrize(
    "payload",
    [
        {"tin": {"123456789": "a", "223456789": "b", "323456789": "c"}},
        {"123-45-6789": 1, "223-45-6789": 2, "***-**-6789#b": 3},
        {"tin": ["123456789", {"value": 123456789}], "note": "SSN 123-45-6789"},
    ],
)
def test_masking_is_idempotent(payload):
    once = mask_agent_text(json.dumps(payload))
    twice = mask_agent_text(once)
    assert twice == once


# --- unclassifiable Unicode digits (fail-closed under TIN keys) ------------- #


@pytest.mark.parametrize(
    "key",
    [
        "123456" + "".join(chr(0x1CCF0 + d) for d in (7, 8, 9)),  # Unicode 16 outlined digits
        "".join(chr(0x11F50 + d) for d in (1, 2, 3, 4, 5, 6, 7)),  # Kawi digits (Unicode 15)
        "123-45-" + "".join(chr(0x16D70 + d) for d in (6, 7, 8, 9)),  # Kirat Rai (Unicode 16)
    ],
)
def test_unclassifiable_digit_keys_under_tin_container_fail_closed(key):
    out = json.loads(mask_agent_text(json.dumps({"tin": {key: "owner"}}, ensure_ascii=False)))
    (masked_key,) = out["tin"].keys()
    assert not any(c.isnumeric() for c in masked_key)
    assert not any(0x11F50 <= ord(c) <= 0x11F59 or 0x16D70 <= ord(c) <= 0x16D79 or 0x1CCF0 <= ord(c) <= 0x1CCF9 for c in masked_key)
    assert out["tin"][masked_key] == "owner"


# --- free text is never re-serialized; deep nesting fails closed ------------ #

from intelligence.persistence.redaction import mask_agent_free_text  # noqa: E402


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1e5", "1e5"),
        ('  {"note": "SSN 123-45-6789", "n": 1e5}  ', '  {"note": "SSN ***-**-6789", "n": 1e5}  '),
        ('"café 123-45-6789"', '"café ***-**-6789"'),
        ("[1, 2, 3]", "[1, 2, 3]"),
        (None, None),
    ],
)
def test_free_text_masked_without_json_reserialization(text, expected):
    assert mask_agent_free_text(text) == expected


@pytest.mark.parametrize("column", ["UserMessage", "ErrorMessage", "AssistantText"])
def test_session_free_text_columns_use_free_text_masker(column):
    import intelligence.persistence.session_repo as sr

    assert f'"{column}": mask_agent_free_text(' in Path(sr.__file__).read_text(encoding="utf-8")


def test_tool_columns_keep_json_masker_and_summary_is_free_text():
    import intelligence.persistence.approval_repo as ar
    import intelligence.persistence.session_repo as sr

    src = Path(sr.__file__).read_text(encoding="utf-8")
    assert '"ToolInput": mask_agent_text(' in src and '"ToolOutput": mask_agent_text(' in src
    assert '"Summary": mask_agent_free_text(' in Path(ar.__file__).read_text(encoding="utf-8")


def test_backfill_routes_only_tool_payloads_through_json_masker():
    flags = {(t.table, t.column): t.json_payload for t in remask_mod._TARGETS}
    assert {k for k, v in flags.items() if v} == {("dbo.AgentToolCall", "ToolInput"), ("dbo.AgentToolCall", "ToolOutput")}


@pytest.mark.parametrize("depth", [5000, 50000])
def test_deeply_nested_tool_payload_fails_closed(depth):
    payload = "[" * depth + '"SSN 123-45-6789"' + "]" * depth
    out = mask_agent_text(payload)
    assert len(out) == len(payload)
    assert not any(c.isdigit() for c in out)


@patch("intelligence.persistence.session_repo.call_procedure")
@patch("intelligence.persistence.session_repo.get_connection")
def test_turn_complete_does_not_reformat_json_looking_assistant_text(mock_conn, mock_proc):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_turn_row()
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    text = '  {"total": 1e5, "note": "café", "ssn": "123-45-6789"}  '
    AgentTurnRepo().complete(id=1, input_tokens=1, output_tokens=1, assistant_text=text)
    stored = mock_proc.call_args.kwargs["params"]["AssistantText"]
    assert stored == '  {"total": 1e5, "note": "café", "ssn": "***-**-6789"}  '


# --- hostile tool payloads: int-digit limit, deep nesting through the readers ---- #

_HUGE_INT_PAYLOAD = '{"ssn": "123-45-6789", "n": ' + "9" * 5000 + "}"
_DEEP_PAYLOAD = '{"x": ' + "[" * 5000 + '"SSN 123-45-6789"' + "]" * 5000 + "}"


@pytest.mark.parametrize("payload", [_HUGE_INT_PAYLOAD, _DEEP_PAYLOAD])
def test_hostile_tool_payload_fails_closed(payload):
    out = mask_agent_text(payload)
    assert len(out) == len(payload)
    assert not any(c.isdigit() for c in out)


@patch("intelligence.persistence.session_repo.call_procedure")
@patch("intelligence.persistence.session_repo.get_connection")
def test_tool_call_complete_persists_huge_int_output(mock_conn, mock_proc):
    cursor = MagicMock()
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
    try:
        AgentToolCallRepo().complete(id=1, tool_output=_HUGE_INT_PAYLOAD, is_error=False)
    except Exception:
        pass  # row mapping of the mocked cursor is not under test
    assert mock_proc.called
    assert "123-45-6789" not in mock_proc.call_args.kwargs["params"]["ToolOutput"]


@pytest.mark.parametrize("payload", [_HUGE_INT_PAYLOAD, _DEEP_PAYLOAD])
def test_readers_tolerate_hostile_tool_input(payload):
    stored = mask_agent_text(payload)

    async def _run():
        session = AgentSession(id=1, public_id="pub", status="completed", termination_reason="end_turn",
                               user_message="hi", previous_session_id=None)
        turn = AgentTurn(id=2, session_id=1, turn_number=1, stop_reason="end_turn")
        call = AgentToolCall(id=3, turn_id=2, tool_use_id="toolu_x", tool_name="demo_tool",
                             tool_input=stored, completed_at="2026-01-01 00:00:01")
        with (
            patch("intelligence.api.replay.AgentSessionRepo") as s_repo,
            patch("intelligence.api.replay.AgentTurnRepo") as t_repo,
            patch("intelligence.api.replay.AgentToolCallRepo") as tc_repo,
            patch("intelligence.api.replay.AgentApprovalRequestRepo") as a_repo,
        ):
            s_repo.return_value.read_by_public_id.return_value = session
            t_repo.return_value.read_by_session_id.return_value = [turn]
            tc_repo.return_value.read_by_turn_id.return_value = [call]
            a_repo.return_value.read_by_session_id.return_value = []
            starts = [ev async for ev in tail_session("pub") if isinstance(ev, ToolCallStart)]
            assert len(starts) == 1
        with (
            patch("intelligence.persistence.history.AgentSessionRepo") as s_repo,
            patch("intelligence.persistence.history.AgentTurnRepo") as t_repo,
            patch("intelligence.persistence.history.AgentToolCallRepo") as tc_repo,
        ):
            s_repo.return_value.read_by_id.return_value = session
            t_repo.return_value.read_by_session_id.return_value = [turn]
            tc_repo.return_value.read_by_turn_id.return_value = [call]
            messages = await load_chain_history(1)
            assert any(isinstance(b, ToolUse) for m in messages if m.role == "assistant" for b in m.content)

    asyncio.run(_run())
