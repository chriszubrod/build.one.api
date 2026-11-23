"""U-797b2: resolve_reply_notification_once returns (em, notification); resolve_reply_once is a thin wrapper."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared.authz import provenance as prov
from shared.authz.provenance import ReviewerProvenanceRefused


NOTIF = SimpleNamespace(Id=42)
TARGET = ("contract_labor", 50)
KW = dict(
    reviewer_email="pm@x.test",
    reviewer_email_message_public_id="EM7",
    target=TARGET,
    what="contract_labor_decision",
    project_id=9,
    work_date="2026-11-19",
)


def _em():
    return SimpleNamespace(
        id=7, public_id="EM7", conversation_id="Conv-B", subject="Crew", folder="inbox",
        from_address="pm@x.test",
    )


@pytest.fixture
def world(monkeypatch):
    st = SimpleNamespace(
        flag=True, notif=NOTIF, raise_exc=None, em=_em(),
        bind_calls=[], notif_calls=[],
    )

    def fake_flag():
        return st.flag

    def fake_bind(**kw):
        st.bind_calls.append(kw)
        return st.em

    def fake_notif(conversation_id, target, **kw):
        st.notif_calls.append((conversation_id, target, kw))
        if st.raise_exc is not None:
            raise st.raise_exc
        return st.notif

    monkeypatch.setattr(prov, "_binder_v2_enabled", fake_flag)
    monkeypatch.setattr(prov, "bind_reviewer_reply", fake_bind)
    monkeypatch.setattr(prov, "bind_reply_via_notification", fake_notif)
    return st


def _new():
    from shared.authz.provenance import resolve_reply_notification_once

    return resolve_reply_notification_once(**KW)


def test_new_function_returns_em_and_notification_when_bound(world):
    got = _new()
    assert got == (world.em, NOTIF)
    assert len(world.notif_calls) == 1
    conv, target, kw = world.notif_calls[0]
    assert conv == "Conv-B"
    assert target == TARGET
    assert kw["project_id"] == 9
    assert kw["work_date"] == "2026-11-19"
    assert kw["reply_subject"] == "Crew"


def test_new_function_returns_none_when_no_notification_row(world):
    world.notif = None
    assert _new() is None
    assert len(world.notif_calls) == 1


def test_new_function_flag_off_returns_none_with_no_lookup(world):
    world.flag = False
    assert _new() is None
    assert world.bind_calls == []
    assert world.notif_calls == []


def test_new_function_refusal_propagates(world):
    world.raise_exc = ReviewerProvenanceRefused("Cannot apply the reviewer decision: ambiguous")
    with pytest.raises(ReviewerProvenanceRefused):
        _new()


def test_wrapper_returns_em_when_bound(world):
    from shared.authz.provenance import resolve_reply_once

    assert resolve_reply_once(**KW) is world.em


def test_wrapper_returns_none_when_no_row(world):
    from shared.authz.provenance import resolve_reply_once

    world.notif = None
    assert resolve_reply_once(**KW) is None


def test_wrapper_flag_off_returns_none_with_no_lookup(world):
    from shared.authz.provenance import resolve_reply_once

    world.flag = False
    assert resolve_reply_once(**KW) is None
    assert world.bind_calls == []
    assert world.notif_calls == []


def test_wrapper_refusal_propagates(world):
    from shared.authz.provenance import resolve_reply_once

    world.raise_exc = ReviewerProvenanceRefused("Cannot apply the reviewer decision: ambiguous")
    with pytest.raises(ReviewerProvenanceRefused):
        resolve_reply_once(**KW)


@pytest.mark.parametrize("flag,row", [(True, NOTIF), (True, None), (False, NOTIF)])
def test_wrapper_matches_new_function_on_every_branch(world, flag, row):
    from shared.authz.provenance import resolve_reply_once

    world.flag = flag
    world.notif = row
    new = _new()
    expected_em = new[0] if new is not None else None
    assert resolve_reply_once(**KW) is expected_em
