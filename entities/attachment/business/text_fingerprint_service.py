"""Write path for attachment document-text fingerprints (U-723)."""
from __future__ import annotations

import logging
from typing import Optional

from entities.attachment.business.text_fingerprint import (
    FINGERPRINT_VERSION,
    fingerprint_pdf_bytes,
)
from entities.attachment.persistence.text_fingerprint_repo import (
    AttachmentTextFingerprintRepository,
)

logger = logging.getLogger(__name__)


class AttachmentTextFingerprintService:
    def __init__(
        self,
        *,
        repo: Optional[AttachmentTextFingerprintRepository] = None,
    ) -> None:
        self._repo = repo or AttachmentTextFingerprintRepository()

    def upsert_from_pdf_bytes(
        self,
        *,
        attachment_id: int,
        pdf_bytes: bytes,
        content_type: str,
    ) -> None:
        fingerprint = fingerprint_pdf_bytes(pdf_bytes, content_type=content_type)
        if not fingerprint:
            return
        self._repo.upsert(
            attachment_id=attachment_id,
            version=FINGERPRINT_VERSION,
            fingerprint=fingerprint,
        )


def persist_text_fingerprint_failure_isolated(
    *,
    attachment_id: int,
    pdf_bytes: bytes,
    content_type: str,
) -> None:
    try:
        AttachmentTextFingerprintService().upsert_from_pdf_bytes(
            attachment_id=attachment_id,
            pdf_bytes=pdf_bytes,
            content_type=content_type,
        )
    except Exception as error:
        logger.warning(
            "attachment_text_fingerprint.persist_failed attachment_id=%s err=%s",
            attachment_id,
            type(error).__name__,
        )
