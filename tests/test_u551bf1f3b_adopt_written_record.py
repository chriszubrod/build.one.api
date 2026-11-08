"""U-551b-f1-f3b ADOPT: system-admin action giving a record-less `written` first write its record.

Repo, lock, connector, resolver and line reads are mocked; nothing opens a connection or calls QBO."""
import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.auth.business.service import get_current_user_api
from entities.coding_suggestion.business.model import CodingSuggestion
from entities.coding_suggestion.business.service import CodingSuggestionService
from shared.api.admin import RECORD_REQUIRED_SINCE_UTC
from shared.lifecycle.completion_gate import HoldingCodeRefused

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
RESOLVER = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
CONNECTOR = "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
LINES = "entities.expense_line_item.business.service.ExpenseLineItemService"
AFTER = datetime(2026, 11, 8, 12, 0, 0)
BEFORE = datetime(2026, 11, 1, 12, 0, 0)


def _item(**over):
    base = dict(
        public_id=PUBLIC_ID, status="written", confirmation_source="review_submit", confirmation_seq=4,
        written_at=AFTER, written_sync_token=None, written_lines_json=None, expense_id=77,
        qbo_purchase_qbo_id="purchase-1", realm_id="realm-1", sync_token_at_suggest="SUGGEST-5",
        confirmed_sub_cost_code_id=101, confirmed_project_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _svc(item, adopted=SimpleNamespace(public_id=PUBLIC_ID)):
    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=item)
    svc.repo = MagicMock()
    svc.repo.adopt_written_record.return_value = adopted
    return svc


def _local(qbo_id, scc=101, project=None):
    return SimpleNamespace(qbo_id=qbo_id, sub_cost_code_id=scc, project_id=project)


def _live_line(line_id, item="item-A", customer=None, detail_type="ItemBasedExpenseLineDetail"):
    d = {"ItemRef": {"value": item}}
    if customer:
        d["CustomerRef"] = {"value": customer}
    return {"Id": line_id, "DetailType": detail_type, "ItemBasedExpenseLineDetail": d}


def _adopt(svc, *, locals_=(), live=(), token="LIVE-7", resolver=None, customer_ref="cust-1", gate=None):
    connector = MagicMock()
    raw = {"Id": "purchase-1", "Line": list(live)}
    if token is not None:
        raw["SyncToken"] = token
    connector.get_purchase_raw.return_value = raw
    connector._get_qbo_customer_ref.return_value = SimpleNamespace(value=customer_ref) if customer_ref else None
    line_svc = MagicMock()
    line_svc.return_value.read_by_expense_id.return_value = list(locals_)
    res = resolver or (lambda scc, realm: SimpleNamespace(value=f"item-{scc}"))
    with patch(GATE, return_value=gate), patch(CONNECTOR, return_value=connector), \
            patch(LINES, line_svc), patch(RESOLVER, side_effect=res):
        out = svc.adopt_written_record(PUBLIC_ID, record_required_since=RECORD_REQUIRED_SINCE_UTC)
    return out, connector


def _no_write(svc):
    svc.repo.adopt_written_record.assert_not_called()


def test_gate_off_is_writes_disabled_with_no_read_get_or_write():
    # kills: the gate checked after the read / not at all (a mark while external writes are off)
    svc = _svc(_item())
    out, connector = _adopt(svc, gate="writes off")
    assert out == {"status": "writes_disabled", "reason": "writes off"}
    svc.read_by_public_id.assert_not_called()
    connector.get_purchase_raw.assert_not_called()
    _no_write(svc)


@pytest.mark.parametrize("item,expected", [
    (None, "not_found"),                                                       # kills: None deref
    (_item(status="confirmed"), "not_written"),                                # kills: adopting a non-written row
    (_item(written_sync_token="9"), "has_record"),                             # kills: overwriting a real token
    (_item(written_lines_json="[]"), "has_record"),                            # kills: only one half checked
    (_item(confirmation_source="complete_repoint"), "not_a_first_write"),      # kills: adopting a repoint row
    (_item(confirmation_source=None), "not_a_first_write"),                    # kills: NULL source adopted
    (_item(written_at=BEFORE), "legacy"),                                      # kills: the legacy cut-off dropped
    (_item(written_at=None), "legacy"),                                        # kills: unknown age read as new
    (_item(written_at=AFTER.isoformat()), None),                               # str WrittenAt parsed, not refused
])
def test_pre_get_refusals_have_exact_status_and_no_get_no_write(item, expected):
    # kills: reordered / renamed refusals, a GET or write before the cheap refusals
    svc = _svc(item)
    out, connector = _adopt(svc, locals_=[_local("L1")], live=[_live_line("L1", "item-101")])
    if expected is None:
        assert out["status"] == "adopted"
        return
    assert out == {"status": expected}
    connector.get_purchase_raw.assert_not_called()
    _no_write(svc)


