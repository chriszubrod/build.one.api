"""U-535b b1: AttachmentBackfillService orchestration (one shared fake repo, synthetic data, no live services)."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import inspect
import io
import logging
import re
from decimal import Decimal
from pathlib import Path

import pytest

import entities.attachment_backfill.business.service as service_mod
from entities.attachment_backfill.business.matcher import BillRow, InventoryFile
from entities.attachment_backfill.business.project_map import ProjectRef
from entities.attachment_backfill.business.service import (
    AttachmentBackfillService,
    default_ocr,
    default_text_layer,
    first_pages,
)

PACKAGE = Path(service_mod.__file__).resolve().parents[1]
REPO_ROOT = PACKAGE.parents[1]
SCRIPT = REPO_ROOT / "scripts" / "attachment_backfill_candidates.py"
SQL_FILE = PACKAGE / "sql" / "dbo.attachment_backfill.sql"
SECRET = "SECRET-PDF-TEXT-7781"
VERDICT_WORDS = ("verified", "mismatch", "partial", "unverifiable", "verdict")
LONG = "ACME LUMBER COMPANY remittance copy page one of one "
LAYER = LONG + "Invoice 1042 Total due 1,234.50"
PROJECTS = [ProjectRef(id=10, name="ABC1 - 1200 Elm Street")]


def NO_OCR(content):
    raise AssertionError("OCR must not run here")


class FakeConn:
    def __init__(self, log):
        self.log = log

    def commit(self):
        self.log.append("commit")


class FakeRepo:
    """Superset fake: generation, check, stale prune, connection lifecycle. Records every call in order."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.inserted: list[dict] = []
        self.updates: list[dict] = []
        self.log: list[str] = []
        self.refuse_ids: set[int] = set()
        self.connections = 0
        self.pruned_keep = None

    @contextlib.contextmanager
    def connection(self):
        self.connections += 1
        self.log.append("open")
        try:
            yield FakeConn(self.log)
        finally:
            self.log.append("close")

    def insert_candidate(self, *, conn=None, **kw):
        assert conn is not None, "insert must join the loop's connection"
        self.log.append(f"insert:{kw['source_file_id']}")
        existing = next((r for r in self.inserted if r["source_file_id"] == kw["source_file_id"]), None)
        if existing:
            return {"Id": existing["Id"], "Inserted": False}
        row = dict(kw, Id=len(self.inserted) + 1)
        self.inserted.append(row)
        return {"Id": row["Id"], "Inserted": True}

    def delete_stale_candidates(self, *, run_id, keep, conn=None):
        assert conn is not None
        self.log.append("delete_stale")
        self.pruned_keep = (run_id, sorted(keep))
        return 2

    def read_candidates_for_verification(self, *, run_id, top, after_id=0, include_errors=False):
        return [r for r in self.rows if r["Id"] > after_id][:top]

    def update_candidate_verification(self, *, conn=None, **kw):
        assert conn is not None, "check writes must join the batch's connection"
        self.log.append(f"update:{kw['candidate_id']}")
        self.updates.append(kw)
        if kw["candidate_id"] in self.refuse_ids:
            return {"Id": None, "RefusedReason": "reviewed_or_linked"}
        return {"Id": kw["candidate_id"]}


def _row(i, number="1042", amount="1234.50", size=1000):
    return {"Id": i, "SourceFileId": f"file{i}", "SourceFileSize": size, "BillNumber": number,
            "TotalAmount": Decimal(amount), "VendorName": "Acme Lumber"}


def _check(repo, **kw):
    kw.setdefault("ocr", NO_OCR)
    return AttachmentBackfillService(repo=repo).verify_candidates(5, **kw)


