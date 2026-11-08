"""U-551b-f1-f3b PREVENT: a first-write retry whose write already landed carries its record.

Connector emits ``verified_lines`` only under the +1 / CustomerRef rules; the worker REPLACES the record and
adopts the live token. Every client / repo is mocked; nothing opens a connection."""
import json

from tests.test_u551bf1_repoint_connector import CUST, _item_line, _raw, _run, _spec
from tests.test_u551bf1_repoint_worker import _item, _run as _worker_run

REC = {"qbo_line_id": "1", "item_ref_value": "item-42", "sub_cost_code_id": 101}


def _ours(line_id="1", customer="cust-99"):
    line = _item_line(line_id, item="item-42")
    line["ItemBasedExpenseLineDetail"]["CustomerRef"] = {"value": customer, "name": "c"}
    return line


# ---- connector ---------------------------------------------------------------------------------

def test_connector_plus_one_with_matching_customer_emits_record_and_live_token():
    # kills: the emit gate staying `is_repoint_call`-only (no record -> the crash gap stays record-less)
    res, client = _run(_raw([_ours()], token="6"), [_spec()])  # expected "5", live "6"
    assert res == {"status": "already_recoded", "sync_token": "6", "verified_lines": [REC]}
    client.update_purchase_raw.assert_not_called()


def test_connector_live_equal_to_expected_emits_nothing():
    # kills: dropping the +1 rule down to "any token" (nothing of ours landed: token unchanged)
    res, _ = _run(_raw([_ours()], token="5"), [_spec()])
    assert res["status"] == "already_recoded" and "verified_lines" not in res


def test_connector_live_plus_two_emits_nothing():
    # kills: `>=` / "token changed" instead of exactly +1 (a human's second edit would be adopted)
    res, _ = _run(_raw([_ours()], token="7"), [_spec()])
    assert "verified_lines" not in res


def test_connector_customer_mismatch_with_a_project_emits_nothing():
    # kills: the CustomerRef check removed (a human coding the same item for another customer adopted)
    res, _ = _run(_raw([_ours(customer="cust-55")], token="6"), [_spec()])
    assert res["status"] == "already_recoded" and "verified_lines" not in res


def test_connector_unresolvable_customer_for_a_project_emits_nothing():
    # kills: unresolvable CustomerRef treated as a match (fail-open) — the guard inverted
    from tests.test_u551bf1_repoint_connector import _connector, _patch, PID, REALM
    patched, client = _patch(_raw([_ours()], token="6"))  # live == expected + 1, line already on target
    c = _connector()
    c._get_qbo_customer_ref.return_value = None  # project's customer cannot be resolved
    with patched:
        res = c.recode_purchase_lines(realm_id=REALM, qbo_purchase_qbo_id=PID, lines=[_spec()], expected_sync_token="5")
    assert res["status"] == "already_recoded" and "verified_lines" not in res
    client.update_purchase_raw.assert_not_called()


def test_connector_one_bad_customer_among_two_specs_emits_nothing():
    # kills: last-writer-wins on the provable flag (bad spec FIRST, good spec after resets it to True)
    res, _ = _run(_raw([_ours("1", customer="cust-55"), _ours("2")], token="6"), [_spec("1"), _spec("2")])
    assert "verified_lines" not in res


def test_connector_one_bad_customer_in_the_last_spec_emits_nothing():
    # kills: checking only the first spec
    res, _ = _run(_raw([_ours("1"), _ours("2", customer="cust-55")], token="6"), [_spec("1"), _spec("2")])
    assert "verified_lines" not in res


def test_connector_failing_token_rule_does_no_customer_reads():
    # kills: the token rule evaluated AFTER the per-spec CustomerRef lookups (wasted project reads)
    from tests.test_u551bf1_repoint_connector import MagicMock, PID, REALM, _connector, _patch
    patched, _ = _patch(_raw([_ours()], token="7"))  # +2: rule fails
    c = _connector()
    with patched:
        res = c.recode_purchase_lines(realm_id=REALM, qbo_purchase_qbo_id=PID, lines=[_spec()], expected_sync_token="5")
    assert "verified_lines" not in res
    c._get_qbo_customer_ref.assert_not_called()


def test_connector_unparseable_expected_token_emits_nothing():
    # kills: int() blowing up / treating "" as a match when the item has no snapshot token
    res, _ = _run(_raw([_ours()], token="6"), [_spec()], token="")
    assert res["status"] == "already_recoded" and "verified_lines" not in res


def test_connector_repoint_path_unchanged_still_fenced_and_verified():
    # kills: the new emit rule leaking into / replacing the repoint path (its fence is token == expected)
    res, _ = _run(_raw([_ours()], token="5"), [_spec(repoint_from="item-77")])
    assert res["verified_lines"] == [REC] and res["sync_token"] == "5"


def test_connector_mixed_placeholder_still_posts_not_early_returned():
    # kills: an early `already_recoded` return before pending_specs (a placeholder sibling would be skipped)
    from tests.test_u551bf1_repoint_connector import _placeholder
    res, client = _run(_raw([_placeholder("1"), _ours("2")], token="5"), [_spec("1"), _spec("2")])
    assert res["status"] == "written"
    client.update_purchase_raw.assert_called_once()


# ---- worker ------------------------------------------------------------------------------------

def test_worker_first_write_already_recoded_with_record_marks_with_token_and_replaces():
    # kills: merging into the old record / leaving sync_token None (the record-less `written` the unit fixes)
    stale = [{"qbo_line_id": "OLD", "item_ref_value": "item-A", "sub_cost_code_id": 5}]
    svc, _, _ = _worker_run(
        _item(source="review_submit", written_lines=stale, targets=None),
        connector_result={"status": "already_recoded", "sync_token": "6", "verified_lines": [REC]})
    kw = svc.mark_written.call_args.kwargs
    assert kw["sync_token"] == "6" and json.loads(kw["written_lines_json"]) == [REC]


def test_worker_first_write_already_recoded_without_record_marks_none_none():
    # kills: adopting an empty / fabricated record (or the staging token) when the connector proved nothing
    svc, _, _ = _worker_run(
        _item(source="review_submit", written_lines=None, targets=None),
        connector_result={"status": "already_recoded", "sync_token": "6"})
    kw = svc.mark_written.call_args.kwargs
    assert kw["sync_token"] is None and kw["written_lines_json"] is None


def test_worker_first_write_already_recoded_with_empty_verified_list_marks_none_none():
    # kills: `is not None` instead of truthiness (an empty list would persist an explicit empty record)
    svc, _, _ = _worker_run(
        _item(source="review_submit", written_lines=None, targets=None),
        connector_result={"status": "already_recoded", "sync_token": "6", "verified_lines": []})
    kw = svc.mark_written.call_args.kwargs
    assert kw["sync_token"] is None and kw["written_lines_json"] is None