def test_lock_busy_is_busy_with_no_read():
    # kills: running the adopt without the suggestion lock
    from tests.conftest import mock_qbo_app_lock_denied
    svc = _svc(_item())
    with patch("entities.coding_suggestion.business.service.qbo_app_lock", mock_qbo_app_lock_denied):
        out, _ = _adopt(svc)
    assert out == {"status": "busy"}
    svc.read_by_public_id.assert_not_called()
    _no_write(svc)


def test_happy_path_gets_once_adopts_the_live_token_and_the_record_round_trips():
    # kills: SyncTokenAtSuggest adopted (I4), a str scc, extra GETs, a missing seq fence, a non-round-tripping record
    svc = _svc(_item(confirmation_seq=4, confirmed_sub_cost_code_id=None))
    out, connector = _adopt(
        svc, token="LIVE-7",
        locals_=[_local("L1", 101), _local("L2", 202, project=9), _local(None, 303), _local("L4", None)],
        live=[_live_line("L1", "item-101"), _live_line("L2", "item-202", customer="cust-1"), _live_line("L9", "x")])
    assert out == {"status": "adopted", "lines": 2, "written_sync_token": "LIVE-7"}
    connector.get_purchase_raw.assert_called_once_with(realm_id="realm-1", qbo_purchase_qbo_id="purchase-1")
    args = svc.repo.adopt_written_record.call_args.args
    assert args[0] == PUBLIC_ID and args[1] == "LIVE-7" and args[3] == 4
    assert args[1] != "SUGGEST-5"
    parsed = CodingSuggestion(**{**{f: None for f in CodingSuggestion.__dataclass_fields__},
                                 "written_lines_json": args[2]}).written_lines
    assert parsed == [
        {"qbo_line_id": "L1", "item_ref_value": "item-101", "sub_cost_code_id": 101},
        {"qbo_line_id": "L2", "item_ref_value": "item-202", "sub_cost_code_id": 202},
    ]


def test_one_of_two_lines_still_on_the_placeholder_refuses_and_writes_nothing():
    # kills: partial adoption (recording only the lines that verified)
    svc = _svc(_item(confirmed_sub_cost_code_id=None))
    out, _ = _adopt(svc, locals_=[_local("L1", 101), _local("L2", 202)],
                    live=[_live_line("L1", "item-101"),
                          _live_line("L2", "", detail_type="AccountBasedExpenseLineDetail")])
    assert out == {"status": "line_not_recoded"}
    _no_write(svc)


def test_wrong_item_is_line_not_recoded():
    # kills: verifying by presence instead of by the confirmed item
    svc = _svc(_item(confirmation_source="confirm_endpoint"))
    out, _ = _adopt(svc, locals_=[_local("L1", 101)], live=[_live_line("L1", "item-OTHER")])
    assert out == {"status": "line_not_recoded"}
    _no_write(svc)


def test_deleted_qbo_line_is_line_missing_not_line_not_recoded():
    # kills: a deleted QBO line collapsed into line_not_recoded (the operator needs the distinct reason)
    svc = _svc(_item(confirmation_source="confirm_endpoint"))
    out, _ = _adopt(svc, locals_=[_local("L1", 101)], live=[])
    assert out == {"status": "line_missing"}
    _no_write(svc)


def test_multi_line_deleted_qbo_line_is_line_missing():
    # kills: the multi-line path reading an absent live line as line_not_recoded
    svc = _svc(_item(confirmed_sub_cost_code_id=None))
    out, _ = _adopt(svc, locals_=[_local("L1", 101), _local("L2", 202)], live=[_live_line("L1", "item-101")])
    assert out == {"status": "line_missing"}
    _no_write(svc)