@pytest.fixture
def forbidden(monkeypatch):
    """Every link / attachment / blob / outbox write path and the DB itself record or explode."""
    calls: list[str] = []

    def recorder(label):
        def _hit(*a, **k):
            calls.append(label)
            raise AssertionError(f"forbidden write: {label}")

        return _hit

    import shared.database as db

    monkeypatch.setattr(db, "_connect", recorder("db.connect"))
    from entities.attachment.persistence import repo as attachment_repo
    from entities.bill_line_item_attachment.persistence.repo import BillLineItemAttachmentRepository
    from integrations.box.outbox.business.service import BoxOutboxService
    from integrations.intuit.qbo.outbox.business.service import QboOutboxService
    from integrations.ms.outbox.business.service import MsOutboxService
    from shared.storage import AzureBlobStorage

    for cls in (BillLineItemAttachmentRepository, attachment_repo.AttachmentRepository):
        for name, _ in inspect.getmembers(cls, inspect.isfunction):
            if not name.startswith("__"):
                monkeypatch.setattr(cls, name, recorder(f"{cls.__name__}.{name}"))
    for cls in (BoxOutboxService, QboOutboxService, MsOutboxService):
        for name, _ in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("enqueue"):
                monkeypatch.setattr(cls, name, recorder(f"{cls.__name__}.{name}"))
    monkeypatch.setattr(AzureBlobStorage, "upload_file", recorder("AzureBlobStorage.upload_file"))
    return calls


# -- generation ---------------------------------------------------------------------- #


def _inv(file_id="f1", name="ABC1 - Acme - 42 - misc - $5.00 - 1-2-2024.pdf"):
    return InventoryFile(file_id, name, "fold", "ABC1 - 1200 Elm Street")


BILLS = [BillRow(id=7, bill_number="42", total_amount=Decimal("5.00"), project_ids=frozenset({10}))]


def test_generate_inserts_only_candidates_and_is_idempotent(forbidden):
    repo = FakeRepo()
    svc = AttachmentBackfillService(repo=repo)
    inventory = [_inv(), _inv("f2", "junk.pdf")]
    counts = svc.generate_box_candidates(99, inventory, BILLS, PROJECTS)
    assert counts["candidates"] == 1 and counts["inserted"] == 1 and counts["unparsed"] == 1
    assert counts["pruned_stale"] == 0 and "delete_stale" not in repo.log
    assert repo.inserted[0]["bill_id"] == 7 and repo.inserted[0]["match_tier"] == "A"
    again = svc.generate_box_candidates(99, inventory, BILLS, PROJECTS)
    assert again["inserted"] == 0 and again["already_present"] == 1
    assert forbidden == []


def test_generate_uses_one_connection_and_commits_after_every_row(forbidden):
    repo = FakeRepo()
    bills = BILLS + [BillRow(id=8, bill_number="43", total_amount=Decimal("6.00"), project_ids=frozenset({10}))]
    inventory = [_inv("f1"), _inv("f2", "ABC1 - Acme - 43 - misc - $6.00 - 1-2-2024.pdf")]
    AttachmentBackfillService(repo=repo).generate_box_candidates(1, inventory, bills, PROJECTS)
    assert repo.connections == 1
    assert repo.log == ["open", "insert:f1", "commit", "insert:f2", "commit", "close"]


def test_resume_prunes_stale_candidates_before_inserting(forbidden):
    repo = FakeRepo()
    counts = AttachmentBackfillService(repo=repo).generate_box_candidates(
        7, [_inv("a")], BILLS, PROJECTS, prune_stale=True
    )
    assert repo.pruned_keep == (7, [("a", 7)])
    assert repo.log == ["open", "delete_stale", "commit", "insert:a", "commit", "close"]
    assert counts["pruned_stale"] == 2


def test_refused_insert_counted_by_reason(forbidden):
    class Refusing(FakeRepo):
        def insert_candidate(self, *, conn=None, **kw):
            return {"Id": None, "Inserted": False, "RefusedReason": "previously_rejected"}

    counts = AttachmentBackfillService(repo=Refusing()).generate_box_candidates(1, [_inv()], BILLS, PROJECTS)
    assert counts["refused_previously_rejected"] == 1 and counts["inserted"] == 0


