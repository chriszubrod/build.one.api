# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.review.business.recipient_model import ResolvedRecipient
from entities.review.persistence.recipient_repo import ReviewRecipientRepository

logger = logging.getLogger(__name__)


class ReviewRecipientService:
    """
    Resolves the list of users to notify when a Review is submitted on
    a parent entity. v1 supports Bill only (the email-agent flow).

    The result envelope is `{"to": [...], "cc": [...]}` where:
      - `to`: users with Role 'Project Manager' on any project the bill spans
      - `cc`: users with Role 'Owner'

    A user holding both roles across the bill's projects appears once in
    `to` (PM precedence). Recipients without an email address are
    included with `email=None` — the caller is responsible for filtering
    + logging unreachable recipients.
    """

    def __init__(self, repo: Optional[ReviewRecipientRepository] = None):
        self.repo = repo or ReviewRecipientRepository()

    def resolve_for_bill(
        self,
        *,
        bill_id: int,
        exclude_user_id: Optional[int] = None,
    ) -> dict[str, list[ResolvedRecipient]]:
        rows = self.repo.resolve_for_bill(
            bill_id=bill_id,
            exclude_user_id=exclude_user_id,
        )
        to_list = [r for r in rows if r.role_name == "Project Manager"]
        cc_list = [r for r in rows if r.role_name == "Owner"]
        return {"to": to_list, "cc": cc_list}
