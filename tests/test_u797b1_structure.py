"""U-797b1 structure (round 2): the shadow enqueue runs before the CL drafts; the target repo sends the snapshot
params only with a snapshot; the review SQL widens SuggestionSource; the hold table and its sprocs and the
suggestion read follow the house rules (SET NOCOUNT ON, GO after every batch, no ROLLBACK, guarded CREATE and
ALTER, NULL defaults, Contract headers); and the repo maps each sproc's aliases."""

import ast
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from entities.review.business import review_notification_service as rns
from entities.review.persistence import review_notification_repo as repo_mod

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "entities/review/business/service.py"
CODING_SQL = ROOT / "entities/contract_labor/sql/dbo.contract_labor_coding_suggestion.sql"
HOLD_SQL = ROOT / "entities/contract_labor/sql/dbo.contract_labor_review_hold.sql"
REVIEW_SQL = ROOT / "entities/review/sql/dbo.review_notification.sql"
NOW_DT = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)
WORK_DAY = date(2026, 11, 19)

HOLD_SPROCS = [
    "UpsertContractLaborReviewHold",
    "DeleteContractLaborReviewHold",
    "PruneContractLaborReviewHolds",
    "ReadContractLaborReviewHoldsDue",
    "ReadContractLaborReviewHold",
    "RecordContractLaborReviewHoldFire",
]


# ---- the order: shadow enqueue before the drafts -----------------------------------------------

def _attr_call_lines(fn, attr):
    return [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def test_shadow_enqueue_runs_before_the_cl_drafts_in_review_create():
    tree = ast.parse(SERVICE.read_text())
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "create"
           and _attr_call_lines(n, "enqueue_for_contract_labor") and _attr_call_lines(n, "enqueue_drafts")]
    assert len(fns) == 1
    assert min(_attr_call_lines(fns[0], "enqueue_for_contract_labor")) < min(_attr_call_lines(fns[0], "enqueue_drafts"))


# ---- the repo: snapshot params only with a snapshot (F8) --------------------------------------

class _Cursor:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = many or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


@pytest.fixture
def procs(monkeypatch):
    calls = []
    result = {"one": None, "many": []}

    @contextmanager
    def fake_conn():
        class _Conn:
            def cursor(self_inner):
                return _Cursor(result["one"], result["many"])

            def commit(self_inner):
                pass

        yield _Conn()

    def fake_call(cursor, name, params):
        calls.append((name, dict(params)))

    monkeypatch.setattr(repo_mod, "get_connection", fake_conn)
    monkeypatch.setattr(repo_mod, "call_procedure", fake_call)
    return calls, result


def test_add_target_sends_the_snapshot_params(procs):
    calls, _ = procs
    repo_mod.ReviewNotificationRepository().add_target(
        review_notification_id=3, bill_id=None, expense_id=None, bill_credit_id=None, contract_labor_id=7,
        suggested_sub_cost_code_id=100, suggestion_id=77, suggestion_source="history",
        suggestion_may_split=False, lines_fingerprint="ab" * 32, with_snapshot=True,
    )
    name, params = calls[-1]
    assert name == "AddReviewNotificationTarget"
    assert params["SuggestedSubCostCodeId"] == 100 and params["SuggestionId"] == 77
    assert params["SuggestionSource"] == "history" and params["SuggestionMaySplit"] is False
    assert params["LinesFingerprint"] == "ab" * 32


def test_bill_style_target_omits_the_snapshot_params(procs):
    calls, _ = procs
    repo_mod.ReviewNotificationRepository().add_target(
        review_notification_id=3, bill_id=9, expense_id=None, bill_credit_id=None, contract_labor_id=None)
    assert list(calls[-1][1]) == ["ReviewNotificationId", "BillId", "ExpenseId", "BillCreditId", "ContractLaborId"]


def test_snapshot_params_appear_only_with_with_snapshot(procs):
    calls, _ = procs
    repo_mod.ReviewNotificationRepository().add_target(
        review_notification_id=3, bill_id=None, expense_id=None, bill_credit_id=None, contract_labor_id=7,
        with_snapshot=False)
    assert "LinesFingerprint" not in calls[-1][1]


@dataclass
class _RecordingRepo:
    targets: list = field(default_factory=list)
    notification: object = None

    def create(self, **kw):
        return self.notification

    def add_target(self, **kw):
        self.targets.append(kw)