def test_plan_writes_nothing(forbidden):
    repo = FakeRepo()
    plan = AttachmentBackfillService(repo=repo).plan_box_candidates([], [], [])
    assert plan.counts()["candidates"] == 0 and repo.inserted == [] and repo.log == [] and forbidden == []


# -- per-PDF check -------------------------------------------------------------------- #


def test_check_records_sha_status_and_evidence_and_never_text(forbidden):
    repo = FakeRepo(rows=[_row(1), _row(2, number="999", amount="1.00")])
    blobs = {"file1": b"%PDF-one", "file2": b"%PDF-two"}
    counts = _check(
        repo,
        fetch_bytes=lambda fid: blobs[fid],
        text_layer=lambda content: f"{SECRET} Invoice 1042 total 1,234.50",
        ocr=lambda content: SECRET,
        batch=1,
        workers=2,
    )
    by_id = {u["candidate_id"]: u for u in repo.updates}
    assert by_id[1]["check_status"] == "checked" and by_id[1]["used_ocr"] is False
    assert by_id[1]["invoice_seen_count"] == 1 and by_id[1]["amount_seen_count"] == 1
    assert by_id[1]["source_sha256"] == hashlib.sha256(b"%PDF-one").hexdigest()
    assert by_id[2]["check_status"] == "checked"
    assert by_id[2]["invoice_seen_count"] == 0 and by_id[2]["amount_seen_count"] == 0
    assert counts["processed"] == 2 and counts["checked"] == 2
    assert counts["invoice_seen_1plus"] == 1 and counts["invoice_seen_0"] == 1
    assert SECRET not in repr(repo.updates) and SECRET not in repr(counts)
    assert forbidden == []


def test_statuses_evidence_and_no_verdict_words(forbidden):
    texts = {b"1": LONG + "Inv. No. 2002 Ref 1001 Total 1,234.50", b"2": "", b"3": LONG,
             b"5": LONG + "STATEMENT OF ACCOUNT Invoice # 1001 1,234.50\nInvoice # 1002 5.00\nInvoice # 1003 6.00"}
    rows = [_row(i, number="1001") for i in (1, 2, 3, 5)] + [_row(4, number="1001", size=30 * 1024 * 1024)]
    repo = FakeRepo(rows=sorted(rows, key=lambda r: r["Id"]))
    fetched = []

    def fetch(fid):
        fetched.append(fid)
        if fid == "file3":
            raise RuntimeError("boom")
        return fid[-1].encode()

    counts = _check(repo, fetch_bytes=fetch, text_layer=lambda c: texts[c], ocr=lambda c: "")
    by_id = {u["candidate_id"]: u for u in repo.updates}
    assert by_id[1]["check_status"] == "checked"
    assert by_id[1]["invoice_seen_count"] == 1 and by_id[1]["other_invoice_label_count"] == 1
    assert by_id[1]["amount_seen_count"] == 1 and by_id[1]["vendor_seen"] is True
    assert by_id[2]["check_status"] == "unreadable" and by_id[2]["invoice_seen_count"] is None
    assert by_id[3]["check_status"] == "error"
    assert by_id[4]["check_status"] == "unreadable" and by_id[4]["source_sha256"] is None
    assert "file4" not in fetched  # oversize: no download
    assert by_id[5]["statement_like"] is True and by_id[5]["other_invoice_label_count"] == 2
    keys = " ".join(counts).lower() + " ".join(k for u in repo.updates for k in u).lower()
    for word in VERDICT_WORDS:
        assert word not in keys, word
    assert counts["checked"] == 2 and counts["unreadable"] == 2 and counts["error"] == 1
    assert counts["statement_like"] == 1 and counts["other_label_1plus"] == 2


