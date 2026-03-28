from __future__ import annotations

import logging
import uuid
from typing import Optional

from core.workflow.api.process_engine import EventType
from core.workflow.business.process_registry import (
    get_initial_stage,
    requires_action,
)
from entities.email_thread.business.model import EmailThread
from entities.email_thread.persistence.repo import EmailThreadRepository
from entities.email_thread.persistence.stage_history_repo import EmailThreadStageHistoryRepository

logger = logging.getLogger(__name__)


class EmailThreadService:

    def __init__(
        self,
        thread_repo:        Optional[EmailThreadRepository]        = None,
        stage_history_repo: Optional[EmailThreadStageHistoryRepository] = None,
    ):
        self.thread_repo        = thread_repo        or EmailThreadRepository()
        self.stage_history_repo = stage_history_repo or EmailThreadStageHistoryRepository()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_by_public_id(self, public_id: str) -> Optional[EmailThread]:
        return self.thread_repo.read_by_public_id(public_id)

    def read_requiring_action(
        self, owner_user_id: Optional[int] = None
    ) -> list[EmailThread]:
        return self.thread_repo.read_requiring_action(owner_user_id=owner_user_id)

    # ------------------------------------------------------------------
    # Correct classification
    # ------------------------------------------------------------------

    def correct_classification(
        self,
        public_id:               str,
        new_classification_type: str,
        notes:                   Optional[str] = None,
        user_id:                 Optional[int] = None,
    ) -> EmailThread:
        """
        Correct a misclassified EmailThread.

        Steps:
          1. Load the existing thread — 404 if not found.
          2. Validate the new classification type against the process registry.
          3. Write a stage history record:
               from_stage   = current stage before correction
               to_stage     = RECEIVED (reset to start)
               triggered_by = USER_ACTION
               notes        = correction reason
          4. Upsert the thread with updated category, process_type,
             current_stage = RECEIVED, requires_action = True.

        Raises:
          ValueError  — thread not found, or invalid classification type.
        """
        thread = self.thread_repo.read_by_public_id(public_id)
        if not thread:
            raise ValueError(f"EmailThread '{public_id}' not found.")

        prior_stage           = thread.current_stage
        prior_classification  = thread.category

        # Derive the reset stage — always RECEIVED for email processes
        if new_classification_type == "UNKNOWN":
            reset_stage  = "RECEIVED"
            process_type = "UNKNOWN"
        else:
            reset_stage  = get_initial_stage(new_classification_type, registry_type="email")
            process_type = new_classification_type

        # Build the history note
        correction_note = (
            f"Owner corrected classification from '{prior_classification}' "
            f"to '{new_classification_type}'. "
            f"Thread reset from '{prior_stage}' to '{reset_stage}'."
        )
        if notes:
            correction_note += f" Reason: {notes}"

        # Write stage history — immutable audit record
        self.stage_history_repo.create(
            public_id=       str(uuid.uuid4()),
            email_thread_id= thread.id,
            from_stage=      prior_stage,
            to_stage=        reset_stage,
            triggered_by=    EventType.USER_ACTION.value,
            user_id=         user_id,
            notes=           correction_note,
        )

        # Update the thread
        action_required = requires_action(
            new_classification_type,
            reset_stage,
            registry_type="email",
        ) if new_classification_type != "UNKNOWN" else True

        updated_thread = self.thread_repo.upsert(
            public_id=       thread.public_id,
            inbox_record_id= thread.inbox_record_id,
            category=        new_classification_type,
            process_type=    process_type,
            current_stage=   reset_stage,
            is_reply=        thread.is_reply or False,
            is_forward=      thread.is_forward or False,
            internet_message_id= thread.internet_message_id,
            subject=         thread.subject,
            owner_user_id=   thread.owner_user_id,
            is_resolved=     False,
            requires_action= action_required,
        )

        logger.info(
            f"EmailThread '{public_id}' classification corrected: "
            f"'{prior_classification}' → '{new_classification_type}'. "
            f"Stage reset: '{prior_stage}' → '{reset_stage}'."
        )

        return updated_thread
