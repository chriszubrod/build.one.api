"""An unsupported worksheet layout dead-letters, but must NOT escalate.

U-559 refuses to write modern column positions into an old-template DETAILS
sheet. For the four workbooks concerned — CC (41), ML (74), MR2-STABLES (95),
CBT (101) — that refusal is PERMANENT by design: the commit deliberately does
not repair the sheets, because doing so restates client-billed documents.

`DetailsLayoutError` is a ValueError, so it misses the worker's
`except BoxError` retry path and lands in `except Exception`. Terminal is right
— a retry cannot fix a template. But escalating it is not: `flag_dead_letter`
has no dedupe and no suppression window, so every future bill, expense and
credit on those four projects would write a fresh `box.ReconciliationIssue`,
forever, to report a condition nobody can action from that table. Found by the
Pass 3 security review of `ea33f48e` (finding S2, MEDIUM/CONFIRMED).

The dead-letter row and its `box.outbox.row.unsupported_worksheet_layout` log
line remain the record. Only the reconciliation-issue write is suppressed.
"""

from types import SimpleNamespace

from integrations.box.excel.business.workbook_editor import (
    DetailsLayoutError,
    DetailsRowShapeError,
)
from integrations.box.outbox.business.worker import (
    BoxOutboxWorker,
    _is_unsupported_layout,
)


class _RecordingRepo:
    def __init__(self):
        self.dead_lettered = []

    def mark_dead_letter(self, *, id, row_version, last_error):
        self.dead_lettered.append((id, last_error))


def _row():
    return SimpleNamespace(
        id=1, row_version=b"\x00", public_id="pub-1", kind="update_box_excel",
        entity_type="Bill", entity_public_id="bill-1",
    )


def _worker():
    repo = _RecordingRepo()
    worker = BoxOutboxWorker(repo=repo)
    escalations = []
    worker._escalate_dead_letter = lambda row, err: escalations.append(err)
    return worker, repo, escalations


def test_an_unsupported_layout_dead_letters_without_escalating():
    worker, repo, escalations = _worker()

    worker._dead_letter(_row(), "old template", escalate=False)

    assert len(repo.dead_lettered) == 1, "the dead-letter itself must still happen"
    assert escalations == [], "no reconciliation issue may be written"


def test_every_other_dead_letter_still_escalates():
    """The default is unchanged — this must not become a silent-failure hatch."""
    worker, repo, escalations = _worker()

    worker._dead_letter(_row(), "something genuinely wrong")

    assert len(repo.dead_lettered) == 1
    assert escalations == ["something genuinely wrong"]


def test_the_layout_error_is_recognised_and_nothing_else_is():
    """The predicate must be exact. A DetailsRowShapeError is a DIFFERENT defect
    — a malformed row from a caller, which IS worth escalating — and it shares a
    base class with DetailsLayoutError, so a sloppy isinstance check on ValueError
    would silence it too."""
    assert _is_unsupported_layout(DetailsLayoutError("wrong columns")) is True
    assert _is_unsupported_layout(DetailsRowShapeError("bad row")) is False
    assert _is_unsupported_layout(ValueError("unrelated")) is False
    assert _is_unsupported_layout(RuntimeError("unrelated")) is False