def test_check_uses_one_connection_per_batch_and_commits_after_every_row(forbidden):
    repo = FakeRepo(rows=[_row(i) for i in (1, 2, 3)])
    _check(repo, fetch_bytes=lambda f: b"x", text_layer=lambda c: LAYER, batch=2)
    assert repo.connections == 2
    assert repo.log == ["open", "update:1", "commit", "update:2", "commit", "close",
                        "open", "update:3", "commit", "close"]


def test_per_candidate_error_becomes_error_status_and_logs_type_only(forbidden, caplog):
    repo = FakeRepo(rows=[_row(1), _row(2)])

    def fetch(fid):
        if fid == "file1":
            raise RuntimeError(f"boom {SECRET}")
        return b"%PDF-ok"

    with caplog.at_level(logging.WARNING):
        counts = _check(repo, fetch_bytes=fetch, text_layer=lambda c: LAYER)
    by_id = {u["candidate_id"]: u for u in repo.updates}
    assert by_id[1]["check_status"] == "error" and by_id[1]["source_sha256"] is None
    assert by_id[2]["check_status"] == "checked"
    assert counts["error"] == 1 and counts["checked"] == 1
    assert "RuntimeError" in caplog.text and SECRET not in caplog.text


def test_extract_failure_keeps_sha_and_marks_error(forbidden):
    repo = FakeRepo(rows=[_row(1)])

    def bad_extract(content):
        raise ValueError("bad pdf")

    _check(repo, fetch_bytes=lambda f: b"x", text_layer=bad_extract)
    assert repo.updates[0]["check_status"] == "error"
    assert repo.updates[0]["source_sha256"] == hashlib.sha256(b"x").hexdigest()


def test_refused_update_counted_separately(forbidden):
    repo = FakeRepo(rows=[_row(1)])
    repo.refuse_ids = {1}
    counts = _check(repo, fetch_bytes=lambda f: b"x", text_layer=lambda c: LAYER)
    assert counts["refused"] == 1 and counts["checked"] == 0


def test_limit_and_keyset_terminate(forbidden):
    repo = FakeRepo(rows=[_row(i) for i in range(1, 8)])
    counts = _check(repo, fetch_bytes=lambda f: b"x", text_layer=lambda c: "", ocr=lambda c: "", batch=3, limit=5)
    assert counts["processed"] == 5 and [u["candidate_id"] for u in repo.updates] == [1, 2, 3, 4, 5]


def test_keyboard_interrupt_stops_promptly_and_leaves_unwritten_rows_unchecked(forbidden, monkeypatch):
    repo = FakeRepo(rows=[_row(i) for i in range(1, 6)])
    shutdowns = []
    real_shutdown = service_mod.ThreadPoolExecutor.shutdown

    def spy_shutdown(self, wait=True, *, cancel_futures=False):
        shutdowns.append((wait, cancel_futures))
        return real_shutdown(self, wait=wait, cancel_futures=cancel_futures)

    monkeypatch.setattr(service_mod.ThreadPoolExecutor, "shutdown", spy_shutdown)

    def fetch(fid):
        if fid == "file3":
            raise KeyboardInterrupt
        return b"x"

    with pytest.raises(KeyboardInterrupt):
        _check(repo, fetch_bytes=fetch, text_layer=lambda c: LAYER, batch=5, workers=1)
    assert [u["candidate_id"] for u in repo.updates] == [1, 2]
    assert (False, True) in shutdowns
    assert repo.log[-1] == "close"


@pytest.mark.parametrize(
    "layer, expect_ocr",
    [
        ("x" * 10, True),  # near-empty text layer
        (LONG * 2, True),  # short, neither the invoice nor the amount seen
        (LONG + "Invoice 1042", False),  # short but the invoice is seen
        ("z " * 300, False),  # long enough: no OCR even though nothing seen
    ],
)
def test_ocr_trigger(layer, expect_ocr):
    repo = FakeRepo(rows=[_row(1)])
    calls = []

    def ocr(content):
        calls.append(content)
        return LONG + "Invoice 1042 Total 1,234.50"

    _check(repo, fetch_bytes=lambda f: b"%PDF", text_layer=lambda c: layer, ocr=ocr)
    update = repo.updates[0]
    assert bool(calls) is expect_ocr and update["used_ocr"] is expect_ocr
    if expect_ocr:
        assert update["check_status"] == "checked"
        assert update["invoice_seen_count"] >= 1 and update["amount_seen_count"] == 1


