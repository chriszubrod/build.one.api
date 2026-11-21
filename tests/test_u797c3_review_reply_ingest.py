"""U-797c3: reviewer-reply ingestion service. Fakes only: no DB, no Graph, no .env reads."""
from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from entities.email_message.business.review_reply_ingest_service import ReviewReplyIngestService

LOCK_NAME = "review_reply_ingest"
from entities.email_message.business.service import (
    REVIEWER_REPLY_STATUS,
    ReviewReplyCopyError,
)

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
MAILBOX = "invoices@example.com"
OPEN_A = "AAQkADOpenThreadA"
OPEN_B = "AAQkADOpenThreadB"
OPEN_C = "AAQkADOpenThreadC"
OPEN_D = "AAQkADOpenThreadD"
OPEN_E = "AAQkADOpenThreadE"


def msg(conversation_id: str, message_id: str) -> dict:
    return {"message_id": message_id, "conversation_id": conversation_id}


class FakeRepo:
    def __init__(self, conversations=(), raises=None):
        self.conversations = list(conversations)
        self.raises = raises
        self.calls: list[int] = []

    def read_open_reply_conversations(self, *, since_days):
        self.calls.append(since_days)
        if self.raises is not None:
            raise self.raises
        return list(self.conversations)


class FakeMail:
    """Inbox listing. Calls filtered on a conversation id are served from
    by_conversation; other calls are served from pages, then default_page."""

    def __init__(self, pages=(), default_page=None, status=200, raises=None,
                 by_conversation=None, fail_conversations=(), raise_conversations=()):
        self.pages = [list(p) for p in pages]
        self.default_page = default_page
        self.status = status
        self.raises = raises
        self.by_conversation = by_conversation or {}
        self.fail_conversations = set(fail_conversations)
        self.raise_conversations = set(raise_conversations)
        self.calls: list[dict] = []

    def list_messages(self, **kwargs):
        self.calls.append(kwargs)
        marker = "conversationId eq '"
        fq = kwargs.get("filter_query") or ""
        if marker in fq:
            cid = fq.split(marker, 1)[1][:-1].replace("''", "'")
            if cid in self.raise_conversations:
                raise RuntimeError("socket reset")
            if cid in self.fail_conversations:
                return {"status_code": 503, "message": "graph down",
                        "messages": [], "total_count": 0}
            return {"status_code": 200, "message": "ok",
                    "messages": list(self.by_conversation.get(cid, [])),
                    "total_count": 0, "has_more": False}
        if self.raises is not None:
            raise self.raises
        if self.status != 200:
            return {"status_code": self.status, "message": "graph down",
                    "messages": [], "total_count": 0}
        if self.pages:
            page = self.pages.pop(0)
        elif self.default_page is not None:
            page = list(self.default_page)
        else:
            page = []
        return {"status_code": 200, "message": "ok", "messages": page,
                "total_count": 0, "has_more": False}


class FakePoll:
    """Insert path. new_count=None reports len(messages) inserted per call."""

    def __init__(self, new_count=None, errors=()):
        self.new_count = new_count
        self.errors = list(errors)
        self.ingested: list[list[dict]] = []
        self.ingest_kwargs: list[dict] = []

    def _ingest_messages(self, *, messages, mailbox, folder="inbox",
                         default_status=None, download_attachments=True):
        self.ingest_kwargs.append({
            "mailbox": mailbox, "folder": folder,
            "default_status": default_status,
            "download_attachments": download_attachments,
        })
        self.ingested.append(list(messages))
        new = len(messages) if self.new_count is None else self.new_count
        return new, 0, list(self.errors)

    def copy_review_thread_replies(self, conversation_ids):
        raise AssertionError("catch_up must not call copy_review_thread_replies")


class FakeLock:
    """Stands in for ms_app_lock: records names, yields the acquired flag."""

    def __init__(self, acquired=True):
        self.acquired = acquired
        self.names: list[str] = []

    def __call__(self, name, timeout_ms=15000):
        self.names.append(name)
        return nullcontext(self.acquired)


class StepClock:
    """Each call advances by `step`; the first call returns 0."""

    def __init__(self, step):
        self.step = step
        self.t = -step

    def __call__(self):
        self.t += self.step
        return self.t


def make(*, repo=None, poll=None, mail=None, enabled=True, mailbox=MAILBOX,
         lock=None, clock=None, now=None):
    settings = SimpleNamespace(review_reply_ingest_enabled=enabled,
                               invoice_inbox_email=mailbox)
    return ReviewReplyIngestService(
        repo=repo if repo is not None else FakeRepo([OPEN_A]),
        poll_service=poll if poll is not None else FakePoll(),
        mail=mail if mail is not None else FakeMail(),
        settings_factory=lambda: settings,
        lock=lock if lock is not None else FakeLock(True),
        clock=clock if clock is not None else (lambda: 0.0),
        now=now if now is not None else (lambda: NOW),
    )