@dataclass(frozen=True)
class _NotificationRow:
    Id: int


def test_claim_for_crew_writes_one_snapshot_per_crew_member():
    repo = _RecordingRepo(notification=_NotificationRow(Id=5))
    snap = rns.CrewTargetSnapshot(7, 100, 77, "model", True, "cd" * 32)
    rns.ReviewNotificationRecordService(repo=repo).claim_for_crew(
        project_id=2, work_date=WORK_DAY, subject="s", contract_labor_ids=[7, 8], snapshots={7: snap})
    by_cl = {t["contract_labor_id"]: t for t in repo.targets}
    assert by_cl[7]["suggested_sub_cost_code_id"] == 100 and by_cl[7]["with_snapshot"] is True
    assert by_cl[7]["suggestion_may_split"] is True and by_cl[7]["lines_fingerprint"] == "cd" * 32
    assert "with_snapshot" not in by_cl[8]


def test_claim_without_snapshots_is_the_trunk_call_shape():
    repo = _RecordingRepo(notification=_NotificationRow(Id=5))
    rns.ReviewNotificationRecordService(repo=repo).claim_for_crew(
        project_id=2, work_date=WORK_DAY, subject="s", contract_labor_ids=[7])
    assert repo.targets == [{"review_notification_id": 5, "bill_id": None, "expense_id": None,
                             "bill_credit_id": None, "contract_labor_id": 7}]


def test_record_for_review_targets_never_carry_snapshot_kwargs():
    repo = _RecordingRepo(notification=_NotificationRow(Id=5))
    rns.ReviewNotificationRecordService(repo=repo).record_for_review(
        kind="bill", review_id=1, targets=[("bill", 9)], subject="s")
    assert all("suggested_sub_cost_code_id" not in t for t in repo.targets)


def test_read_crew_snapshots_maps_rows_to_snapshots():
    @dataclass(frozen=True)
    class _SnapRow:
        ContractLaborId: int
        SuggestedSubCostCodeId: object
        SuggestionId: object
        SuggestionSource: object
        SuggestionMaySplit: object
        LinesFingerprint: object

    class _Repo:
        def read_target_snapshots(self, *, review_notification_id):
            return [_SnapRow(7, 100, 77, "history", False, "ef" * 32), _SnapRow(8, None, None, None, None, None)]

    out = rns.ReviewNotificationRecordService(repo=_Repo()).read_crew_snapshots(5)
    assert out[7] == rns.CrewTargetSnapshot(7, 100, 77, "history", False, "ef" * 32)
    assert out[8].suggested_sub_cost_code_id is None


def test_config_flag_exists_and_defaults_off():
    text = (ROOT / "config.py").read_text()
    assert re.search(r"cl_suggestions_in_review_enabled:\s*bool\s*=\s*False", text)
    assert "cl_coding_shadow_enabled: bool = False" in text


# ---- SQL house rules helpers -------------------------------------------------------------------

def _batches(text):
    out, current = [], []
    for line in text.splitlines():
        if line.strip().upper() == "GO":
            out.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if "\n".join(current).strip():
        out.append("\n".join(current))
    return out


def _proc_batch(text, name):
    matches = [b for b in _batches(text) if re.search(rf"CREATE OR ALTER PROCEDURE\s+dbo\.{name}\b", b)]
    assert len(matches) == 1, f"{name} must be defined in exactly one GO-terminated batch"
    return matches[0]


def test_no_rollback_in_any_touched_sql_file():
    for path in (CODING_SQL, HOLD_SQL, REVIEW_SQL):
        assert "ROLLBACK" not in path.read_text().upper(), path.name


def test_coding_sql_no_longer_defines_the_held_crew_sproc_and_still_lists_the_read():
    text = CODING_SQL.read_text()
    assert "ReadContractLaborCodingHeldCrews" not in text
    body = _proc_batch(text, "ReadContractLaborCodingSuggestionsByProjectAndDate")
    assert "SET NOCOUNT ON;" in body and body.count("CREATE OR ALTER PROCEDURE") == 1


