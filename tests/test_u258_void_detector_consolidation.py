"""White-box tests for U-258 void-detector consolidation in delete_reconcile.py."""
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.base.delete_reconcile import detect_void_absent_candidates
from integrations.intuit.qbo.base.errors import (
    QboAuthError,
    QboNotFoundError,
    QboRateLimitError,
)
from integrations.intuit.qbo.base.ids import normalize_qbo_id


def _run_detector(
    *,
    local_rows,
    live_ids=None,
    ids_raises=None,
    confirm_raises_by_id=None,
    confirm_default_raises=None,
    log_prefix="test.void_detector",
):
    live_ids = live_ids if live_ids is not None else []
    confirm_raises_by_id = confirm_raises_by_id or {}
    fetch_calls = []
    confirm_calls = []

    def fetch_live_ids():
        fetch_calls.append(True)
        if ids_raises:
            raise ids_raises
        return [normalize_qbo_id(i) for i in live_ids]

    def confirm_get(qbo_id):
        confirm_calls.append(qbo_id)
        if qbo_id in confirm_raises_by_id:
            raise confirm_raises_by_id[qbo_id]
        if confirm_default_raises:
            raise confirm_default_raises
        return SimpleNamespace(id=qbo_id)

    mappings = {row.id: SimpleNamespace(mapped_id=100 + row.id) for row in local_rows}

    result = detect_void_absent_candidates(
        local_rows=local_rows,
        realm_id="realm-test",
        reconcile_run_id="run-1",
        log_prefix=log_prefix,
        fetch_live_ids=fetch_live_ids,
        confirm_get=confirm_get,
        extract_qbo_id=lambda row: normalize_qbo_id(row.qbo_id),
        lookup_mapping=lambda row: mappings.get(row.id),
    )
    return result, fetch_calls, confirm_calls


def test_empty_candidates_skips_confirm_get():
    """All locals present in live set — confirm GET never runs."""
    local_a = SimpleNamespace(id=1, qbo_id="A")
    local_b = SimpleNamespace(id=2, qbo_id="B")

    result, fetch_calls, confirm_calls = _run_detector(
        local_rows=[local_a, local_b],
        live_ids=["A", "B"],
    )

    assert result.candidate_count == 0
    assert result.confirmed_voids == []
    assert result.aborted is False
    assert len(fetch_calls) == 1
    assert confirm_calls == []


def test_id_fetch_failure_aborts_without_confirm_get():
    local = SimpleNamespace(id=1, qbo_id="GONE")

    with pytest.raises(QboRateLimitError):
        _run_detector(
            local_rows=[local],
            ids_raises=QboRateLimitError("rate limited"),
        )


def test_ceiling_exceeded_aborts_without_confirm_get(monkeypatch):
    monkeypatch.setenv("QBO_RECONCILE_VOID_MAX_CANDIDATES", "2")
    locals_list = [
        SimpleNamespace(id=i, qbo_id=f"GONE-{i}")
        for i in range(1, 5)
    ]

    result, fetch_calls, confirm_calls = _run_detector(
        local_rows=locals_list,
        live_ids=[],
    )

    assert result.aborted is True
    assert result.abort_reason == "ceiling_exceeded"
    assert result.candidate_count == 4
    assert len(fetch_calls) == 1
    assert confirm_calls == []


def test_candidate_count_exactly_at_ceiling_does_not_abort(monkeypatch):
    """The ceiling check is strictly '>' — a candidate count EQUAL to the
    ceiling must confirm normally, not abort. Guards against an off-by-one
    ('>' vs '>=') regression on this P0-risk boundary (mass-delete-scare
    class of bug, U-212)."""
    monkeypatch.setenv("QBO_RECONCILE_VOID_MAX_CANDIDATES", "3")
    locals_list = [
        SimpleNamespace(id=i, qbo_id=f"GONE-{i}")
        for i in range(1, 4)
    ]

    result, fetch_calls, confirm_calls = _run_detector(
        local_rows=locals_list,
        live_ids=[],
        confirm_raises_by_id={
            f"GONE-{i}": QboNotFoundError("gone") for i in range(1, 4)
        },
    )

    assert result.aborted is False
    assert result.abort_reason is None
    assert result.candidate_count == 3
    assert len(fetch_calls) == 1
    assert confirm_calls == ["GONE-1", "GONE-2", "GONE-3"]
    assert len(result.confirmed_voids) == 3