class FakeDI:
    def __init__(self):
        self.calls = 0
        self.contents = []

    def extract_text(self, content, content_type):
        self.calls += 1
        self.contents.append(content)
        return {"content": "OCR TEXT Invoice 1042", "pages_count": 1, "key_value_pairs": []}


def test_default_paths_reuse_text_layer_and_di_read_on_first_pages(monkeypatch):
    import entities.attachment.business.extraction_service as ext

    monkeypatch.setattr(ext, "_extract_text_layer", lambda content, ct: ("layer text", 1))
    assert default_text_layer(b"%PDF-") == "layer text"
    monkeypatch.setattr(service_mod, "first_pages", lambda content, pages=3: b"trimmed")
    di = FakeDI()
    assert "OCR TEXT" in default_ocr(b"%PDF-full", di=di)
    assert di.calls == 1 and di.contents == [b"trimmed"]


def _pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_ocr_input_capped_to_first_three_pages():
    from pypdf import PdfReader

    assert len(PdfReader(io.BytesIO(first_pages(_pdf(5), 3))).pages) == 3
    assert len(PdfReader(io.BytesIO(first_pages(_pdf(2), 3))).pages) == 2


# -- Box inventory (read-only) --------------------------------------------------------- #


class FakeBox:
    """Read-only Box double: only .get exists, so any write verb would AttributeError."""

    def __init__(self, tree):
        self.tree = tree
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, *, params=None, operation_name=None, **kwargs):
        self.calls.append((path, dict(params or {})))
        entries = self.tree.get(path.split("/")[1], [])
        marker = int((params or {}).get("marker") or 0)
        nxt = marker + 2  # tiny pages to exercise marker pagination
        return {"entries": entries[marker:nxt], "next_marker": str(nxt) if nxt < len(entries) else None}


def test_box_inventory_walks_invoice_folders_only():
    from entities.attachment_backfill.business.box_inventory import is_invoices_folder, walk_inventory

    def folder(i, name):
        return {"type": "folder", "id": i, "name": name}

    def file(i, name):
        return {"type": "file", "id": i, "name": name, "size": 10}

    tree = {
        "root": [folder("p1", "ABC1 - 1 Elm"), file("x", "stray.pdf")],
        "p1": [folder("inv", "14 - Invoices"), folder("other", "12 - Budgets"), folder("old", "Old invoice scans")],
        "inv": [file("a", "a.pdf"), file("b", "b.PDF"), file("c", "c.xlsx"), folder("sub", "2023")],
        "sub": [file("d", "d.pdf")],
        "old": [file("e", "e.pdf")],
        "other": [file("z", "z.pdf")],
    }
    box = FakeBox(tree)
    inv = walk_inventory(box, "root")
    assert sorted(f.file_id for f in inv) == ["a", "b", "d", "e"]
    assert {f.project_folder_name for f in inv} == {"ABC1 - 1 Elm"}
    assert next(f for f in inv if f.file_id == "d").folder_id == "sub"
    assert all(p.startswith("folders/") and p.endswith("/items") for p, _ in box.calls)
    assert all(params["fields"] == "id,name,type,size" for _, params in box.calls)
    assert is_invoices_folder("14 Invoices") and not is_invoices_folder("140 - Plans")


# -- write surface -------------------------------------------------------------------- #

_FORBIDDEN_MODULE = re.compile(r"bill_line_item_attachment|attachment\.persistence|outbox|shared\.storage")
_FORBIDDEN_NAMES = frozenset(
    {"AzureBlobStorage", "upload_file", "upload_file_version", "create_folder", "post", "put", "delete"}
)