@pytest.mark.parametrize("alias", [
    "[Id]", "[ContractLaborId]", "[Status]", "[DecisionSource]", "[SubCostCodeId]", "[SubCostCodeNumber]",
    "[SubCostCodeName]", "[IsHoldingCode]", "[MaySplit]", "[ModelSplitCodes]", "[ContractLaborModifiedDatetime]",
    "[ContractLaborStatus]", "[HasSubmittedProjectLine]",
])
def test_suggestion_read_sproc_selects_each_alias_the_repo_reads(alias):
    body = _proc_batch(CODING_SQL.read_text(), "ReadContractLaborCodingSuggestionsByProjectAndDate")
    assert re.search(rf"AS {re.escape(alias)}|[.]{re.escape(alias)}|\s{re.escape(alias)}", body), alias


def test_has_submitted_project_line_is_an_exists_on_the_project():
    body = _proc_batch(CODING_SQL.read_text(), "ReadContractLaborCodingSuggestionsByProjectAndDate")
    assert re.search(r"EXISTS \(\s*SELECT 1 FROM dbo\.\[ContractLaborLineItem\] li[\s\S]*li\.\[ProjectId\] = s\.\[ProjectId\]", body)


# ---- the hold table and its sprocs -------------------------------------------------------------

def test_hold_table_is_a_guarded_create_with_the_agreed_columns_and_unique_key():
    text = HOLD_SQL.read_text()
    assert "IF OBJECT_ID('dbo.ContractLaborReviewHold', 'U') IS NULL" in text
    for col in ("[Id] BIGINT IDENTITY(1,1)", "[ProjectId] BIGINT NOT NULL", "[WorkDate] DATE NOT NULL",
                "[HeldDatetime] DATETIME2(3) NOT NULL", "[FireAttempts] INT NOT NULL",
                "[LastFireDatetime] DATETIME2(3) NULL", "[GaveUpDatetime] DATETIME2(3) NULL"):
        assert col in text, col
    assert "DEFAULT 0" in text and "DEFAULT GETUTCDATE()" in text
    assert "UNIQUE ([ProjectId], [WorkDate])" in text
    assert "-- Contract: dbo.ContractLaborReviewHold" in text.splitlines()[0]
    assert "CREATE TABLE" in text.split("IF OBJECT_ID")[1]


def test_hold_sql_ends_with_go_and_every_sproc_is_its_own_batch():
    text = HOLD_SQL.read_text()
    assert text.rstrip().endswith("GO")
    for name in HOLD_SPROCS:
        body = _proc_batch(text, name)
        assert body.count("CREATE OR ALTER PROCEDURE") == 1, name
        assert "ROLLBACK" not in body.upper(), name


@pytest.mark.parametrize("name", [n for n in HOLD_SPROCS if n not in ("ReadContractLaborReviewHoldsDue",)])
def test_hold_sprocs_set_nocount(name):
    assert "SET NOCOUNT ON;" in _proc_batch(HOLD_SQL.read_text(), name)


def test_upsert_is_a_locked_update_or_insert():
    # Round 3 (EM): one marker per (project, date) — update under UPDLOCK/HOLDLOCK, insert only when absent.
    body = _proc_batch(HOLD_SQL.read_text(), "UpsertContractLaborReviewHold")
    assert "UPDLOCK, HOLDLOCK" in body and "IF @@ROWCOUNT = 0" in body and body.count("INSERT INTO") == 1


def test_prune_deletes_only_markers_whose_day_already_has_a_claim():
    body = _proc_batch(HOLD_SQL.read_text(), "PruneContractLaborReviewHolds")
    assert body.count("DELETE") == 1 and "ContractLaborNotification" in body and "ReviewNotification" in body
    assert "EXISTS" in body


def test_due_read_is_bounded_to_seven_days_and_excludes_gave_up_markers():
    body = _proc_batch(HOLD_SQL.read_text(), "ReadContractLaborReviewHoldsDue")
    assert "DATEADD(DAY, -@Days, GETUTCDATE())" in body.replace(" ", " ")
    assert "@Days INT = 7" in body
    assert "GaveUpDatetime] IS NULL" in body and "FireAttempts] < @MaxAttempts" in body
    assert "AS [ContractLaborId]" in body


def test_record_fire_increments_and_gives_up_at_the_limit():
    body = _proc_batch(HOLD_SQL.read_text(), "RecordContractLaborReviewHoldFire")
    assert "@MaxAttempts INT = 5" in body
    assert "[FireAttempts] = [FireAttempts] + 1" in body
    assert "CASE WHEN [FireAttempts] + 1 >= @MaxAttempts" in body


def test_hold_sprocs_take_null_or_literal_defaults_only_on_new_params():
    body = _proc_batch(HOLD_SQL.read_text(), "ReadContractLaborReviewHoldsDue")
    assert "@Days INT = 7" in body


