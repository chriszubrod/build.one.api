"""U-799 — the MS Graph mail client's query shapes, and two new helpers.

Graph answered 400 to two shapes `list_messages` always produced, and the caller saw an
empty `messages` list (verified live 2026-11-22, invoice@ mailbox):

* every `search=` call — `$orderby` is not supported alongside `$search`;
* a `$filter` on a property other than the ordered one (`flag/flagStatus eq 'flagged'`) —
  "The restriction or sort order is too complex for this operation."

and a caller-quoted search term was double-quoted into a third 400.

These tests pin the request each call now sends, using a fake Graph client — no network.
"""

from unittest.mock import patch

import pytest

from integrations.ms.base.errors import MsGraphError
from integrations.ms.mail.external import client as mail

_CLIENT = "integrations.ms.mail.external.client.MsGraphClient"


class FakeGraph:
    """Records every call; serves queued GET responses in order."""

    def __init__(self, gets=None, get_error=None, errors_by_suffix=None):
        self.gets = list(gets or [])
        self.get_error = get_error
        self.errors_by_suffix = errors_by_suffix or {}
        self.calls = []  # (method, path, params)

    @property
    def params(self):
        """Params of the first request."""
        return self.calls[0][2]

    def __call__(self):  # MsGraphClient() -> instance
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path, params=None, **kwargs):
        self.calls.append(("GET", path, params))
        for suffix, err in self.errors_by_suffix.items():
            if path.endswith(suffix):
                raise err
        if self.get_error is not None:
            raise self.get_error
        return self.gets.pop(0) if self.gets else {"value": []}

    def delete(self, path, **kwargs):
        self.calls.append(("DELETE", path, None))
        return {}


def _msg(mid, received, subject="s", attachments=None, sender="a@x.com"):
    m = {"id": mid, "receivedDateTime": received, "subject": subject,
         "from": {"emailAddress": {"address": sender, "name": sender}}}
    if attachments is not None:
        m["attachments"] = [{"id": f"att-{n}", "name": n} for n in attachments]
    return m


A, B = "2026-10-12T05:00:00Z", "2026-10-13T05:00:00Z"


@pytest.fixture
def writes_on(monkeypatch):
    monkeypatch.setenv("ALLOW_MS_WRITES", "true")


def _graph_error(status=400, text="The restriction or sort order is too complex for this operation."):
    e = MsGraphError(text)
    e.http_status = status
    return e


# --------------------------------------------------------------------------- $search


def test_search_sends_no_orderby_and_no_skip():
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="alley-cassetty")
    assert r["status_code"] == 200
    assert "$orderby" not in fake.params, "Graph rejects $orderby with $search (400)"
    assert "$skip" not in fake.params


@pytest.mark.parametrize("given", ['"alley"', '""alley""', '  "alley" ', "alley", 'al"ley'])
def test_search_term_is_quoted_exactly_once(given):
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        mail.list_messages(search=given)
    term = fake.params["$search"]
    assert term.startswith('"') and term.endswith('"')
    assert '"' not in term[1:-1], f"inner quote survived: {term!r}"


@pytest.mark.parametrize("order_by, expected", [
    ("receivedDateTime desc", ["m3", "m2", "m1"]),
    ("receivedDateTime asc", ["m1", "m2", "m3"]),
])
def test_search_results_are_sorted_client_side(order_by, expected):
    page = {"value": [_msg("m2", "2026-10-02T00:00:00Z"), _msg("m1", "2026-10-01T00:00:00Z"),
                      _msg("m3", "2026-10-03T00:00:00Z")]}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="x", order_by=order_by, top=10)
    assert [m["message_id"] for m in r["messages"]] == expected


def test_search_with_a_filter_is_refused_locally():
    # Graph: "The query parameter '$filter' is not supported with '$search'" (verified live).
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="x", filter_query="isRead eq false")
    assert r["status_code"] == 400 and r["messages"] == []
    assert "cannot be combined" in r["message"]
    assert fake.calls == [], "must not send a request Graph will reject"


# --------------------------------------------------------------------------- $filter + $orderby


def test_non_date_filter_is_made_to_lead_with_the_ordered_property():
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        mail.list_messages(filter_query="flag/flagStatus eq 'flagged'")
    params = fake.params
    assert params["$orderby"] == "receivedDateTime desc"
    assert params["$filter"] == (
        "receivedDateTime ge 2000-01-01T00:00:00Z and (flag/flagStatus eq 'flagged')"
    )


def test_or_filter_keeps_its_meaning_inside_parentheses():
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        mail.list_messages(filter_query="isRead eq false or hasAttachments eq true")
    assert fake.params["$filter"].endswith("and (isRead eq false or hasAttachments eq true)")