@pytest.mark.parametrize("customer_ref,live_customer", [("cust-1", "cust-2"), ("cust-1", None), (None, "cust-1")])
def test_customer_mismatch_or_unresolvable_refuses(customer_ref, live_customer):
    # kills: the CustomerRef check removed / an unresolvable customer read as a match (confirmed project)
    svc = _svc(_item(confirmation_source="confirm_endpoint", confirmed_project_id=9))
    out, _ = _adopt(svc, locals_=[_local("L1", 101)],
                    live=[_live_line("L1", "item-101", customer=live_customer)], customer_ref=customer_ref)
    assert out == {"status": "line_not_recoded"}
    _no_write(svc)


@pytest.mark.parametrize("customer_ref,live_customer", [("cust-1", "cust-2"), ("cust-1", None), (None, "cust-1")])
def test_multi_line_customer_mismatch_or_unresolvable_refuses(customer_ref, live_customer):
    # kills: the multi-line CustomerRef check removed
    svc = _svc(_item(confirmed_sub_cost_code_id=None))
    out, _ = _adopt(svc, locals_=[_local("L1", 101, project=9), _local("L2", 202)],
                    live=[_live_line("L1", "item-101", customer=live_customer), _live_line("L2", "item-202")],
                    customer_ref=customer_ref)
    assert out == {"status": "line_not_recoded"}
    _no_write(svc)


def test_multi_line_zero_verifiable_lines_is_line_not_recoded_not_an_empty_record():
    # kills: persisting `[]` when every multi-line local scc is NULL (nothing verifiable)
    svc = _svc(_item(confirmed_sub_cost_code_id=None))
    out, _ = _adopt(svc, locals_=[_local("L1", None), _local("L2", None)], live=[_live_line("L1"), _live_line("L2")])
    assert out == {"status": "line_not_recoded"}
    _no_write(svc)


def test_confirm_endpoint_null_coded_placeholder_line_pre_pull_is_adopted_with_the_confirmed_coding():
    # kills: the local-scc check reinstated (the pre-pull placeholder line has a NULL local scc -> wrongly refused)
    svc = _svc(_item(confirmation_source="confirm_endpoint", confirmed_sub_cost_code_id=303, confirmed_project_id=9))
    out, _ = _adopt(svc, locals_=[_local("L1", None, project=None)],
                    live=[_live_line("L1", "item-303", customer="cust-1")])
    assert out == {"status": "adopted", "lines": 1, "written_sync_token": "LIVE-7"}
    args = svc.repo.adopt_written_record.call_args.args
    assert json.loads(args[2]) == [{"qbo_line_id": "L1", "item_ref_value": "item-303", "sub_cost_code_id": 303}]


def test_confirm_endpoint_human_recode_mirrored_into_the_local_line_is_refused():
    # kills: THE P2 - verifying against the pull-mirrored local line (a human's recode would be adopted as ours)
    svc = _svc(_item(confirmation_source="confirm_endpoint", confirmed_sub_cost_code_id=303))
    out, _ = _adopt(svc, locals_=[_local("L1", 808)], live=[_live_line("L1", "item-808")])
    assert out == {"status": "line_not_recoded"}
    _no_write(svc)


def test_single_line_review_submit_human_recode_is_refused_like_confirm_endpoint():
    # kills: a single-linked-line review_submit falling back to the local coding on zero confirmed matches
    # (the local line mirrors QBO after a pull, so that would adopt a human's recode as ours - the P2)
    svc = _svc(_item(confirmation_source="review_submit", confirmed_sub_cost_code_id=303))
    out, _ = _adopt(svc, locals_=[_local("L1", 808)], live=[_live_line("L1", "item-808")])
    assert out == {"status": "line_not_recoded"}
    _no_write(svc)


def test_single_line_review_submit_adopts_the_confirmed_coding_not_the_local_scc():
    # kills: recording the local scc (808) instead of item.confirmed_sub_cost_code_id
    svc = _svc(_item(confirmation_source="review_submit", confirmed_sub_cost_code_id=303))
    out, _ = _adopt(svc, locals_=[_local("L1", 808)], live=[_live_line("L1", "item-303")])
    assert out["status"] == "adopted"
    assert json.loads(svc.repo.adopt_written_record.call_args.args[2])[0]["sub_cost_code_id"] == 303