# `post` / `put` / `delete` are allowed ONLY as the FastAPI route decorator on the Name `router`
# (`@router.post(...)` in api/router.py, U-535b b2); on any other object they stay banned.
_ROUTE_VERBS = frozenset({"post", "put", "delete"})


def _write_path_violations(source: str, filename: str = "<src>") -> list[tuple[str, str]]:
    """(filename, offending name) for every write-path reference in `source`; empty = clean."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if _FORBIDDEN_MODULE.search(node.module or ""):
                found.append((filename, node.module))
            found.extend((filename, n) for n in sorted({a.name for a in node.names} & _FORBIDDEN_NAMES))
        elif isinstance(node, ast.Import):
            found.extend((filename, a.name) for a in node.names if _FORBIDDEN_MODULE.search(a.name))
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            is_route_decorator = (
                node.attr in _ROUTE_VERBS and isinstance(node.value, ast.Name) and node.value.id == "router"
            )
            if not is_route_decorator:
                found.append((filename, node.attr))
    return found


def test_package_never_references_write_paths():
    """Code (not prose): no import of a link/attachment/blob/outbox module, no Box write verb."""
    for path in sorted(PACKAGE.rglob("*.py")) + [SCRIPT]:
        assert _write_path_violations(path.read_text(encoding="utf-8"), path.name) == []


@pytest.mark.parametrize(
    "source",
    [
        "client.post('files/1/content')",
        "box.delete('files/1')",
        "self.client.put('x')",
        "router.client.post('x')",
        "routers.post('x')",
        "box.upload_file(b'x')",
        "from integrations.box.base.client import upload_file",
        "from integrations.box.outbox.service import BoxOutboxService",
        "import shared.storage",
    ],
)
def test_write_path_guard_still_catches_writes(source):
    assert _write_path_violations(source)


def test_write_path_guard_allows_only_the_router_route_decorator():
    src = "@router.post('/x')\ndef f():\n    pass\n\n@router.delete('/y')\ndef g():\n    pass\n"
    assert _write_path_violations(src) == []
    assert _write_path_violations(src + "box.post('z')\n") == [("<src>", "post")]


def _sql_code() -> str:
    text = re.sub(r"/\*.*?\*/", "", SQL_FILE.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def _write_targets(code: str) -> list[str]:
    """Tables written by INSERT/UPDATE/DELETE/MERGE; an alias (UPDATE c ... FROM dbo.X AS c) resolves to its table."""
    patterns = (
        r"\bINSERT\s+INTO\s+(@?[\w.\[\]]+)",
        r"\bUPDATE\s+(@?[\w.\[\]]+)",
        r"\bDELETE\s+(?:FROM\s+)?(@?[\w.\[\]]+)",
        r"\bMERGE\s+(?:INTO\s+)?(@?[\w.\[\]]+)",
    )
    targets = []
    for pattern in patterns:
        for m in re.finditer(pattern, code, re.I):
            name = m.group(1)
            if name.startswith("@"):
                continue  # table variable
            if "." not in name:
                alias = re.search(
                    rf"\bFROM\s+([\w.\[\]]+)\s+AS\s+{re.escape(name)}\b", code[m.end(): m.end() + 800], re.I
                )
                assert alias, f"unresolved write alias {name!r}"
                name = alias.group(1)
            targets.append(name)
    return targets


def test_sql_writes_only_the_staging_tables():
    names = {t.replace("[", "").replace("]", "").removeprefix("dbo.") for t in _write_targets(_sql_code())}
    assert names and names <= {"AttachmentBackfillRun", "AttachmentBackfillCandidate"}, names


def test_sql_has_no_bill_foreign_key_and_no_verdict_column():
    code = _sql_code()
    assert "REFERENCES dbo.[Bill]" not in code and "REFERENCES dbo.Bill" not in code
    assert "AutoVerdict" not in code and "VerifiedDatetime" not in code