@pytest.mark.parametrize("existing", [
    # The exact shapes today's callers send (entities/email_message/business/service.py):
    "receivedDateTime ge 2026-11-20T06:00:00.000Z",
    "receivedDateTime ge 2000-01-01T00:00:00Z and conversationId eq 'AAQk'",
    "(receivedDateTime ge 2026-01-01T00:00:00Z)",
    "ReceivedDateTime ge 2026-01-01T00:00:00Z",
])
def test_filters_already_leading_with_received_are_unchanged(existing):
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        mail.list_messages(filter_query=existing, order_by="receivedDateTime asc")
    assert fake.params["$filter"] == existing


def test_filter_is_left_alone_when_sorting_on_another_property():
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        mail.list_messages(filter_query="isRead eq false", order_by="subject asc")
    assert fake.params["$filter"] == "isRead eq false"


def test_no_filter_means_no_filter_param():
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        mail.list_messages()
    assert "$filter" not in fake.calls[0][2]


# --------------------------------------------------------------------------- errors


def test_graph_error_is_an_error_envelope_not_a_success(caplog):
    fake = FakeGraph(get_error=_graph_error())
    with patch(_CLIENT, fake), caplog.at_level("ERROR"):
        r = mail.list_messages(filter_query="flag/flagStatus eq 'flagged'")
    assert r["status_code"] == 400
    assert r["messages"] == []
    assert "too complex" in r["message"]
    # The log line names the query that failed, so "0 results" can't hide the cause.
    assert any("flag/flagStatus" in rec.getMessage() for rec in caplog.records)


# --------------------------------------------------------------------------- delete_attachment_from_message


def test_delete_attachment_refused_by_the_write_gate_makes_no_call(monkeypatch):
    monkeypatch.delenv("ALLOW_MS_WRITES", raising=False)
    fake = FakeGraph(gets=[{"id": "m1", "isDraft": True}])
    with patch(_CLIENT, fake):
        r = mail.delete_attachment_from_message("m1", "a1")
    # Same envelope MsGraphClient's own gate produces (MsWriteRefusedError -> 500, not retryable).
    assert r["status_code"] == 500
    assert r["is_retryable"] is False and r["is_auth_error"] is False
    assert "ALLOW_MS_WRITES" in r["message"]
    assert fake.calls == [], "gate off: no draft read, no delete"


def test_delete_attachment_checks_draft_then_deletes(writes_on):
    fake = FakeGraph(gets=[{"id": "m1", "isDraft": True}])
    with patch(_CLIENT, fake):
        r = mail.delete_attachment_from_message("m1", "a1")
    assert r["status_code"] == 204
    assert fake.calls[0][0] == "GET" and fake.calls[0][1] == "me/messages/m1"
    assert fake.calls[1] == ("DELETE", "me/messages/m1/attachments/a1", None)


@pytest.mark.parametrize("is_draft", [False, None, "true"])
def test_delete_attachment_refuses_a_non_draft(is_draft, writes_on):
    msg = {"id": "m1"} if is_draft is None else {"id": "m1", "isDraft": is_draft}
    fake = FakeGraph(gets=[msg])
    with patch(_CLIENT, fake):
        r = mail.delete_attachment_from_message("m1", "a1")
    assert r["status_code"] == 409
    assert not any(c[0] == "DELETE" for c in fake.calls), "must not delete from a received message"


def test_delete_attachment_uses_the_delegated_mailbox_path(writes_on):
    fake = FakeGraph(gets=[{"id": "m1", "isDraft": True}])
    with patch(_CLIENT, fake):
        mail.delete_attachment_from_message("m1", "a1", mailbox="invoice@x.com")
    assert fake.calls[0][1] == "users/invoice@x.com/messages/m1"
    assert fake.calls[1][1] == "users/invoice@x.com/messages/m1/attachments/a1"


def test_delete_attachment_graph_error_is_an_envelope(writes_on):
    fake = FakeGraph(get_error=_graph_error(404, "not found"))
    with patch(_CLIENT, fake):
        r = mail.delete_attachment_from_message("m1", "a1")
    assert r["status_code"] == 404
    assert not any(c[0] == "DELETE" for c in fake.calls)


# --------------------------------------------------------------------------- find_messages_by_attachment_name


def test_attachment_scan_searches_all_folders_in_the_window():
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        mail.find_messages_by_attachment_name("x", A, B)
    _, path, params = fake.calls[0]
    assert path == "me/messages", "all folders, not one mailFolder"
    assert params["$filter"] == f"receivedDateTime ge {A} and receivedDateTime lt {B}"
    assert params["$orderby"].startswith("receivedDateTime")
    assert "attachments(" in params["$expand"]


