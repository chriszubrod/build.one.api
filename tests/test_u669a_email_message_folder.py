"""U-669a — EmailMessage [Folder] must round-trip on read for reviewer provenance."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from entities.email_message.persistence.repo import EmailMessageRepository
from shared.authz.provenance import ReviewerProvenanceRefused, bind_reviewer_reply
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

EMAIL_MESSAGE_SQL = REPO_ROOT / "entities/email_message/sql/dbo.email_message.sql"

REVIEWER_EMAIL = "pm@example.com"
REPLY_PUB = "reply-em-pub-u669a"

# The four reads U-669a fixed, plus the conversation read that already had it.
_READ_SPROCS_WITH_FOLDER = (
    "ReadEmailMessageById",
    "ReadEmailMessageByPublicId",
    "ReadEmailMessageByGraphMessageId",
    "ReadEmailMessagesPaginated",
    "ReadEmailMessagesByConversationId",
)


def _stub_db_row(**overrides):
    """Minimal pyodbc-like row with the attributes _from_db reads."""
    return SimpleNamespace(
        Id=1,
        PublicId="00000000-0000-0000-0000-000000000001",
        FromAddress=REVIEWER_EMAIL,
        **overrides,
    )


# ---------------------------------------------------------------------------
# 1. _from_db maps Folder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("folder", ["inbox", "sentitems"])
def test_from_db_maps_folder_when_present(folder):
    em = EmailMessageRepository()._from_db(_stub_db_row(Folder=folder))
    assert em.folder == folder


def test_from_db_folder_none_when_column_absent():
    em = EmailMessageRepository()._from_db(_stub_db_row())
    assert em.folder is None


# ---------------------------------------------------------------------------
# 2. Static SQL — every read sproc that feeds _from_db projects [Folder]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("proc_name", _READ_SPROCS_WITH_FOLDER)
def test_read_sproc_select_includes_folder(proc_name):
    body = strip_sql_comments(sproc_body(EMAIL_MESSAGE_SQL, proc_name))
    assert "[Folder]" in body, f"{proc_name} must project [Folder]"


# ---------------------------------------------------------------------------
# 3. End-to-end: the reply goes through the REAL _from_db before binding
# ---------------------------------------------------------------------------


def _bind_through_from_db(email_svc, **row_overrides):
    email_svc.return_value.read_by_public_id.return_value = (
        EmailMessageRepository()._from_db(_stub_db_row(**row_overrides))
    )
    return bind_reviewer_reply(
        reviewer_email=REVIEWER_EMAIL,
        reviewer_email_message_public_id=REPLY_PUB,
        what="test",
    )


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_accepts_inbox_reply_mapped_through_from_db(EmailSvc):
    bound = _bind_through_from_db(EmailSvc, Folder="inbox")
    assert bound.folder == "inbox"
    assert bound.from_address == REVIEWER_EMAIL


# A missing Folder must refuse like a non-inbox one — this pins fail-closed
# against a "fix" such as getattr(row, "Folder", "inbox").
@pytest.mark.parametrize(
    "row_overrides",
    [{"Folder": "sentitems"}, {}],
    ids=["sentitems", "folder_not_mapped"],
)
@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_refuses_non_inbox_mapped_through_from_db(EmailSvc, row_overrides):
    with pytest.raises(ReviewerProvenanceRefused, match="inbox"):
        _bind_through_from_db(EmailSvc, **row_overrides)