@pytest.fixture(autouse=True)
def _unpaused(monkeypatch):
    monkeypatch.delenv("PAUSE_EMAIL_POLL", raising=False)


# ---------------------------------------------------------------- gates


@pytest.mark.parametrize("method", ["ingest_recent", "catch_up"])
def test_flag_off_skips_without_db_or_graph(method):
    repo, mail, poll, lock = FakeRepo([OPEN_A]), FakeMail([[msg(OPEN_A, "m1")]]), FakePoll(), FakeLock()
    svc = make(repo=repo, mail=mail, poll=poll, enabled=False, lock=lock)
    assert getattr(svc, method)() == {"skipped": "disabled"}
    assert repo.calls == []
    assert mail.calls == []
    assert poll.ingested == []
    assert lock.names == []


@pytest.mark.parametrize("method", ["ingest_recent", "catch_up"])
def test_pause_skips_without_db_or_graph(monkeypatch, method):
    monkeypatch.setenv("PAUSE_EMAIL_POLL", "true")
    repo, mail, poll = FakeRepo([OPEN_A]), FakeMail([[msg(OPEN_A, "m1")]]), FakePoll()
    svc = make(repo=repo, mail=mail, poll=poll)
    assert getattr(svc, method)() == {"skipped": "paused"}
    assert repo.calls == []
    assert mail.calls == []
    assert poll.ingested == []


@pytest.mark.parametrize("method", ["ingest_recent", "catch_up"])
def test_no_mailbox_skips_without_graph(method):
    repo, mail, poll = FakeRepo([OPEN_A]), FakeMail([[msg(OPEN_A, "m1")]]), FakePoll()
    svc = make(repo=repo, mail=mail, poll=poll, mailbox=None)
    assert getattr(svc, method)() == {"skipped": "no_mailbox"}
    assert mail.calls == []
    assert poll.ingested == []


# ---------------------------------------------------------------- lock


@pytest.mark.parametrize("method", ["ingest_recent", "catch_up"])
def test_lock_held_skips_without_db_or_graph(method):
    repo, mail, poll = FakeRepo([OPEN_A]), FakeMail([[msg(OPEN_A, "m1")]]), FakePoll()
    svc = make(repo=repo, mail=mail, poll=poll, lock=FakeLock(acquired=False))
    assert getattr(svc, method)() == {"skipped": "locked"}
    assert repo.calls == []
    assert mail.calls == []
    assert poll.ingested == []


@pytest.mark.parametrize("method", ["ingest_recent", "catch_up"])
def test_both_entry_points_hold_the_review_reply_ingest_lock(method):
    lock = FakeLock(True)
    getattr(make(lock=lock), method)()
    assert lock.names == ["review_reply_ingest"]


def test_empty_open_set_makes_no_graph_call():
    mail, poll = FakeMail([[msg(OPEN_A, "m1")]]), FakePoll()
    result = make(repo=FakeRepo([]), mail=mail, poll=poll).ingest_recent()
    assert mail.calls == []
    assert poll.ingested == []
    assert result["open"] == 0
    assert result["inserted"] == 0
    assert result["errors"] == 0


def test_empty_open_set_catch_up_makes_no_graph_call():
    mail = FakeMail()
    result = make(repo=FakeRepo([]), mail=mail).catch_up()
    assert mail.calls == []
    assert result["open"] == 0
    assert result["checked"] == 0
    assert result["remaining"] == 0


# ---------------------------------------------------------------- ingest_recent


def test_lookback_filter_string_shape_and_inbox_args():
    mail = FakeMail([[]])
    make(mail=mail).ingest_recent(lookback_hours=3)
    kwargs = mail.calls[0]
    assert kwargs["folder"] == "inbox"
    assert kwargs["mailbox"] == MAILBOX
    assert kwargs["order_by"] == "receivedDateTime desc"
    assert kwargs["filter_query"] == "receivedDateTime ge 2026-11-21T09:00:00Z"


def test_repo_read_uses_45_day_window():
    repo = FakeRepo([])
    make(repo=repo).ingest_recent()
    assert repo.calls == [45]