def test_systemic_error_stops_before_next_candidate():
    locals_list = [
        SimpleNamespace(id=1, qbo_id="GONE-1"),
        SimpleNamespace(id=2, qbo_id="GONE-2"),
        SimpleNamespace(id=3, qbo_id="GONE-3"),
    ]

    result, _fetch_calls, confirm_calls = _run_detector(
        local_rows=locals_list,
        live_ids=[],
        confirm_raises_by_id={
            "GONE-1": QboNotFoundError("gone"),
            "GONE-2": QboAuthError("auth expired"),
        },
    )

    assert confirm_calls == ["GONE-1", "GONE-2"]
    assert len(result.confirmed_voids) == 1
    assert result.confirmed_voids[0].qbo_id == "GONE-1"
    assert result.aborted is True
    assert result.abort_reason == "systemic_confirm_abort"
    assert result.errors == 1


def test_confirmed_404_retains_row_mapping_and_qbo_id():
    local = SimpleNamespace(id=7, qbo_id="DELETED-7")

    result, _fetch_calls, confirm_calls = _run_detector(
        local_rows=[local],
        live_ids=[],
        confirm_raises_by_id={"DELETED-7": QboNotFoundError("gone")},
    )

    assert confirm_calls == ["DELETED-7"]
    assert len(result.confirmed_voids) == 1
    confirmed = result.confirmed_voids[0]
    assert confirmed.local_row is local
    assert confirmed.qbo_id == "DELETED-7"
    assert confirmed.mapping.mapped_id == 107


def test_false_positive_200_not_in_confirmed_list():
    local = SimpleNamespace(id=5, qbo_id="GHOST")

    result, _fetch_calls, confirm_calls = _run_detector(
        local_rows=[local],
        live_ids=[],
    )

    assert confirm_calls == ["GHOST"]
    assert result.confirmed_voids == []
    assert result.aborted is False
    assert result.errors == 0


def test_non_systemic_exception_continues_to_later_candidates():
    locals_list = [
        SimpleNamespace(id=1, qbo_id="GONE-1"),
        SimpleNamespace(id=2, qbo_id="GONE-2"),
        SimpleNamespace(id=3, qbo_id="GONE-3"),
    ]

    result, _fetch_calls, confirm_calls = _run_detector(
        local_rows=locals_list,
        live_ids=[],
        confirm_raises_by_id={
            "GONE-1": QboNotFoundError("gone"),
            "GONE-2": RuntimeError("transient"),
            "GONE-3": QboNotFoundError("gone"),
        },
    )

    assert confirm_calls == ["GONE-1", "GONE-2", "GONE-3"]
    assert [c.qbo_id for c in result.confirmed_voids] == ["GONE-1", "GONE-3"]
    assert result.aborted is False
    assert result.errors == 1


def test_unmapped_rows_are_not_candidates():
    local_mapped = SimpleNamespace(id=1, qbo_id="GONE-1")
    local_unmapped = SimpleNamespace(id=2, qbo_id="GONE-2")
    mappings = {1: SimpleNamespace(mapped_id=101)}

    fetch_calls = []
    confirm_calls = []

    def fetch_live_ids():
        fetch_calls.append(True)
        return []

    def confirm_get(qbo_id):
        confirm_calls.append(qbo_id)
        raise QboNotFoundError("gone")

    result = detect_void_absent_candidates(
        local_rows=[local_mapped, local_unmapped],
        realm_id="realm-test",
        reconcile_run_id="run-1",
        log_prefix="test.void_detector",
        fetch_live_ids=fetch_live_ids,
        confirm_get=confirm_get,
        extract_qbo_id=lambda row: normalize_qbo_id(row.qbo_id),
        lookup_mapping=lambda row: mappings.get(row.id),
    )

    assert result.candidate_count == 1
    assert confirm_calls == ["GONE-1"]
    assert len(result.confirmed_voids) == 1