def test_attachment_scan_matches_a_second_attachment_name_case_insensitively():
    # The 10/12 Alley-Cassetty email: generic subject, invoice number only in the 2nd PDF name.
    page = {"value": [
        _msg("m1", "2026-10-12T19:14:00Z", subject="Alley-Cassetty Invoice",
             attachments=["image001.png", "000688547_5007900.pdf", "000688548_5007900.PDF"]),
        _msg("m2", "2026-10-12T20:00:00Z", subject="Other", attachments=["x.pdf"]),
    ]}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("000688548", A, B)
    assert r["status_code"] == 200
    assert [m["message_id"] for m in r["messages"]] == ["m1"]
    assert r["scanned"] == 2 and r["truncated"] is False
    assert any(a["name"] == "000688548_5007900.PDF" for a in r["messages"][0]["attachments"])


def test_attachment_scan_also_matches_subject_and_sender():
    page = {"value": [
        _msg("s", "t", subject="Pella statement", attachments=[]),
        _msg("f", "t", subject="x", attachments=[], sender="ar@PELLA.com"),
        _msg("n", "t", subject="x", attachments=[]),
    ]}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("pella", A, B)
    assert sorted(m["message_id"] for m in r["messages"]) == ["f", "s"]


def test_attachment_scan_follows_next_links_and_reports_truncation():
    pages = [
        {"value": [_msg("m1", "t", attachments=["hit.pdf"])],
         "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=1"},
        {"value": [_msg("m2", "t", attachments=["hit.pdf"])],
         "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=2"},
    ]
    fake = FakeGraph(gets=pages)
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("hit", A, B, max_pages=2)
    assert [m["message_id"] for m in r["messages"]] == ["m1", "m2"]
    assert r["truncated"] is True, "cap hit with more pages left — must not claim a full search"
    assert fake.calls[1][1] == "me/messages?$skiptoken=1"


def test_attachment_scan_requires_a_term():
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("  ", A, B)
    assert r["status_code"] == 400
    assert fake.calls == []


def test_attachment_scan_graph_error_is_not_a_clean_empty_result():
    fake = FakeGraph(get_error=_graph_error(503, "unavailable"))
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("x", A, B)
    assert r["status_code"] == 503
    assert r["messages"] == [] and r["truncated"] is True


@pytest.mark.parametrize("bad", ["2026-10-12", "a", "2026-10-12T05:00:00Z or true", "", None,
                                 "2026-02-30T05:00:00Z", "2026-10-12T05:00:00Z\n", "2026-10-12T25:00:00Z"])
def test_attachment_scan_rejects_a_malformed_window(bad):
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("x", bad, B)
    assert r["status_code"] == 400
    assert fake.calls == [], "a malformed instant must never reach $filter"


def test_attachment_scan_names_the_folder_of_each_hit():
    page = {"value": [
        dict(_msg("vendor", "t", attachments=["000688548.pdf"]), parentFolderId="inbox-id"),
        dict(_msg("ours", "t", attachments=["000688548.pdf"]), parentFolderId="drafts-id"),
        dict(_msg("sub", "t", attachments=["000688548.pdf"]), parentFolderId="some-subfolder"),
    ]}
    folders = {"value": [{"id": "inbox-id", "displayName": "Inbox"},
                         {"id": "drafts-id", "displayName": "Drafts"}]}
    fake = FakeGraph(gets=[page, folders])
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("000688548", A, B)
    assert {m["message_id"]: m["folder_name"] for m in r["messages"]} == {
        "vendor": "Inbox", "ours": "Drafts", "sub": None}


def test_attachment_scan_folder_lookup_failure_keeps_the_matches():
    fake = FakeGraph(gets=[{"value": [_msg("m1", "t", attachments=["hit.pdf"])]}],
                     errors_by_suffix={"/mailFolders": _graph_error(503, "unavailable")})
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("hit", A, B)
    assert r["status_code"] == 200
    assert [m["message_id"] for m in r["messages"]] == ["m1"]
    assert r["messages"][0]["folder_name"] is None


def test_attachment_scan_skips_the_folder_read_when_nothing_matched():
    fake = FakeGraph(gets=[{"value": [_msg("m1", "t", attachments=["other.pdf"])]}])
    with patch(_CLIENT, fake):
        mail.find_messages_by_attachment_name("hit", A, B)
    assert not any(c[1].endswith("/mailFolders") for c in fake.calls)


# --------------------------------------------------------------------------- pinned caller: Ramp chaser digest