def test_inbox_filter_keeps_only_open_conversation_messages_case_sensitive():
    page = [
        msg(OPEN_A, "m1"),
        msg(OPEN_A.lower(), "m2"),          # case differs: not a match
        msg(OPEN_A + "X", "m3"),            # longer id: not a match
        msg(" " + OPEN_A, "m4"),            # padded: not a match
        msg("AAQkADOther", "m5"),           # unrelated thread
        msg(OPEN_B, "m6"),                  # second open thread: a match
    ]
    poll = FakePoll()
    svc = make(repo=FakeRepo([OPEN_A, OPEN_B]), mail=FakeMail([page]), poll=poll)
    result = svc.ingest_recent()
    assert [m["message_id"] for m in poll.ingested[0]] == ["m1", "m6"]
    assert result["listed"] == 6
    assert result["matched"] == 2


def test_ingest_call_args_match_reviewer_reply_contract():
    poll = FakePoll()
    make(mail=FakeMail([[msg(OPEN_A, "m1")]]), poll=poll).ingest_recent()
    assert poll.ingest_kwargs == [{
        "mailbox": MAILBOX,
        "folder": "inbox",
        "default_status": REVIEWER_REPLY_STATUS,
        "download_attachments": False,
    }]


def test_no_match_means_no_ingest_call():
    poll = FakePoll()
    make(mail=FakeMail([[msg("AAQkADOther", "m1")]]), poll=poll).ingest_recent()
    assert poll.ingested == []


def test_paging_stops_on_short_page():
    full = [msg(OPEN_A, f"a{i}") for i in range(50)]
    short = [msg(OPEN_A, "b1"), msg(OPEN_A, "b2"), msg(OPEN_A, "b3")]
    mail = FakeMail([full, short])
    result = make(mail=mail).ingest_recent(page_size=50, max_pages=20)
    assert len(mail.calls) == 2
    assert [c["skip"] for c in mail.calls] == [0, 50]
    assert all(c["top"] == 50 for c in mail.calls)
    assert result["matched"] == 53


def test_paging_stops_at_max_pages():
    full = [msg("AAQkADOther", f"x{i}") for i in range(50)]
    mail = FakeMail(default_page=full)
    make(mail=mail).ingest_recent(page_size=50, max_pages=3)
    assert len(mail.calls) == 3


def test_graph_non_200_is_counted_not_raised():
    poll = FakePoll()
    result = make(mail=FakeMail(status=503), poll=poll).ingest_recent()
    assert result["errors"] == 1
    assert result["inserted"] == 0
    assert poll.ingested == []


def test_graph_exception_is_counted_not_raised():
    result = make(mail=FakeMail(raises=RuntimeError("socket reset"))).ingest_recent()
    assert result["errors"] == 1


def test_programming_error_is_not_swallowed():
    with pytest.raises(TypeError):
        make(mail=FakeMail(raises=TypeError("bad call"))).ingest_recent()


def test_ingest_errors_and_inserted_count_are_reported():
    poll = FakePoll(new_count=2, errors=[{"message_id": "bad"}])
    result = make(mail=FakeMail([[msg(OPEN_A, "m1"), msg(OPEN_A, "m2")]]),
                  poll=poll).ingest_recent()
    assert result["inserted"] == 2
    assert result["errors"] == 1


def test_repo_failure_is_counted_not_raised():
    result = make(repo=FakeRepo(raises=RuntimeError("db down"))).ingest_recent()
    assert result["errors"] == 1


# ---------------------------------------------------------------- catch_up


def _filter_for(cid: str) -> str:
    return f"receivedDateTime ge 2000-01-01T00:00:00Z and conversationId eq '{cid}'"


def test_catch_up_lists_per_conversation_and_ingests_never_copies():
    mail, poll = FakeMail(by_conversation={OPEN_A: [msg(OPEN_A, "m1")]}), FakePoll(new_count=1)
    result = make(repo=FakeRepo([OPEN_A]), mail=mail, poll=poll).catch_up()
    assert mail.calls[0]["filter_query"] == _filter_for(OPEN_A)
    assert mail.calls[0]["order_by"] == "receivedDateTime desc"
    assert mail.calls[0]["folder"] == "inbox"
    assert mail.calls[0]["mailbox"] == MAILBOX
    assert poll.ingest_kwargs == [{
        "mailbox": MAILBOX, "folder": "inbox",
        "default_status": REVIEWER_REPLY_STATUS, "download_attachments": False,
    }]
    assert result["inserted"] == 1
    assert "copied" not in result


def test_catch_up_escapes_single_quotes_like_copy_does():
    cid = "AAQk'Quote"
    mail = FakeMail()
    make(repo=FakeRepo([cid]), mail=mail).catch_up()
    assert mail.calls[0]["filter_query"] == _filter_for("AAQk''Quote")


