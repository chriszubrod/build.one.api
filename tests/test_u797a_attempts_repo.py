"""U-797a fix round 2: repository attempt accounting with fake connection and procedure (no DB)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

from entities.contract_labor.persistence import coding_suggestion_repo as repo_mod


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _Cursor(self._row)


def _patch(monkeypatch, row=None):
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_connection():
        yield _Conn(row)

    def fake_call(cursor, name, params):
        calls.append((name, dict(params)))
        return cursor

    monkeypatch.setattr(repo_mod, "get_connection", fake_connection)
    monkeypatch.setattr(repo_mod, "call_procedure", fake_call)
    return calls


def _claim_row(attempt_count):
    return SimpleNamespace(
        Id=7,
        ContractLaborId=1,
        ProjectId=2,
        WorkDate=date(2026, 11, 10),
        VendorId=None,
        AttemptCount=attempt_count,
    )


def test_claim_next_carries_attempt_count_from_the_sproc_row(monkeypatch):
    _patch(monkeypatch, row=_claim_row(attempt_count=2))
    claim = repo_mod.ContractLaborCodingSuggestionRepository().claim_next()
    assert claim is not None
    assert claim.attempt_count == 2


def test_release_without_an_attempt_sends_count_attempt_zero(monkeypatch):
    calls = _patch(monkeypatch, row=SimpleNamespace(Id=7, Status="pending"))
    repo_mod.ContractLaborCodingSuggestionRepository().release(7)
    name, params = calls[-1]
    assert name == "ReleaseContractLaborCodingSuggestion"
    assert params == {"Id": 7, "CountAttempt": 0}


def test_release_with_an_attempt_sends_count_attempt_one(monkeypatch):
    calls = _patch(monkeypatch, row=SimpleNamespace(Id=7, Status="pending"))
    repo_mod.ContractLaborCodingSuggestionRepository().release(7, count_attempt=True)
    name, params = calls[-1]
    assert name == "ReleaseContractLaborCodingSuggestion"
    assert params == {"Id": 7, "CountAttempt": 1}
