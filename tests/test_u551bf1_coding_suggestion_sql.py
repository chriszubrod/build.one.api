"""U-551b-f1: text pins on the CodingSuggestion base SQL (written-lines record)."""
import re
from pathlib import Path

import pytest

SQL = (Path(__file__).resolve().parent.parent / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql").read_text()


def _batches():
    return re.split(r"^GO\s*$", SQL, flags=re.M)


def _proc(name):
    for b in _batches():
        if re.search(rf"CREATE OR ALTER PROCEDURE\s+{name}\b", b):
            return b
    raise AssertionError(f"{name} not found")


def test_guarded_alter_for_both_columns():
    for col, typ in (("WrittenSyncToken", "NVARCHAR(50)"), ("WrittenLinesJson", "NVARCHAR(MAX)")):
        pat = (
            rf"IF COL_LENGTH\('dbo\.CodingSuggestion', '{col}'\) IS NULL\s+"
            rf"ALTER TABLE dbo\.\[CodingSuggestion\] ADD \[{col}\] {re.escape(typ)} NULL;"
        )
        assert re.search(pat, SQL), col


def test_create_table_carries_both_columns():
    create = SQL[SQL.index("CREATE TABLE [dbo].[CodingSuggestion]"):]
    create = create[: create.index("CONSTRAINT [CK_CodingSuggestion_Target]")]
    assert "[WrittenSyncToken] NVARCHAR(50) NULL" in create
    assert "[WrittenLinesJson] NVARCHAR(MAX) NULL" in create


def test_mark_written_declares_param_and_preserves_with_case():
    body = _proc("MarkCodingSuggestionWritten")
    assert "@WrittenLinesJson NVARCHAR(MAX) = NULL" in body
    assert re.search(
        r"\[WrittenSyncToken\] = CASE WHEN @SyncToken IS NOT NULL THEN @SyncToken ELSE cs\.\[WrittenSyncToken\] END", body)
    assert re.search(
        r"\[WrittenLinesJson\] = CASE WHEN @WrittenLinesJson IS NOT NULL THEN @WrittenLinesJson ELSE cs\.\[WrittenLinesJson\] END", body)
    assert "cs.[WrittenSyncToken]," in body and "cs.[WrittenLinesJson]," in body  # final SELECT


def test_read_by_public_id_selects_both():
    body = _proc("ReadCodingSuggestionByPublicId")
    assert "cs.[WrittenSyncToken]" in body and "cs.[WrittenLinesJson]" in body


def test_every_batch_has_at_most_one_create_or_alter():
    for b in _batches():
        assert len(re.findall(r"CREATE OR ALTER", b)) <= 1


def test_confirmation_source_values_fit_column_and_param():
    """R2-1: a value longer than the column/param is truncated SILENTLY (23-char source -> 20)."""
    from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE

    col_sizes = [int(n) for n in re.findall(r"\[ConfirmationSource\] NVARCHAR\((\d+)\)", SQL)]
    par_sizes = [int(n) for n in re.findall(r"@ConfirmationSource NVARCHAR\((\d+)\)", SQL)]
    assert col_sizes and par_sizes
    for value in (REPOINT_CONFIRMATION_SOURCE, "review_submit", "confirm_endpoint"):
        assert all(len(value) <= n for n in col_sizes + par_sizes), value
    assert REPOINT_CONFIRMATION_SOURCE == "complete_repoint"


def test_column_comment_lists_the_repoint_source():
    line = next(l for l in SQL.splitlines() if l.strip().startswith("[ConfirmationSource] NVARCHAR"))
    assert "complete_repoint" in line


def test_guarded_alters_never_share_a_batch_with_create_or_alter():
    """R2-8d: an ALTER swallowed into a preceding procedure batch would be proc body, not DDL."""
    alter_batches = [b for b in _batches() if re.search(r"ALTER TABLE dbo\.\[CodingSuggestion\] ADD", b)]
    assert len(alter_batches) >= 4
    for b in alter_batches:
        assert "CREATE OR ALTER" not in b


def test_record_confirmation_sets_sync_token_with_case_preserve():
    body = _proc("RecordCodingSuggestionConfirmation")
    assert re.search(
        r"\[SyncTokenAtSuggest\] = CASE WHEN @ExpectedSyncToken IS NOT NULL "
        r"THEN @ExpectedSyncToken ELSE cs\.\[SyncTokenAtSuggest\] END", body)


def test_state_read_exposes_confirmation_source():
    assert "cs.[ConfirmationSource]" in _proc("ReadCodingSuggestionStateByExpenseIds")


# ---- R3-C: persisted repoint targets --------------------------------------------------------

def test_repoint_targets_guarded_alter_in_its_own_batch_and_create_table():
    pat = (
        r"IF COL_LENGTH\('dbo\.CodingSuggestion', 'RepointTargetsJson'\) IS NULL\s+"
        r"ALTER TABLE dbo\.\[CodingSuggestion\] ADD \[RepointTargetsJson\] NVARCHAR\(MAX\) NULL;"
    )
    (batch,) = [b for b in _batches() if re.search(pat, b)]
    assert "CREATE OR ALTER" not in batch and "CREATE TABLE" not in batch
    create = SQL[SQL.index("CREATE TABLE [dbo].[CodingSuggestion]"):]
    create = create[: create.index("CONSTRAINT [CK_CodingSuggestion_Target]")]
    assert "[RepointTargetsJson] NVARCHAR(MAX) NULL" in create


def test_record_confirmation_declares_param_and_sets_unconditionally():
    body = _proc("RecordCodingSuggestionConfirmation")
    assert "@RepointTargetsJson NVARCHAR(MAX) = NULL" in body
    assert re.search(r"\[RepointTargetsJson\] = @RepointTargetsJson,", body)
    assert "CASE WHEN @RepointTargetsJson" not in body
    assert "cs.[RepointTargetsJson]" in body.split("SELECT", 1)[1]


def test_read_by_public_id_selects_repoint_targets():
    assert "cs.[RepointTargetsJson]" in _proc("ReadCodingSuggestionByPublicId")


def test_mark_enqueued_is_seq_guarded_like_mark_written():
    body = _proc("MarkCodingSuggestionEnqueued")
    assert "@ExpectedConfirmationSeq INT = NULL" in body
    assert "@ExpectedConfirmationSeq IS NULL" in body
    assert "cs.[ConfirmationSeq] = @ExpectedConfirmationSeq" in body
    assert "cs.[Status] IN (N'confirmed', N'enqueued')" in body


# ---- R4b-1: RecordCodingSuggestionConfirmation is guarded by the revision the caller read ----

def test_record_confirmation_declares_expected_seq_and_guards_the_update():
    body = _proc("RecordCodingSuggestionConfirmation")
    assert "@ExpectedConfirmationSeq INT = NULL" in body
    where = _update_where(body)
    assert "cs.[PublicId] = @PublicId" in where
    assert "@ExpectedConfirmationSeq IS NULL" in where  # the NULL branch keeps PublicId-only
    assert "cs.[ConfirmationSeq] = @ExpectedConfirmationSeq" in where


# ---- R4b-2(4): the guard predicates as a parsed boolean shape --------------------------------

def _update_where(body):
    m = re.search(r"\bWHERE\b(.*?);", body[body.index("UPDATE cs"):], flags=re.S)
    assert m, "UPDATE ... WHERE not found"
    return m.group(1)


@pytest.mark.parametrize("proc,statuses", [
    # U-551b-f1-f5: "error" admitted in the seq-given branch so the admin redrive of an error repoint can fence
    ("RecordCodingSuggestionConfirmation", {"written", "confirmed", "enqueued", "error"}),
    ("MarkCodingSuggestionEnqueued", {"confirmed", "enqueued"}),
    ("MarkCodingSuggestionWritten", {"confirmed", "enqueued"}),
    ("MarkCodingSuggestionChangedInQbo", {"confirmed", "enqueued"}),
    ("MarkCodingSuggestionError", {"confirmed", "enqueued"}),
])
def test_guard_predicate_shape_has_no_tautology_and_the_exact_status_set(proc, statuses):
    flat = re.sub(r"\s+", " ", _update_where(_proc(proc))).strip()
    assert not re.search(r"\bOR\s+1\s*=\s*1\b", flat, flags=re.I)
    assert not re.search(r"\b1\s*=\s*1\b", flat)
    assert "RowVersion" not in flat
    # exactly one OR, at the top of the seq guard: (@Expected IS NULL OR (seq = @Expected AND status IN (...)))
    assert len(re.findall(r"\bOR\b", flat)) == 1
    m = re.fullmatch(
        r"cs\.\[PublicId\] = @PublicId AND \( @ExpectedConfirmationSeq IS NULL OR \( "
        r"cs\.\[ConfirmationSeq\] = @ExpectedConfirmationSeq AND cs\.\[Status\] IN \(([^)]*)\) \) \)", flat)
    assert m, flat
    listed = set(re.findall(r"N'([a-z_]+)'", m.group(1)))
    assert listed == statuses
    assert len(re.findall(r"IN \(", flat)) == 1


def test_record_confirmation_increments_the_seq_by_exactly_one():
    # a dropped "+ 1" would leave every revision at the same seq and silently disarm every seq guard
    # r6b Codex P3: strip `--` comments first, so a commented-out assignment cannot satisfy the pin
    body = re.sub(r"/\*.*?\*/", "", _proc("RecordCodingSuggestionConfirmation"), flags=re.S)  # r6c twin P3: block comments too
    body = re.sub(r"--[^\n]*", "", body)
    flat = re.sub(r"\s+", " ", body)
    assert "[ConfirmationSeq] = cs.[ConfirmationSeq] + 1," in flat
    assert flat.count("[ConfirmationSeq] = cs.[ConfirmationSeq] + 1,") == 1


def test_record_confirmation_where_has_no_tautology_and_the_exact_status_set():
    # r4b P3 (iii): RecordCodingSuggestionConfirmation's OWN guard (also in the parametrized shape test above,
    # pinned here by name so a rewrite of that table cannot silently drop it)
    flat = re.sub(r"\s+", " ", _update_where(_proc("RecordCodingSuggestionConfirmation")))
    assert not re.search(r"\bOR\s+1\s*=\s*1\b", flat, flags=re.I)
    assert not re.search(r"\b1\s*=\s*1\b", flat)
    assert len(re.findall(r"\bOR\b", flat)) == 1
    (listed,) = re.findall(r"cs\.\[Status\] IN \(([^)]*)\)", flat)
    assert set(re.findall(r"N'([a-z_]+)'", listed)) == {"written", "confirmed", "enqueued", "error"}  # U-551b-f1-f5