def test_catch_up_inserted_is_the_ingest_new_count():
    mail = FakeMail(by_conversation={OPEN_A: [msg(OPEN_A, "m1")]})
    result = make(repo=FakeRepo([OPEN_A]), mail=mail, poll=FakePoll(new_count=5)).catch_up()
    assert result["inserted"] == 5


def test_catch_up_isolates_a_failing_conversation():
    mail = FakeMail(
        by_conversation={OPEN_A: [msg(OPEN_A, "m1")], OPEN_C: [msg(OPEN_C, "m2"), msg(OPEN_C, "m3")]},
        fail_conversations={OPEN_B},
    )
    result = make(repo=FakeRepo([OPEN_A, OPEN_B, OPEN_C]), mail=mail).catch_up()
    assert {c["filter_query"] for c in mail.calls} == {_filter_for(OPEN_A), _filter_for(OPEN_B), _filter_for(OPEN_C)}
    assert result["open"] == 3
    assert result["checked"] == 3
    assert result["matched"] == 3
    assert result["inserted"] == 3
    assert result["errors"] == 1


def test_catch_up_isolates_an_exception_in_one_conversation():
    mail = FakeMail(
        by_conversation={OPEN_A: [msg(OPEN_A, "m1")], OPEN_C: [msg(OPEN_C, "m2")]},
        raise_conversations={OPEN_B},
    )
    result = make(repo=FakeRepo([OPEN_A, OPEN_B, OPEN_C]), mail=mail).catch_up()
    assert result["checked"] == 3
    assert result["inserted"] == 2
    assert result["errors"] == 1


def test_catch_up_log_names_only_conversation_suffix(caplog):
    mail = FakeMail(fail_conversations={OPEN_A})
    caplog.set_level(logging.WARNING)
    make(repo=FakeRepo([OPEN_A]), mail=mail).catch_up()
    assert f"conversation={OPEN_A[-8:]}" in caplog.text
    assert OPEN_A not in caplog.text


def test_catch_up_budget_stops_and_reports_remaining():
    ids = [OPEN_A, OPEN_B, OPEN_C, OPEN_D, OPEN_E]
    # start call returns 0; conversation checks see 10, 20, 30, 40 (<45) then 50 (stop).
    result = make(repo=FakeRepo(ids), clock=StepClock(10)).catch_up(max_seconds=45)
    assert result["open"] == 5
    assert result["checked"] == 4
    assert result["remaining"] == 1


def _ids(n: int) -> list[str]:
    return [f"AAQkADThread{i:03d}" for i in range(n)]


def _order(mail: FakeMail) -> list[str]:
    marker = "conversationId eq '"
    return [c["filter_query"].split(marker, 1)[1][:-1].replace("''", "'") for c in mail.calls]


def _run_day(ids, when, *, clock=None, max_seconds=None) -> FakeMail:
    mail = FakeMail()
    kwargs = {} if max_seconds is None else {"max_seconds": max_seconds}
    make(repo=FakeRepo(ids), mail=mail, now=lambda: when, clock=clock).catch_up(**kwargs)
    return mail


def test_rotation_is_stable_within_a_utc_day():
    ids = _ids(50)
    morning = _order(_run_day(ids, NOW))
    evening = _order(_run_day(ids, NOW + timedelta(hours=9)))
    assert morning == evening
    assert sorted(morning) == sorted(ids)


def test_rotation_differs_across_days():
    ids = _ids(50)
    orders = {tuple(_order(_run_day(ids, NOW + timedelta(days=d)))) for d in range(5)}
    assert len(orders) == 5


def test_forty_percent_budget_reaches_every_conversation_of_50_within_14_days():
    ids = _ids(50)
    seen: set[str] = set()
    for day in range(14):
        # 20 checks per day: the start call returns 0, check k sees k, and k=21 stops.
        mail = _run_day(ids, NOW + timedelta(days=day), clock=StepClock(1), max_seconds=21)
        assert len(mail.calls) == 20
        seen.update(_order(mail))
    assert seen == set(ids)


def test_catch_up_never_ingests_a_message_from_another_thread():
    """Defence in depth (EM, mutation survivor): even if Graph returned a message from a
    different conversation for a per-thread listing, catch_up must not store it."""
    stray = msg("AAQkADSomeoneElsesThread", "stray")
    mail = FakeMail(by_conversation={OPEN_A: [msg(OPEN_A, "m1"), stray]})
    poll = FakePoll()
    make(repo=FakeRepo([OPEN_A]), mail=mail, poll=poll).catch_up()
    stored = [m for batch in poll.ingested for m in batch]
    assert [m["conversation_id"] for m in stored] == [OPEN_A]