def test_ramp_digest_conversation_lookup_now_sends_a_graph_legal_filter():
    """Before U-799 this exact call got Graph 400 ("restriction or sort order is too
    complex"), so `_conversation_in_folder` returned None (inconclusive) for every digest."""
    from entities.ramp_chaser_digest.business.digest_service import RampChaserDigestService

    fake = FakeGraph(gets=[{"value": [_msg("m1", "2026-11-20T00:00:00Z")]}])
    with patch(_CLIENT, fake):
        hit = RampChaserDigestService._conversation_in_folder(
            mail.list_messages, folder="sentitems", conversation_id="AAQk")
    assert hit is True
    params = fake.params
    assert params["$filter"] == (
        "receivedDateTime ge 2000-01-01T00:00:00Z and (conversationId eq 'AAQk')"
    )
    assert params["$orderby"] == "receivedDateTime desc"


# --------------------------------------------------------------------------- U-799 fix round (Codex xhigh + Pass 3)


@pytest.mark.parametrize("message_id, attachment_id", [
    ("draft", "../../received/attachments/orig"),  # normalised onto a RECEIVED message after the draft check
    ("draft?x=", "a1"),                             # query swallows the attachment segment -> DELETE the draft
    ("draft#x", "a1"),
    ("draft", "a/b"),
    ("", "a1"),
    ("draft", None),
])
def test_delete_attachment_refuses_ids_that_could_retarget_the_request(writes_on, message_id, attachment_id):
    fake = FakeGraph(gets=[{"id": "draft", "isDraft": True}])
    with patch(_CLIENT, fake):
        r = mail.delete_attachment_from_message(message_id, attachment_id)
    assert r["status_code"] == 400
    assert fake.calls == [], "no draft read and no DELETE for a malformed id"


def test_delete_attachment_accepts_a_real_graph_id_shape(writes_on):
    mid = "AAMkAGE2YjU2M2E5LWQ2MjQtNGRiNC04ODI2LWY4MDY3NjQ5MDExMQBGAAAAAABTMkZ-d3BLRa5vo-DM0s7HBwAM_dQa="
    aid = "AAMkAGE2YjU2M2E5LWQ2MjQtNGRiNC04ODI2LWY4MDY3NjQ5MDExMQBGAAAAAABTMkZ-ABEgAQAMa1tws-LvZDguESBGSeuoA="
    fake = FakeGraph(gets=[{"id": mid, "isDraft": True}])
    with patch(_CLIENT, fake):
        r = mail.delete_attachment_from_message(mid, aid)
    assert r["status_code"] == 204
    assert fake.calls[1] == ("DELETE", f"me/messages/{mid}/attachments/{aid}", None)


@pytest.mark.parametrize("mailbox", ["x@y.com/../me", "x@y.com?z", "../me"])
def test_delete_attachment_refuses_a_path_shaped_mailbox(writes_on, mailbox):
    fake = FakeGraph(gets=[{"id": "m1", "isDraft": True}])
    with patch(_CLIENT, fake):
        r = mail.delete_attachment_from_message("m1", "a1", mailbox=mailbox)
    assert r["status_code"] == 400 and fake.calls == []


def test_compound_order_by_keeps_the_first_clause_direction():
    page = {"value": [_msg("old", "2026-10-01T00:00:00Z"), _msg("new", "2026-10-03T00:00:00Z")]}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="x", order_by="receivedDateTime desc, subject asc", top=10)
    assert [m["message_id"] for m in r["messages"]] == ["new", "old"]


def test_search_re_sorts_only_the_requested_relevance_slice():
    # Graph's relevance order: A first. top=1 must return A, not the newer lower-ranked B.
    page = {"value": [_msg("A", "2026-10-01T00:00:00Z"), _msg("B", "2026-10-02T00:00:00Z")]}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="x", top=1)
    assert [m["message_id"] for m in r["messages"]] == ["A"]


def test_search_with_skip_is_refused_locally():
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="x", skip=25)
    assert r["status_code"] == 400 and "skip" in r["message"]
    assert fake.calls == []


@pytest.mark.parametrize("spaced", [
    "( (receivedDateTime lt 2000-01-01T00:00:00Z) )",
    "(  (  receivedDateTime ge 2026-01-01T00:00:00Z))",
])
def test_spaced_nested_parentheses_still_count_as_leading_with_received(spaced):
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        mail.list_messages(filter_query=spaced)
    assert fake.params["$filter"] == spaced


