"""U-778a bite 2: charge-link backfill service (fakes only; no DB, no QBO)."""
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.base.charge_link import MissingChargeLine
from integrations.intuit.qbo.bill.external.schemas import QboBillLine
from integrations.intuit.qbo.charge_link.business.backfill import QboChargeLinkBackfillService


def _line(lid, charge=None, extra=None):
    d = {"Id": lid, "Amount": 1, "DetailType": "AccountBasedExpenseLineDetail"}
    links = []
    if charge:
        links.append({"TxnId": charge, "TxnType": "ReimburseCharge"})
    for x in extra or []:
        links.append({"TxnId": x, "TxnType": "ReimburseCharge"})
    if links:
        d["LinkedTxn"] = links
    return QboBillLine(**d)


def _hdr(hid, *lines):
    return SimpleNamespace(id=str(hid), line=list(lines))


class FakeClient:
    def __init__(self, headers=None, boom=None):
        self.headers = {h.id: h for h in (headers or [])}
        self.batches = []
        self.boom = boom

    def _q(self, ids):
        self.batches.append(list(ids))
        if self.boom:
            raise self.boom
        return [self.headers[i] for i in ids if i in self.headers]

    query_bills_by_ids = query_purchases_by_ids = query_vendor_credits_by_ids = _q


class FakeRepo:
    def __init__(self, rows=(), current=(), boom=None, forbid_write=False):
        self.rows = list(rows)
        self.current = set(current)  # line pks already holding the right value
        self.writes = []
        self.boom = boom
        self.forbid_write = forbid_write

    def read_lines_missing_reimburse_charge_by_realm_id(self, realm_id):
        assert realm_id == "R"
        return list(self.rows)

    def _w(self, id, rc):
        if self.forbid_write:
            raise AssertionError("write in dry-run")
        if self.boom:
            raise self.boom
        self.writes.append((id, rc))
        return id not in self.current

    update_reimburse_charge_by_id = update_line_reimburse_charge_by_id = _w


def _svc(bill_client=None, bill_repo=None, purchase_client=None, purchase_repo=None, vc_client=None, vc_repo=None):
    return QboChargeLinkBackfillService(
        "R",
        bill_client=bill_client or FakeClient(),
        purchase_client=purchase_client or FakeClient(),
        vendor_credit_client=vc_client or FakeClient(),
        bill_line_repo=bill_repo or FakeRepo(),
        purchase_line_repo=purchase_repo or FakeRepo(),
        vendor_credit_repo=vc_repo or FakeRepo(),
    )


def _rows(*triples):
    return [MissingChargeLine(*t) for t in triples]


def test_dry_run_writes_nothing():
    repo = FakeRepo(_rows((1, "a", "10")), forbid_write=True)
    out = _svc(bill_client=FakeClient([_hdr(10, _line("a", "68056"))]), bill_repo=repo).run(entities=("bill",))
    assert out["entities"]["bill"]["would_write"] == 1 and out["entities"]["bill"]["written"] == 0
    assert repo.writes == [] and out["aborted"] is None


def test_apply_writes_only_lines_with_link_and_counts():
    repo = FakeRepo(
        _rows((1, "a", "10"), (2, "b", "10"), (3, "c", "10"), (4, "d", "10"), (5, "e", "10"), (6, "f", "11")),
        current={2},
    )
    client = FakeClient([_hdr(10, _line("a", "1"), _line("b", "2"), _line("c"), _line("d", "3", extra=["4"]))])
    out = _svc(bill_client=client, bill_repo=repo).run(entities=("bill",), dry_run=False)
    c = out["entities"]["bill"]
    assert repo.writes == [(1, "1"), (2, "2")]
    assert (c["written"], c["not_written"], c["no_link_live"], c["ambiguous"], c["line_gone"], c["header_gone"]) == (
        1, 1, 1, 1, 1, 1)


def test_batches_of_100_headers():
    rows = _rows(*[(i, "a", str(i)) for i in range(1, 102)])
    client = FakeClient([_hdr(i, _line("a", "9")) for i in range(1, 102)])
    out = _svc(bill_client=client, bill_repo=FakeRepo(rows)).run(entities=("bill",), dry_run=False)
    assert [len(b) for b in client.batches] == [100, 1]
    assert out["entities"]["bill"]["written"] == 101


def test_resume_from_skips_headers_at_or_below_numerically():
    rows = _rows((1, "a", "9"), (2, "a", "10"), (3, "a", "11"))
    client = FakeClient([_hdr(i, _line("a", "5")) for i in (9, 10, 11)])
    repo = FakeRepo(rows)
    _svc(bill_client=client, bill_repo=repo).run(entities=("bill",), dry_run=False, resume_from="9")
    assert client.batches == [["10", "11"]]
    assert [w[0] for w in repo.writes] == [2, 3]


def test_max_batches_stops_cleanly_with_resume_from():
    rows = _rows(*[(i, "a", str(i)) for i in range(1, 251)])
    client = FakeClient([_hdr(i, _line("a", "9")) for i in range(1, 251)])
    out = _svc(bill_client=client, bill_repo=FakeRepo(rows)).run(entities=("bill",), dry_run=False, max_batches=2)
    assert len(client.batches) == 2 and out["resume_from"] == "200" and out["aborted"] is None
    assert out["stopped"] == "max_batches"