# ---- the widened SuggestionSource (A2) ------------------------------------------------------------

def test_suggestion_source_is_nvarchar_50_in_the_column_and_the_sproc_param():
    text = REVIEW_SQL.read_text()
    assert re.search(r"\[SuggestionSource\]\s+NVARCHAR\(50\) NULL", text)
    assert re.search(r"@SuggestionSource\s+NVARCHAR\(50\) = NULL", text)
    assert "NVARCHAR(20)" not in text.split("ALTER TABLE")[1].split("GO")[0]


def test_snapshot_columns_are_a_single_guarded_nullable_alter_then_go():
    text = REVIEW_SQL.read_text()
    m = re.search(
        r"IF COL_LENGTH\('dbo\.ReviewNotificationTarget',\s*'SuggestedSubCostCodeId'\) IS NULL\s*\n"
        r"\s*ALTER TABLE \[dbo\]\.\[ReviewNotificationTarget\] ADD(?P<cols>.*?);\s*\nGO",
        text, re.DOTALL)
    assert m, "guarded ALTER followed by GO not found"
    cols = m.group("cols")
    assert "NOT NULL" not in cols.upper()
    for col in ("SuggestedSubCostCodeId", "SuggestionId", "SuggestionSource", "SuggestionMaySplit", "LinesFingerprint"):
        assert f"[{col}]" in cols, col


def test_add_review_notification_target_new_params_default_to_null():
    body = _proc_batch(REVIEW_SQL.read_text(), "AddReviewNotificationTarget")
    for param in ("SuggestedSubCostCodeId", "SuggestionId", "SuggestionSource", "SuggestionMaySplit", "LinesFingerprint"):
        assert re.search(rf"@{param}\s+\S+\s*=\s*NULL", body), param


def test_read_target_snapshots_sproc_is_defined_and_go_terminated():
    body = _proc_batch(REVIEW_SQL.read_text(), "ReadReviewNotificationTargetSnapshots")
    assert "SET NOCOUNT ON" in body


# ---- repo mapping: aliases, nextset, the hold methods ----------------------------------------------

class _SeqCursor:
    def __init__(self, sets):
        self._sets = list(sets)
        self._i = 0

    def fetchall(self):
        return self._sets[self._i]

    def nextset(self):
        self._i += 1
        return self._i < len(self._sets)

    def fetchone(self):
        return None


def _patch_seq(monkeypatch, sets):
    from entities.contract_labor.persistence import coding_suggestion_repo as crepo

    cursor = _SeqCursor(sets)

    @contextmanager
    def fake_conn():
        class _Conn:
            def cursor(self_inner):
                return cursor

            def commit(self_inner):
                pass

        yield _Conn()

    calls = []
    monkeypatch.setattr(crepo, "get_connection", fake_conn)
    monkeypatch.setattr(crepo, "call_procedure", lambda cursor, name, params: calls.append((name, dict(params))))
    return calls


def test_read_suggestions_maps_rows_labels_and_the_submitted_line_flag(monkeypatch):
    from entities.contract_labor.persistence.coding_suggestion_repo import ContractLaborCodingSuggestionRepository

    row = SimpleNamespace(Id=9, ContractLaborId=3, Status="done", DecisionSource="model", SubCostCodeId=100,
                          SubCostCodeNumber="65.02", SubCostCodeName="Misc", IsHoldingCode=0, MaySplit=1,
                          ModelSplitCodes="7,8", ContractLaborModifiedDatetime=NOW_DT,
                          ContractLaborStatus="submitted", HasSubmittedProjectLine=1)
    calls = _patch_seq(monkeypatch, [[row], [SimpleNamespace(Id=7, Number="1.00", Name="Alpha")]])
    rows, labels = ContractLaborCodingSuggestionRepository().read_suggestions_for_project_day(5, WORK_DAY)
    assert calls[-1] == ("ReadContractLaborCodingSuggestionsByProjectAndDate", {"ProjectId": 5, "WorkDate": WORK_DAY})
    assert rows[0].has_submitted_project_line is True and rows[0].model_split_code_ids == (7, 8)
    assert rows[0].contract_labor_status == "submitted" and labels == {7: ("1.00", "Alpha")}


