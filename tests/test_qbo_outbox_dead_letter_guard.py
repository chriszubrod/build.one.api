"""U-232 — hard guard on the QBO outbox dead-letter re-push path."""
from unittest.mock import MagicMock

import pytest

from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.service import (
    QboOutboxDeadLetterExistsError,
    QboOutboxService,
)

REALM_ID = "realm-test"
ENTITY_TYPE = "Bill"
ENTITY_PUBLIC_ID = "22222222-2222-2222-2222-222222222222"
KIND = "sync_bill_to_qbo"


def _dead_letter_row():
    return QboOutbox(
        id=5,
        public_id="dead-letter-outbox-5",
        row_version="abc",
        kind=KIND,
        entity_type=ENTITY_TYPE,
        entity_public_id=ENTITY_PUBLIC_ID,
        realm_id=REALM_ID,
        request_id="req-original",
        status="dead_letter",
        attempts=5,
        last_error="QboValidationError: bad payload",
    )


def _pending_row():
    return QboOutbox(
        id=9,
        public_id="pending-outbox-9",
        row_version="xyz",
        kind=KIND,
        entity_type=ENTITY_TYPE,
        entity_public_id=ENTITY_PUBLIC_ID,
        realm_id=REALM_ID,
        request_id="req-pending",
        status="pending",
        attempts=0,
    )


def test_enqueue_refuses_fresh_create_when_dead_letter_row_exists():
    repo = MagicMock()
    repo.read_pending_by_entity.return_value = None
    repo.read_dead_letter_by_entity.return_value = _dead_letter_row()
    service = QboOutboxService(repo=repo)

    with pytest.raises(QboOutboxDeadLetterExistsError) as exc_info:
        service.enqueue(
            kind=KIND,
            entity_type=ENTITY_TYPE,
            entity_public_id=ENTITY_PUBLIC_ID,
            realm_id=REALM_ID,
        )

    assert exc_info.value.dead_letter_public_id == "dead-letter-outbox-5"
    assert "retry_qbo_outbox_dead_letters.py" in str(exc_info.value)
    repo.create.assert_not_called()


def test_enqueue_coalesces_into_pending_row_without_checking_dead_letter():
    repo = MagicMock()
    pending = _pending_row()
    repo.read_pending_by_entity.return_value = pending
    repo.update_ready_after.return_value = pending
    service = QboOutboxService(repo=repo)

    result = service.enqueue(
        kind=KIND,
        entity_type=ENTITY_TYPE,
        entity_public_id=ENTITY_PUBLIC_ID,
        realm_id=REALM_ID,
    )

    assert result is pending
    repo.read_dead_letter_by_entity.assert_not_called()
    repo.create.assert_not_called()


def test_enqueue_creates_fresh_row_when_no_pending_and_no_dead_letter():
    repo = MagicMock()
    repo.read_pending_by_entity.return_value = None
    repo.read_dead_letter_by_entity.return_value = None
    created = QboOutbox(
        id=1,
        public_id="fresh-outbox-1",
        row_version="new",
        kind=KIND,
        entity_type=ENTITY_TYPE,
        entity_public_id=ENTITY_PUBLIC_ID,
        realm_id=REALM_ID,
        request_id="req-fresh",
        status="pending",
        attempts=0,
    )
    repo.create.return_value = created
    service = QboOutboxService(repo=repo)

    result = service.enqueue(
        kind=KIND,
        entity_type=ENTITY_TYPE,
        entity_public_id=ENTITY_PUBLIC_ID,
        realm_id=REALM_ID,
    )

    assert result is created
    repo.create.assert_called_once()


def test_read_dead_letter_sproc_filters_on_dead_letter_status():
    """Static pin for the new ReadDeadLetterQboOutboxByEntity sproc — the
    unit tests above mock repo.read_dead_letter_by_entity() entirely, so a
    dropped/typo'd status filter in the SQL itself wouldn't be caught there.
    No live-DB test harness exists (v1 is pure-logic/no-live-DB), so pin the
    WHERE-clause semantics directly against the SQL text — reusing the
    existing GO-boundary-aware sproc extractor rather than hand-rolled
    string splitting (see tests/test_sproc_nocount_shape_guard.py)."""
    from tests.test_sproc_nocount_shape_guard import _extract_procedures

    sql = open("integrations/intuit/qbo/outbox/sql/qbo.outbox.sql").read()
    procedures = {name: body for name, body, _line_no in _extract_procedures(sql)}
    assert "ReadDeadLetterQboOutboxByEntity" in procedures

    normalized = " ".join(procedures["ReadDeadLetterQboOutboxByEntity"].split())

    assert "[EntityType] = @EntityType" in normalized
    assert "[EntityPublicId] = @EntityPublicId" in normalized
    assert "[Kind] = @Kind" in normalized
    assert "[Status] = 'dead_letter'" in normalized
    assert "'pending'" not in normalized
    assert "'failed'" not in normalized