def test_two_live_lines_carrying_the_confirmed_pair_is_ambiguous():
    # kills: adopting the first match (or all matches) when the confirmed pair is not unique
    svc = _svc(_item(confirmation_source="confirm_endpoint", confirmed_sub_cost_code_id=303))
    out, _ = _adopt(svc, locals_=[_local("L1", None), _local("L2", None)],
                    live=[_live_line("L1", "item-303"), _live_line("L2", "item-303")])
    assert out == {"status": "ambiguous_lines"}
    _no_write(svc)


def test_confirm_endpoint_with_other_local_lines_adopts_only_the_matching_one():
    # kills: recording every local line instead of the one carrying the confirmed pair
    svc = _svc(_item(confirmation_source="confirm_endpoint", confirmed_sub_cost_code_id=303))
    out, _ = _adopt(svc, locals_=[_local("L1", 111), _local("L2", None)],
                    live=[_live_line("L1", "item-111"), _live_line("L2", "item-303")])
    assert out["status"] == "adopted" and out["lines"] == 1
    assert json.loads(svc.repo.adopt_written_record.call_args.args[2])[0]["qbo_line_id"] == "L2"


@pytest.mark.parametrize("scc", [None, 0])
def test_single_target_without_confirmed_scc_is_no_confirmed_coding(scc):
    # kills: proceeding (and recording scc None) when the confirmed coding is missing
    svc = _svc(_item(confirmation_source="confirm_endpoint", confirmed_sub_cost_code_id=scc))
    out, connector = _adopt(svc, locals_=[_local("L1", 101)], live=[_live_line("L1", "item-101")])
    assert out == {"status": "no_confirmed_coding"}
    connector.get_purchase_raw.assert_not_called()
    _no_write(svc)


def test_multi_line_review_submit_uses_local_coding_and_skips_null_coded_lines():
    # kills: multi-line rows verified against the (single) confirmed coding, or NULL-coded lines refused/recorded
    svc = _svc(_item(confirmed_sub_cost_code_id=999))
    out, _ = _adopt(svc, locals_=[_local("L1", 101), _local("L2", None), _local("L3", 202)],
                    live=[_live_line("L1", "item-101"), _live_line("L2", "item-x"), _live_line("L3", "item-202")])
    assert out["status"] == "adopted" and out["lines"] == 2
    assert [r["qbo_line_id"] for r in json.loads(svc.repo.adopt_written_record.call_args.args[2])] == ["L1", "L3"]


def test_live_purchase_without_sync_token_is_no_live_token_not_the_string_none():
    # kills: str(None) == "None" adopted as the written token
    svc = _svc(_item())
    out, _ = _adopt(svc, token=None, locals_=[_local("L1", 101)], live=[_live_line("L1", "item-101")])
    assert out == {"status": "no_live_token"}
    _no_write(svc)


def test_null_confirmation_seq_is_moved_with_no_get_and_no_repo_call():
    # kills: reaching the sproc THROW (NULL fence) instead of refusing in the service
    svc = _svc(_item(confirmation_seq=None))
    out, connector = _adopt(svc, locals_=[_local("L1", 101)], live=[_live_line("L1", "item-101")])
    assert out == {"status": "moved"}
    connector.get_purchase_raw.assert_not_called()
    _no_write(svc)


def test_holding_code_is_line_holding():
    # kills: HoldingCodeRefused escaping as a 500 / being read as unmapped
    def boom(scc, realm):
        raise HoldingCodeRefused("holding")
    svc = _svc(_item())
    out, _ = _adopt(svc, locals_=[_local("L1", 101)], live=[_live_line("L1")], resolver=boom)
    assert out == {"status": "line_holding"}
    _no_write(svc)


def test_unmapped_code_is_line_unmapped():
    # kills: an unresolvable item ref treated as "no constraint"
    svc = _svc(_item())
    out, _ = _adopt(svc, locals_=[_local("L1", 101)], live=[_live_line("L1")], resolver=lambda s, r: None)
    assert out == {"status": "line_unmapped"}
    _no_write(svc)