def test_read_holds_due_maps_each_marker(monkeypatch):
    from entities.contract_labor.persistence.coding_suggestion_repo import ContractLaborCodingSuggestionRepository

    row = SimpleNamespace(Id=4, ProjectId=5, WorkDate=WORK_DAY, HeldDatetime=NOW_DT, FireAttempts=2,
                          GaveUpDatetime=None, ContractLaborId=12)
    calls = _patch_seq(monkeypatch, [[row]])
    holds = ContractLaborCodingSuggestionRepository().read_review_holds_due(days=7)
    assert calls[-1] == ("ReadContractLaborReviewHoldsDue", {"Days": 7})
    assert holds[0].id == 4 and holds[0].contract_labor_id == 12 and holds[0].fire_attempts == 2


def test_read_hold_and_record_fire_map_rows_and_the_deleted_case(monkeypatch):
    from entities.contract_labor.persistence.coding_suggestion_repo import ContractLaborCodingSuggestionRepository

    repo = ContractLaborCodingSuggestionRepository()
    _patch_seq(monkeypatch, [[SimpleNamespace(Id=4, ProjectId=5, WorkDate=WORK_DAY, HeldDatetime=NOW_DT,
                                              FireAttempts=0, GaveUpDatetime=None)]])
    assert repo.read_review_hold(5, WORK_DAY).id == 4
    _patch_seq(monkeypatch, [[]])
    assert repo.read_review_hold(5, WORK_DAY) is None
    _patch_seq(monkeypatch, [[SimpleNamespace(Id=4, ProjectId=5, WorkDate=WORK_DAY, FireAttempts=5,
                                              GaveUpDatetime=NOW_DT)]])
    fire = repo.record_review_hold_fire(4)
    assert fire.fire_attempts == 5 and fire.gave_up_datetime == NOW_DT
    _patch_seq(monkeypatch, [[]])
    assert repo.record_review_hold_fire(4) is None  # the marker was deleted by the fire


def test_hold_write_methods_send_the_key_params(monkeypatch):
    from entities.contract_labor.persistence.coding_suggestion_repo import ContractLaborCodingSuggestionRepository

    calls = _patch_seq(monkeypatch, [[]])
    repo = ContractLaborCodingSuggestionRepository()
    repo.upsert_review_hold(5, WORK_DAY)
    repo.delete_review_hold(5, WORK_DAY)
    repo.prune_review_holds()
    assert calls[0] == ("UpsertContractLaborReviewHold", {"ProjectId": 5, "WorkDate": WORK_DAY})
    assert calls[1] == ("DeleteContractLaborReviewHold", {"ProjectId": 5, "WorkDate": WORK_DAY})
    assert calls[2] == ("PruneContractLaborReviewHolds", {})


# ---- the shadow import under the flag (H4) ------------------------------------------------------

def test_hold_sprocs_are_named_in_the_entity_docstring():
    text = (ROOT / "entities/contract_labor/business/service.py").read_text()
    for name in HOLD_SPROCS:
        assert name in text, name
    assert "ReadContractLaborCodingHeldCrews" not in text


def test_upsert_resets_a_re_held_marker():
    # Round 3 (EM): a day held again after giving up must be fired again: the upsert resets the marker.
    from pathlib import Path
    sql = Path("entities/contract_labor/sql/dbo.contract_labor_review_hold.sql").read_text()
    body = sql[sql.index("CREATE OR ALTER PROCEDURE dbo.UpsertContractLaborReviewHold"):]
    body = body[: body.index("\nGO")]
    flat = " ".join(body.split())
    assert "UPDATE dbo.[ContractLaborReviewHold] WITH (UPDLOCK, HOLDLOCK)" in flat
    for frag in ("[HeldDatetime] = SYSUTCDATETIME()", "[FireAttempts] = 0", "[LastFireDatetime] = NULL", "[GaveUpDatetime] = NULL"):
        assert frag in flat, frag
    assert "IF @@ROWCOUNT = 0" in flat


def test_due_read_backs_off_between_failed_fires():
    # Round 3 (EM): failed fires are spaced (10 min x attempts) so a short outage cannot exhaust all five.
    flat = " ".join(_proc_batch(HOLD_SQL.read_text(), "ReadContractLaborReviewHoldsDue").split())
    assert "h.[LastFireDatetime] IS NULL" in flat
    assert "h.[LastFireDatetime] <= DATEADD(MINUTE, -10 * h.[FireAttempts], GETUTCDATE())" in flat
