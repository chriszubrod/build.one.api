"""Box completion fan-out shared by Bill / Expense / BillCredit / Invoice (U-756).

Two passive, failure-isolated completion side-effects, formerly hand-copied
into each entity service:

- ``enqueue_box_excel`` — one ``update_box_excel`` outbox row (or, for an
  invoice, an UPDATE-only DRAW REQUEST stamp) when the project has a mapped
  Box workbook.
- ``enqueue_box_uploads`` — one Box document upload per unique
  (project, attachment) pair, routed to the project's ``'invoices'`` folder.

Both early-return when ``ALLOW_BOX_WRITES`` is not ``'true'`` (skipping the DB
legwork, not just the enqueue) and swallow every exception so Box can never
affect the completion flow.

Behaviour is pinned by ``tests/test_u756_box_fanout_characterization.py``.
The entity services keep their private ``_enqueue_box_*`` methods as one-line
delegations that build a ``BoxUploadSpec`` per call from their own instance
attributes, so every existing patch point keeps working. Log records go to the
CALLER's logger (``spec.logger`` / ``logger=``) so logger names are unchanged.

Box collaborators are imported lazily from their source modules at call time —
patching ``integrations.box.*.<Class>`` is still the way to fake them.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

# Filename strategies for the uploaded Box document.
FILENAME_ORIGINAL = "original"  # attachment.original_filename or .filename or "document"
FILENAME_MODULE = "module_filename"  # owner._build_module_filename(...) with the original fallback

# Helper results that are never an acceptable filename (empty base name).
_BARE_MODULE_FILENAMES = (".pdf", ".")


def box_writes_enabled() -> bool:
    """The ALLOW_BOX_WRITES gate, read at call time (case-insensitive, trimmed 'true')."""
    return os.getenv("ALLOW_BOX_WRITES", "").strip().lower() == "true"


@dataclass(frozen=True)
class BoxUploadSpec:
    """Per-entity knobs for ``enqueue_box_uploads``.

    entity_type          outbox ``entity_type`` AND the log key (``box.enqueue.failed <entity_type>=...``).
    read_attachment_link line-item public_id -> link row (``.attachment_id``) or None.
    read_attachment      attachment id -> attachment row or None.
    logger               the entity module's logger.
    filename_strategy    FILENAME_ORIGINAL or FILENAME_MODULE.
    owner                the entity service — FILENAME_MODULE reads ``owner.vendor_service``,
                         ``owner.project_service`` and calls ``owner._build_module_filename``.
    has_holding_line     optional ``() -> bool``; True defers the whole upload (U-692a).
    """

    entity_type: str
    read_attachment_link: Callable[[Any], Any]
    read_attachment: Callable[[Any], Any]
    logger: logging.Logger
    filename_strategy: str = FILENAME_ORIGINAL
    owner: Any = None
    has_holding_line: Optional[Callable[[], bool]] = None


def enqueue_box_excel(*, entity_type: str, entity, project_id: int, logger: logging.Logger,
                      draw_stamp: bool = False) -> None:
    """Enqueue a Box DETAILS-tab update for ``entity``'s project.

    ``draw_stamp=True`` (invoice) enqueues the UPDATE-only DRAW REQUEST stamp via
    ``enqueue_box_excel_draw_stamp(invoice_public_id=...)``; otherwise
    ``enqueue_box_excel(entity_type=..., entity_public_id=...)``.
    """
    if not box_writes_enabled():
        return  # gate closed — skip the DB legwork, not just the enqueue
    try:
        from integrations.box.excel.business.mapping_service import (
            BoxProjectWorkbookService,
        )
        from integrations.box.outbox.business.service import BoxOutboxService

        mapping = BoxProjectWorkbookService().read_by_project_id(project_id)
        if not mapping:
            logger.info(f"box.excel.skipped_unmapped_project project_id={project_id}")
            return
        if draw_stamp:
            BoxOutboxService().enqueue_box_excel_draw_stamp(
                invoice_public_id=str(entity.public_id),
                project_id=project_id,
                box_file_id=mapping["box_file_id"],
                worksheet_name=mapping["worksheet_name"],
            )
        else:
            BoxOutboxService().enqueue_box_excel(
                entity_type=entity_type,
                entity_public_id=str(entity.public_id),
                project_id=project_id,
                box_file_id=mapping["box_file_id"],
                worksheet_name=mapping["worksheet_name"],
            )
    except Exception as e:
        logger.warning(f"box.excel.enqueue.failed {entity_type}={entity.public_id} project_id={project_id}: {e}")


def enqueue_box_uploads(spec: BoxUploadSpec, entity, line_items, doc_kind: str) -> None:
    """Enqueue Box uploads for ``entity``'s line-item attachments (one per unique
    (project, attachment) pair; unmapped projects skipped with one info log each)."""
    if not box_writes_enabled():
        return  # gate closed — skip the DB legwork, not just the enqueue
    log = spec.logger
    label = spec.entity_type
    try:
        if spec.has_holding_line is not None and spec.has_holding_line():
            log.info(f"box.enqueue.deferred_holding {label}={entity.public_id}")
            return
        from integrations.box.folder.business.service import (
            BoxProjectFolderService,
            DOC_CLASS_INVOICES,
        )
        from integrations.box.outbox.business.service import BoxOutboxService

        folder_service = BoxProjectFolderService()
        box_outbox = BoxOutboxService()
        mapping_cache: dict = {}  # project_id -> mapping dict or None
        enqueued_keys = set()  # (project_id, attachment_id)

        # FILENAME_MODULE-only state; bound up front so nothing is ever unbound.
        owner = None
        vendor = None
        project_cache: Optional[dict] = None  # project_id -> project object (for filename)
        line_items_count: Optional[int] = None
        module_filename = spec.filename_strategy == FILENAME_MODULE
        if module_filename:
            owner = spec.owner
            project_cache = {}
            line_items_count = len(line_items)
            # Look up vendor once per entity (needed by _build_module_filename).
            vendor = owner.vendor_service.read_by_id(id=entity.vendor_id) if entity.vendor_id else None

        for line_item in line_items:
            try:
                if not line_item.project_id or not line_item.public_id:
                    continue
                project_id = line_item.project_id
                if project_id not in mapping_cache:
                    # Bill / expense / credit-memo docs all file to the project's
                    # "14 - Invoices" (AP/'invoices') folder.
                    mapping_cache[project_id] = folder_service.read_mapping_by_project_id_and_class(
                        project_id, DOC_CLASS_INVOICES
                    )
                    if mapping_cache[project_id] is None:
                        log.info(f"box.enqueue.skipped_unmapped_project project_id={project_id} doc_class=invoices")
                mapping = mapping_cache[project_id]
                if not mapping:
                    continue
                attachment_link = spec.read_attachment_link(line_item.public_id)
                if not attachment_link or not attachment_link.attachment_id:
                    continue
                if (project_id, attachment_link.attachment_id) in enqueued_keys:
                    continue
                attachment = spec.read_attachment(attachment_link.attachment_id)
                if not attachment or not attachment.blob_url:
                    continue

                if module_filename:
                    # Project lookup for the human-readable filename (cached per entity).
                    if project_id not in project_cache:
                        project_cache[project_id] = owner.project_service.read_by_id(id=str(project_id))
                    box_filename = owner._build_module_filename(
                        bill=entity,
                        line_item=line_item,
                        vendor=vendor,
                        project=project_cache[project_id],
                        bill_line_items_count=line_items_count,
                        attachment=attachment,
                    )
                    # Fallback if the helper produced an empty base (missing vendor/
                    # project/etc.) — never enqueue a bare `.pdf` or empty name.
                    if not box_filename or box_filename in _BARE_MODULE_FILENAMES:
                        box_filename = attachment.original_filename or attachment.filename or "document"
                else:
                    box_filename = attachment.original_filename or attachment.filename or "document"

                box_outbox.enqueue_box_upload(
                    entity_type=spec.entity_type,
                    entity_public_id=str(entity.public_id),
                    doc_kind=doc_kind,
                    blob_path=attachment.blob_url,
                    filename=box_filename,
                    content_type=attachment.content_type or "application/octet-stream",
                    box_folder_id=mapping["box_folder_id"],
                    attachment_id=attachment.id,
                    project_id=project_id,
                )
                enqueued_keys.add((project_id, attachment_link.attachment_id))
            except Exception as line_error:
                log.warning(
                    f"box.enqueue.failed {label}={entity.public_id} line_item={line_item.id}: {line_error}"
                )
    except Exception as e:
        log.warning(f"box.enqueue.failed {label}={entity.public_id}: {e}")
