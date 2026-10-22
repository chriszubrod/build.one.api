"""Write path for attachment text token hashes (U-701a)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from entities.attachment.business.extraction_envelope import extraction_payload_is_current
from entities.attachment.persistence.text_token_hash_repo import AttachmentTextTokenHashRepository
from entities.coding_suggestion.business.bill_text_token_hash import (
    current_tokenizer_version,
    feature_enabled,
    token_hashes_from_text,
    token_hashes_to_storage,
    warn_once_if_disabled,
)

logger = logging.getLogger(__name__)


class AttachmentTextTokenHashService:
    def __init__(
        self,
        *,
        repo: Optional[AttachmentTextTokenHashRepository] = None,
        settings: Optional[object] = None,
    ) -> None:
        import config

        self._settings = settings or config.Settings()
        self._repo = repo or AttachmentTextTokenHashRepository()

    def upsert_from_extraction_envelope(
        self,
        *,
        attachment_id: int,
        envelope: dict[str, Any],
    ) -> None:
        if not feature_enabled(self._settings):
            warn_once_if_disabled()
            return
        if not extraction_payload_is_current(envelope):
            return
        content = envelope.get("content")
        if not isinstance(content, str) or not content.strip():
            return
        key = getattr(self._settings, "bill_text_token_hash_key", None)
        version = current_tokenizer_version(self._settings)
        if not key or not version:
            return
        tokens = token_hashes_from_text(content, str(key))
        stored, count = token_hashes_to_storage(tokens)
        self._repo.upsert(
            attachment_id=attachment_id,
            tokenizer_version=version,
            token_hashes=stored,
            token_count=count,
        )


def persist_token_hashes_failure_isolated(
    *,
    attachment_id: int,
    envelope: dict[str, Any],
    settings: Optional[object] = None,
) -> None:
    try:
        AttachmentTextTokenHashService(settings=settings).upsert_from_extraction_envelope(
            attachment_id=attachment_id,
            envelope=envelope,
        )
    except Exception as error:
        logger.warning(
            "attachment_text_token_hash.persist_failed err=%s",
            type(error).__name__,
        )