def test_client_exception_aborts_whole_run():
    purchase_client = FakeClient()
    out = _svc(
        bill_client=FakeClient(boom=RuntimeError("x")),
        bill_repo=FakeRepo(_rows((1, "a", "10"))),
        purchase_client=purchase_client,
        purchase_repo=FakeRepo(_rows((2, "a", "20"))),
    ).run(entities=("bill", "purchase"), dry_run=False)
    assert out["aborted"] == "RuntimeError" and out["resume_from"] is None
    assert purchase_client.batches == []


def test_client_exception_on_second_batch_reports_resume_from():
    rows = _rows(*[(i, "a", str(i)) for i in range(1, 102)])
    class Flaky(FakeClient):
        def _q(self, ids):
            if len(self.batches) == 1:
                self.batches.append(ids)
                raise ConnectionError("down")
            return super()._q(ids)
        query_bills_by_ids = _q
    client = Flaky([_hdr(i, _line("a", "9")) for i in range(1, 102)])
    out = _svc(bill_client=client, bill_repo=FakeRepo(rows)).run(entities=("bill",), dry_run=False)
    assert out["aborted"] == "ConnectionError" and out["resume_from"] == "100"


def test_repo_write_exception_aborts_whole_run():
    purchase_client = FakeClient()
    out = _svc(
        bill_client=FakeClient([_hdr(10, _line("a", "1"), _line("b", "2"))]),
        bill_repo=FakeRepo(_rows((1, "a", "10"), (2, "b", "10")), boom=ValueError("db")),
        purchase_client=purchase_client,
        purchase_repo=FakeRepo(_rows((3, "a", "20"))),
    ).run(entities=("bill", "purchase"), dry_run=False)
    assert out["aborted"] == "ValueError" and out["resume_from"] is None
    assert purchase_client.batches == []


def test_all_three_entities_run_in_order():
    out = _svc(
        bill_client=FakeClient([_hdr(1, _line("a", "1"))]), bill_repo=FakeRepo(_rows((1, "a", "1"))),
        purchase_client=FakeClient([_hdr(2, _line("a", "2"))]), purchase_repo=FakeRepo(_rows((2, "a", "2"))),
        vc_client=FakeClient([_hdr(3, _line("a", "3"))]), vc_repo=FakeRepo(_rows((3, "a", "3"))),
    ).run(dry_run=False)
    assert [out["entities"][k]["written"] for k in ("bill", "purchase", "vendor_credit")] == [1, 1, 1]


def test_f4_resume_from_requires_exactly_one_entity():
    client = FakeClient([_hdr(10, _line("a", "1"))])
    for ents in (("bill", "purchase"), ("bill", "purchase", "vendor_credit"), ()):
        with pytest.raises(ValueError):
            _svc(bill_client=client, bill_repo=FakeRepo(_rows((1, "a", "10")))).run(
                entities=ents, dry_run=False, resume_from="9")
    assert client.batches == []  # raised before any read/fetch


def test_f4_single_entity_resume_skips_only_that_entitys_headers():
    bill_client = FakeClient([_hdr(i, _line("a", "5")) for i in (8, 9, 10, 11)])
    bill_repo = FakeRepo(_rows((1, "a", "8"), (2, "a", "9"), (3, "a", "10"), (4, "a", "11")))
    purchase_client = FakeClient([_hdr(3, _line("a", "5"))])
    purchase_repo = FakeRepo(_rows((9, "a", "3")))
    out = _svc(bill_client=bill_client, bill_repo=bill_repo, purchase_client=purchase_client,
               purchase_repo=purchase_repo).run(entities=("bill",), dry_run=False, resume_from="9")
    assert bill_client.batches == [["10", "11"]] and [w[0] for w in bill_repo.writes] == [3, 4]
    assert purchase_client.batches == [] and purchase_repo.writes == []
    # an unrelated entity with the same low header ids is NOT skipped when resumed on its own
    out = _svc(purchase_client=purchase_client, purchase_repo=purchase_repo).run(
        entities=("purchase",), dry_run=False, resume_from="2")
    assert purchase_client.batches == [["3"]] and out["entities"]["purchase"]["written"] == 1


def test_f4_cli_refuses_resume_from_with_entity_all(monkeypatch, capsys):
    import scripts.backfill_u778a_charge_links as cli

    def _boom(*a, **k):
        raise AssertionError("CLI did real work before validating its arguments")

    monkeypatch.setattr(cli, "assert_cli_system_admin", _boom)
    monkeypatch.setattr(cli, "QboAuthService", _boom)
    assert cli.main(["--resume-from", "5"]) != 0
    assert cli.main(["--entity", "all", "--resume-from", "5", "--apply"]) != 0
    assert "--resume-from" in capsys.readouterr().err


def test_f7_checkpoint_is_per_completed_batch_not_per_written_line():
    """Batch 2's first line is written, a later line's write raises: resume_from is the LAST header of batch 1."""
    rows = _rows(*[(i, "a", str(i)) for i in range(1, 101)], (1001, "a", "101"), (1002, "b", "101"))

    class WriterBoomsOnSecondLine(FakeRepo):
        def _w(self, id, rc):
            if id == 1002:
                raise ValueError("db down")
            return super()._w(id, rc)

        update_reimburse_charge_by_id = _w

    repo = WriterBoomsOnSecondLine(rows)
    client = FakeClient([_hdr(i, _line("a", "9")) for i in range(1, 101)] + [_hdr(101, _line("a", "9"), _line("b", "9"))])
    out = _svc(bill_client=client, bill_repo=repo).run(entities=("bill",), dry_run=False)
    assert out["aborted"] == "ValueError"
    assert (1001, "9") in repo.writes  # batch 2's first line really was written before the failure
    assert out["resume_from"] == "100"