@pytest.mark.parametrize("pages", [0, -1])
def test_attachment_scan_refuses_a_nonpositive_page_budget(pages):
    fake = FakeGraph()
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("x", A, B, max_pages=pages)
    assert r["status_code"] == 400 and r["truncated"] is False
    assert fake.calls == [], "must not report a 'complete' search that never ran"


def test_attachment_scan_is_case_insensitive_on_letters():
    page = {"value": [_msg("m1", "t", subject="x", attachments=["RB - PELLA TENN - STATEMENT.pdf"]),
                      _msg("m2", "t", subject="x", attachments=["other.pdf"])]}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.find_messages_by_attachment_name("Pella Tenn", A, B)
    assert [m["message_id"] for m in r["messages"]] == ["m1"]


def test_ramp_digest_deleted_items_hit_counts_only_when_still_a_draft():
    from entities.ramp_chaser_digest.business.digest_service import RampChaserDigestService as S

    def fn_with(messages):
        return lambda **kw: {"status_code": 200, "messages": messages}

    assert S._conversation_in_folder(fn_with([{"is_draft": True}]), folder="deleteditems",
                                     conversation_id="c", require_draft=True) is True
    assert S._conversation_in_folder(fn_with([{"is_draft": False}]), folder="deleteditems",
                                     conversation_id="c", require_draft=True) is None
    assert S._conversation_in_folder(fn_with([]), folder="deleteditems",
                                     conversation_id="c", require_draft=True) is False
    # sentitems keeps its old meaning: any match is a send.
    assert S._conversation_in_folder(fn_with([{"is_draft": False}]), folder="sentitems",
                                     conversation_id="c") is True


# --------------------------------------------------------------------------- U-799 fix round 2 (Codex fix-delta re-review)


@pytest.mark.parametrize("mailbox, segment", [
    ("o'brien@example.com", "users/o%27brien@example.com"),
    ("invoice@rogersbuild.com", "users/invoice@rogersbuild.com"),
    ("a+b!c&d@x.co", "users/a%2Bb%21c%26d@x.co"),
])
def test_legitimate_mailboxes_are_accepted_and_encoded_as_one_segment(writes_on, mailbox, segment):
    fake = FakeGraph(gets=[{"id": "m1", "isDraft": True}])
    with patch(_CLIENT, fake):
        r = mail.delete_attachment_from_message("m1", "a1", mailbox=mailbox)
    assert r["status_code"] == 204
    assert fake.calls[0][1] == f"{segment}/messages/m1"


def test_mailbox_path_leaves_plain_addresses_unchanged_for_existing_callers():
    assert mail._mailbox_path("invoice@rogersbuild.com") == "users/invoice@rogersbuild.com"
    assert mail._mailbox_path(None) == "me"


def test_search_never_advertises_a_page_it_cannot_serve():
    page = {"value": [_msg(f"m{i}", f"2026-10-{i + 1:02d}T00:00:00Z") for i in range(3)],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=1"}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="x", top=2)
    assert r["has_more"] is False and r["search_truncated"] is True


def test_non_search_listing_still_reports_has_more():
    page = {"value": [_msg("m1", "t")], "@odata.nextLink": "https://graph.microsoft.com/v1.0/x?$skiptoken=1"}
    fake = FakeGraph(gets=[page, {"value": []}])
    with patch(_CLIENT, fake):
        r = mail.list_messages(top=1)
    assert r["has_more"] is True and "search_truncated" not in r


def test_ramp_deleted_items_lookup_filters_drafts_server_side():
    from entities.ramp_chaser_digest.business.digest_service import RampChaserDigestService as S
    seen = {}

    def fn(**kw):
        seen.update(kw)
        return {"status_code": 200, "messages": []}

    S._conversation_in_folder(fn, folder="deleteditems", conversation_id="c'1", require_draft=True)
    assert seen["filter_query"] == "conversationId eq 'c''1' and isDraft eq true"
    S._conversation_in_folder(fn, folder="sentitems", conversation_id="c")
    assert seen["filter_query"] == "conversationId eq 'c'"


def test_search_exactly_top_matches_is_not_truncated():
    page = {"value": [_msg("m1", "2026-10-01T00:00:00Z"), _msg("m2", "2026-10-02T00:00:00Z")], "@odata.count": 2}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="invoice", top=2)
    assert len(r["messages"]) == 2
    assert r["search_truncated"] is False, "every match was returned"


def test_search_more_than_top_matches_on_one_page_is_truncated():
    page = {"value": [_msg(f"m{i}", f"2026-10-0{i + 1}T00:00:00Z") for i in range(3)]}
    fake = FakeGraph(gets=[page])
    with patch(_CLIENT, fake):
        r = mail.list_messages(search="invoice", top=2)
    assert r["search_truncated"] is True