def test_fenced_out_persist_is_moved():
    # kills: reporting `adopted` when the sproc matched zero rows (a revision landed after the read)
    svc = _svc(_item(), adopted=None)
    out, _ = _adopt(svc, locals_=[_local("L1", 101)], live=[_live_line("L1", "item-101")])
    assert out == {"status": "moved"}


# ---- route -------------------------------------------------------------------------------------

URL = f"/api/v1/admin/expense-coding/suggestions/{PUBLIC_ID}/adopt-written-record"
ADMIN = {"sub": "a", "is_system_admin": True}


@pytest.fixture
def client():
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.clear()


def _as(user):
    app.dependency_overrides[get_current_user_api] = lambda: user


def test_route_non_admin_is_403(client):
    # kills: require_system_admin() dropped
    _as({"sub": "u", "is_system_admin": False})
    with patch.object(CodingSuggestionService, "adopt_written_record") as adopt:
        assert client.post(URL).status_code == 403
    adopt.assert_not_called()


def test_route_non_uuid_is_422(client):
    # kills: an unvalidated id reaching the lock/sproc
    _as(ADMIN)
    with patch.object(CodingSuggestionService, "adopt_written_record") as adopt:
        r = client.post("/api/v1/admin/expense-coding/suggestions/nope/adopt-written-record")
    assert r.status_code == 422
    adopt.assert_not_called()


def test_route_adopted_is_200_envelope_and_passes_the_legacy_cutoff(client):
    # kills: envelope changed, or the RECORD_REQUIRED_SINCE_UTC cut-off not handed to the service
    _as(ADMIN)
    body = {"status": "adopted", "lines": 1, "written_sync_token": "T"}
    with patch.object(CodingSuggestionService, "adopt_written_record", return_value=body) as adopt:
        r = client.post(URL)
    assert r.status_code == 200 and r.json() == {"data": body}
    adopt.assert_called_once_with(PUBLIC_ID, record_required_since=RECORD_REQUIRED_SINCE_UTC)


def test_route_not_found_is_404(client):
    # kills: not_found folded into 409
    _as(ADMIN)
    with patch.object(CodingSuggestionService, "adopt_written_record", return_value={"status": "not_found"}):
        assert client.post(URL).status_code == 404


@pytest.mark.parametrize("status", ["writes_disabled", "busy", "not_written", "has_record", "not_a_first_write",
                                    "legacy", "line_holding", "line_unmapped", "line_not_recoded", "moved",
                                    "no_confirmed_coding", "no_live_token", "line_missing", "ambiguous_lines"])
def test_route_refusals_are_409_with_status_detail(client, status):
    # kills: a refusal returned as 200 or a lost status in `detail`
    _as(ADMIN)
    with patch.object(CodingSuggestionService, "adopt_written_record", return_value={"status": status}):
        r = client.post(URL)
    assert r.status_code == 409 and r.json()["detail"] == status


# ---- SQL text ----------------------------------------------------------------------------------

SQL = (Path(__file__).resolve().parent.parent / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql").read_text()


def _body():
    for b in re.split(r"^GO\s*$", SQL, flags=re.M):
        if re.search(r"CREATE OR ALTER PROCEDURE\s+AdoptCodingSuggestionWrittenRecord\b", b):
            return b
    raise AssertionError("AdoptCodingSuggestionWrittenRecord not found")


def test_sproc_batch_shape():
    # kills: a second CREATE in the batch, a missing GO terminator, a lost NOCOUNT, a ROLLBACK
    b = _body()
    assert len(re.findall(r"CREATE OR ALTER", b)) == 1
    assert "SET NOCOUNT ON;" in b and "ROLLBACK" not in b
    assert re.search(r"^GO\s*$", SQL[SQL.index("AdoptCodingSuggestionWrittenRecord"):], flags=re.M)


def test_sproc_params_are_required_not_defaulted():
    # kills: a `= NULL` default (the CASE-preserve family that made the record-less `written`)
    b = _body()
    params = b[b.index("(") : b.index(")\nAS")]
    for decl in ("@PublicId UNIQUEIDENTIFIER", "@WrittenSyncToken NVARCHAR(50)",
                 "@WrittenLinesJson NVARCHAR(MAX)", "@ExpectedConfirmationSeq INT"):
        assert re.search(re.escape(decl) + r"\s*(,|$)", params, flags=re.M), decl
    assert "= NULL" not in params
    assert re.search(r"IF @WrittenSyncToken IS NULL OR @WrittenLinesJson IS NULL OR @ExpectedConfirmationSeq IS NULL\s+THROW", b)


def test_sproc_where_is_exactly_the_four_guards_and_set_is_unconditional():
    # kills: any guard dropped (overwrite of a real record / a revised row), a tautology, CASE-preserve SET
    b = _body()
    upd = b[b.index("UPDATE cs"):]
    where = re.search(r"\bWHERE\b(.*?);", upd, flags=re.S).group(1)
    preds = [p.strip() for p in re.split(r"\bAND\b", re.sub(r"\s+", " ", where))]
    assert preds == [
        "cs.[PublicId] = @PublicId",
        "cs.[Status] = N'written'",
        "cs.[WrittenSyncToken] IS NULL",
        "cs.[WrittenLinesJson] IS NULL",
        "cs.[ConfirmationSeq] = @ExpectedConfirmationSeq",
    ]
    assert not re.search(r"\bOR\b|1\s*=\s*1", where)
    set_clause = re.search(r"\bSET\b(.*?)\bFROM\b", upd, flags=re.S).group(1)
    assert "CASE" not in set_clause
    assert "[WrittenSyncToken] = @WrittenSyncToken" in set_clause and "[WrittenLinesJson] = @WrittenLinesJson" in set_clause
    assert "[Status]" not in set_clause and "[WrittenAt]" not in set_clause and "[ConfirmationSeq]" not in set_clause


def test_review_submit_confirmed_pair_on_one_of_two_linked_lines_adopts_only_that_line():
    # kills: multi-line branch taken for a 2-linked-line review_submit (would record both lines, over-claiming L2)
    svc = _svc(_item(confirmation_source="review_submit", confirmed_sub_cost_code_id=303))
    out, _ = _adopt(svc, locals_=[_local("L1", 303), _local("L2", 202)],
                    live=[_live_line("L1", "item-303"), _live_line("L2", "item-202")])
    assert out == {"status": "adopted", "lines": 1, "written_sync_token": "LIVE-7"}
    assert json.loads(svc.repo.adopt_written_record.call_args.args[2]) == [
        {"qbo_line_id": "L1", "item_ref_value": "item-303", "sub_cost_code_id": 303}]


def test_review_submit_confirmed_pair_on_two_linked_lines_is_ambiguous():
    # kills: review_submit with a confirmed scc skipping the ambiguity refusal (multi-line fallthrough on >1 match)
    svc = _svc(_item(confirmation_source="review_submit", confirmed_sub_cost_code_id=303))
    out, _ = _adopt(svc, locals_=[_local("L1", 303), _local("L2", 303)],
                    live=[_live_line("L1", "item-303"), _live_line("L2", "item-303")])
    assert out == {"status": "ambiguous_lines"}
    _no_write(svc)


def test_review_submit_zero_confirmed_matches_falls_back_to_multi_line_local_coding():
    # kills: the fallback removed (a review_submit whose confirmed pair is on no live line refused outright)
    svc = _svc(_item(confirmation_source="review_submit", confirmed_sub_cost_code_id=999))
    out, _ = _adopt(svc, locals_=[_local("L1", 101), _local("L2", 202)],
                    live=[_live_line("L1", "item-101"), _live_line("L2", "item-202")])
    assert out["status"] == "adopted" and out["lines"] == 2
    assert [r["qbo_line_id"] for r in json.loads(svc.repo.adopt_written_record.call_args.args[2])] == ["L1", "L2"]


@pytest.mark.parametrize("scc", [None, 0])
def test_review_submit_without_confirmed_scc_goes_straight_to_multi_line_mode(scc):
    # kills: no_confirmed_coding returned for review_submit (it applies to confirm_endpoint only)
    svc = _svc(_item(confirmation_source="review_submit", confirmed_sub_cost_code_id=scc))
    out, _ = _adopt(svc, locals_=[_local("L1", 101)], live=[_live_line("L1", "item-101")])
    assert out["status"] == "adopted" and out["lines"] == 1
