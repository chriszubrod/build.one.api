"""SharePoint completion fan-out shared by Bill / BillCredit / Expense (U-757a) and Invoice (U-757b).

Two layers, formerly hand-copied into each entity service:

- ``enqueue_attachment_file`` — the per-FILE core: enqueue one SharePoint
  upload on the durable MS outbox, classify the result via
  ``sharepoint_upload_outcome``, and credit ``SharePointUploadTally`` (or record
  the caller's refused-error). Folder-agnostic: also used directly by the
  expense general-receipts loop and the invoice line / packet loops (U-757b).
- ``upload_to_project_module_folder`` — the module-folder engine: resolve the
  module -> the project's module folder (unmapped -> mapping-missing issue) ->
  drive -> vendor -> project -> (storage) -> per-line link / dedupe / attachment
  / filename / (blob probe) / enqueue, returning the entity's result dict.

The three entities differ in many small, OBSERVABLE ways (result-dict shapes,
messages, error-dict keys, logs, prints, module fallbacks, the expense holding
deferral and blob probe). Each difference is an explicit ``SharePointModuleSpec``
field, and the MS outbox worker (``_handle_expense_pull_fanout``) keys on the
expense message / error STRINGS, so every literal here is load-bearing.

Behaviour is pinned by ``tests/test_u757a_sharepoint_fanout_characterization.py``.
The entity services keep their private ``_upload_attachments_to_module_folder``
methods as delegations that build a ``SharePointModuleSpec`` per call from their
own module globals and instance attributes, so every existing patch point keeps
working (``<entity module>.AzureBlobStorage``,
``<entity module>._record_project_mapping_missing_ms_issue``, the instance
services). The filename builder stays in each entity, VERBATIM, as a callable:
the outbox idempotency guard keys on filename, so any drift re-uploads history.
Log records go to the CALLER's logger (``spec.logger``) so logger names are
unchanged. ``MsOutboxService`` / ``SharePointUploadTally`` /
``sharepoint_upload_outcome`` are imported lazily from their source module at
call time — patching ``integrations.ms.outbox.business.service.MsOutboxService``
is still the way to fake the outbox.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

REFUSED_ERROR_RICH = "SharePoint upload enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)"
REFUSED_ERROR_PLAIN = "SharePoint upload enqueue refused (ALLOW_MS_WRITES=false)"
_MISSING_BLOB_ERROR = "Attachment not found or missing blob_url"


@dataclass(frozen=True, kw_only=True)
class SharePointModuleSpec:
    """Per-call knobs for ``upload_to_project_module_folder``.

    Identity
      entity_type             outbox ``entity_type`` and the mapping-missing issue's entity_type.
      entity                  the bill / credit / expense. ``public_id`` and ``vendor_id`` are read
                              inside the engine's try, at the step that needs them (not at spec
                              build) — an entity missing them fails exactly where it used to.
      logger                  the entity module's logger.

    Collaborators (callables, so lazy instance properties are touched inside the
    engine's try — exactly where the original methods touched them)
      read_module_by_name     name -> module or None; tried over ``module_names`` in order.
      read_all_modules        optional last resort: ``read_all()[0]`` (bill credit only).
      get_folder_for_module   (project_id=, module_id=) -> folder dict or None.
      record_mapping_missing  the entity module's ``_record_project_mapping_missing_ms_issue``.
      read_drive              ms_drive_id -> drive or None.
      read_vendor             vendor id -> vendor or None; called only when ``entity.vendor_id`` is truthy.
      read_project            project id (str) -> project or None.
      read_attachment_link    line public_id -> link (``.attachment_id``) or None.
      read_attachment         attachment id -> attachment or None.
      build_filename          (line_item, vendor, project, attachment) -> SharePoint filename.
      storage_factory         optional; constructed after the project read (bill, expense). Bill
                              never uses the instance (it does not probe): the construction is kept
                              only because its failure is an observable "Failed to initialize
                              storage: …" early return (pinned by the goldens). Required when
                              ``probe_blob``.

    Behaviour flags (each one is an observable difference between the entities)
      module_missing_message / module_missing_error   the no-module early return's texts.
      module_id_required      True: ``int(module.id)`` unconditionally (expense — a None id
                              raises into the outer handler); False: ``int(id) if id else None``.
      storage_error_prefix    prefix on the storage-init failure texts.
      probe_blob              download each blob before enqueueing (expense); a failure is a
                              per-line error, not an enqueue.
      has_holding_line /      optional U-692a deferral (expense; set both or neither): True returns
      holding_deferred_result ``holding_deferred_result()`` before any other read, and the loop and
                              outer-error results carry ``deferred_holding: False``.
      early_return_skipped_count  include ``skipped_count`` on the pre-loop early returns.
      lenient_success         True (bill/credit): success = synced>0 or skipped>0 or no errors,
                              message carries the error count. False (expense): success = no
                              errors, message has no error count.
      rich_errors             True (bill/credit): refused / per-line-exception error dicts carry
                              ``line_item_public_id``, the exception text is "Unexpected error: …",
                              the refused text names "or enqueue failure", and both are logged.
                              False (expense): ``{"line_item_id", "error"}``, nothing logged.
      outer_error_prefix      prefix on the outer-exception ``message`` ("Error: " for bill/credit).
      narration               NARRATION_SILENT (default) | NARRATION_BILL | NARRATION_CREDIT.
    """

    entity_type: str
    entity: Any
    logger: logging.Logger

    read_module_by_name: Callable[[str], Any]
    module_names: Tuple[str, ...]
    read_all_modules: Optional[Callable[[], Any]] = None
    get_folder_for_module: Callable[..., Any]
    record_mapping_missing: Callable[..., None]
    read_drive: Callable[[Any], Any]
    read_vendor: Callable[[Any], Any]
    read_project: Callable[[str], Any]
    read_attachment_link: Callable[[Any], Any]
    read_attachment: Callable[[Any], Any]
    build_filename: Callable[[Any, Any, Any, Any], str]
    storage_factory: Optional[Callable[[], Any]] = None

    module_missing_message: str
    module_missing_error: str
    module_id_required: bool = False
    storage_error_prefix: str = ""
    probe_blob: bool = False
    has_holding_line: Optional[Callable[[], bool]] = None
    holding_deferred_result: Optional[Callable[[], Dict[str, Any]]] = None
    early_return_skipped_count: bool = True
    lenient_success: bool = False
    rich_errors: bool = False
    outer_error_prefix: str = ""
    narration: "_Narration" = None  # None = NARRATION_SILENT (the instances are defined below)

    def __post_init__(self):
        if self.narration is None:
            object.__setattr__(self, "narration", NARRATION_SILENT)
        if (self.has_holding_line is None) != (self.holding_deferred_result is None):
            raise ValueError("has_holding_line and holding_deferred_result go together")
        if self.probe_blob and self.storage_factory is None:
            raise ValueError("probe_blob needs a storage_factory")


# --------------------------------------------------------------------------- core


def enqueue_attachment_file(
    *,
    ms_outbox,
    tally,
    entity_type: str,
    entity_public_id: str,
    drive_id: Any,
    parent_item_id: Any,
    filename: str,
    content_type: str,
    blob_path: str,
    attachment_id: Any,
    tally_key: Any,
    refused_error: Callable[[], Dict[str, Any]],
) -> Tuple[str, Any]:
    """Enqueue ONE file's SharePoint upload and tally it.

    The worker fetches ``blob_path`` at drain time, uploads the raw blob (inline
    compression was dropped in Phase 3 — see TODO.md "Packet compression") and
    links the resulting DriveItem back via ``attachment_id``. Returns ``(outcome, queued)`` where outcome
    is ``'refused' | 'skipped' | 'synced'``; a refusal records ``refused_error()``
    (built only then, as the originals read the line item only on that path),
    anything else credits ``tally_key`` once (``SharePointUploadTally`` counts files).
    """
    from integrations.ms.outbox.business.service import sharepoint_upload_outcome

    queued = ms_outbox.enqueue_sharepoint_upload(
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        drive_id=drive_id,
        parent_item_id=parent_item_id,
        filename=filename,
        content_type=content_type,
        blob_path=blob_path,
        attachment_id=attachment_id,
    )
    outcome = sharepoint_upload_outcome(queued)
    if outcome == "refused":
        tally.record_error(refused_error())
    else:
        tally.record(tally_key, outcome)
    return outcome, queued


def _line_error(line_item, text: str, *, rich: bool) -> Dict[str, Any]:
    """The engine's per-line error dict; ``rich`` adds ``line_item_public_id`` (key order is observable)."""
    if rich:
        return {"line_item_id": line_item.id, "line_item_public_id": line_item.public_id, "error": text}
    return {"line_item_id": line_item.id, "error": text}


# --------------------------------------------------------------------------- narration


class _Narration:
    """Per-line chatter. The silent base is the expense behaviour."""

    def start(self, log, line_count, project_id):
        pass

    def checking(self, log, line_item):
        pass

    def no_attachment(self, log, line_item):
        pass

    def found(self, log, line_item, attachment_id):
        pass

    def duplicate(self, log, attachment_id):
        pass

    def queued(self, log, filename, queued, attachment, outcome):
        pass


class _BillNarration(_Narration):
    noun = "bill"

    def start(self, log, line_count, project_id):
        log.info(f"SharePoint sync: Processing {line_count} line items for project {project_id}")

    def no_attachment(self, log, line_item):
        log.debug(f"No attachment for line item {line_item.public_id}")

    def found(self, log, line_item, attachment_id):
        log.debug(f"Found attachment for line item {line_item.public_id}, attachment_id={attachment_id}")

    def duplicate(self, log, attachment_id):
        log.info(f"Attachment {attachment_id} already enqueued for this {self.noun}, skipping duplicate line item")

    def queued(self, log, filename, queued, attachment, outcome):
        log.info(
            f"Queued SharePoint upload: '{filename}' "
            f"(outbox {queued.public_id}, attachment_id={attachment.id}, outcome={outcome})"
        )


class _CreditNarration(_BillNarration):
    noun = "credit"

    def start(self, log, line_count, project_id):
        print(f"  SharePoint sync: Processing {line_count} line items for project {project_id}")
        super().start(log, line_count, project_id)

    def checking(self, log, line_item):
        print(f"    Checking line item {line_item.public_id}")
        log.info(f"  Checking line item {line_item.public_id}")

    def no_attachment(self, log, line_item):
        print(f"      -> No attachment for this line item")
        log.info(f"    No attachment for line item {line_item.public_id}")

    def found(self, log, line_item, attachment_id):
        print(f"      -> Found attachment, attachment_id={attachment_id}")
        log.info(f"    Found attachment link for line item {line_item.public_id}, attachment_id={attachment_id}")

    def queued(self, log, filename, queued, attachment, outcome):
        log.info(
            f"Queued SharePoint upload: '{filename}' "
            f"(outbox {queued.public_id}, attachment_id={attachment.id})"
        )


# Per-line narration (logs / prints) — the only per-entity difference in the loop's chatter.
NARRATION_SILENT = _Narration()  # expense
NARRATION_BILL = _BillNarration()
NARRATION_CREDIT = _CreditNarration()


# --------------------------------------------------------------------------- engine


def _early_return(spec: SharePointModuleSpec, message: str, error: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"success": False, "message": message, "synced_count": 0}
    if spec.early_return_skipped_count:
        result["skipped_count"] = 0
    result["errors"] = [{"error": error}]
    return result


def _resolve_module(spec: SharePointModuleSpec):
    module = None
    for name in spec.module_names:
        module = spec.read_module_by_name(name)
        if module:
            return module
    if spec.read_all_modules is not None:
        all_modules = spec.read_all_modules()
        module = all_modules[0] if all_modules else None
    return module


def upload_to_project_module_folder(
    spec: SharePointModuleSpec, line_items, project_id
) -> Dict[str, Any]:
    """Enqueue the line items' attachments into ``project_id``'s module folder.

    Counting is per FILE (``SharePointUploadTally``): an attachment shared by
    several line items is one upload and counts once. Never raises — every
    failure comes back as a result dict.
    """
    log = spec.logger
    narration = spec.narration
    try:
        if spec.has_holding_line is not None and spec.has_holding_line():
            return spec.holding_deferred_result()

        module = _resolve_module(spec)
        if not module:
            return _early_return(spec, spec.module_missing_message, spec.module_missing_error)

        if spec.module_id_required:
            module_id = int(module.id)
        else:
            module_id = int(module.id) if module.id else None

        module_folder = spec.get_folder_for_module(project_id=project_id, module_id=module_id)
        if not module_folder:
            unmapped = f"Module folder not linked for project {project_id}"
            spec.record_mapping_missing(
                entity_type=spec.entity_type,
                entity_public_id=str(spec.entity.public_id),
                project_id=project_id,
                mapping_kind="module_folder",
                details=unmapped,
            )
            return _early_return(spec, unmapped, unmapped)

        folder_ms_drive_id = module_folder.get("ms_drive_id")
        folder_item_id = module_folder.get("item_id")
        if not folder_ms_drive_id or not folder_item_id:
            return _early_return(spec, "Module folder missing drive or item_id", "Module folder missing drive or item_id")

        drive = spec.read_drive(folder_ms_drive_id)
        if not drive:
            return _early_return(spec, "Drive not found", "Drive not found")

        vendor_id = spec.entity.vendor_id
        vendor = spec.read_vendor(vendor_id) if vendor_id else None
        if not vendor:
            return _early_return(spec, "Vendor not found", "Vendor not found")

        project = spec.read_project(str(project_id))
        if not project:
            return _early_return(spec, f"Project {project_id} not found", f"Project {project_id} not found")

        storage = None
        if spec.storage_factory is not None:
            try:
                storage = spec.storage_factory()
            except Exception as e:
                text = f"{spec.storage_error_prefix}{str(e)}"
                return _early_return(spec, text, text)

        from integrations.ms.outbox.business.service import MsOutboxService, SharePointUploadTally

        tally = SharePointUploadTally()
        ms_outbox = MsOutboxService()

        narration.start(log, len(line_items), project_id)

        for line_item in line_items:
            narration.checking(log, line_item)
            try:
                if not line_item.public_id:
                    continue

                attachment_link = spec.read_attachment_link(line_item.public_id)
                if not attachment_link or not attachment_link.attachment_id:
                    narration.no_attachment(log, line_item)
                    continue
                narration.found(log, line_item, attachment_link.attachment_id)

                # Same file on another line item — already enqueued; the tally counts files.
                if tally.already_enqueued(attachment_link.attachment_id):
                    narration.duplicate(log, attachment_link.attachment_id)
                    continue

                attachment = spec.read_attachment(attachment_link.attachment_id)
                if not attachment or not attachment.blob_url:
                    tally.record_error(_line_error(line_item, _MISSING_BLOB_ERROR, rich=True))
                    continue

                sharepoint_filename = spec.build_filename(line_item, vendor, project, attachment)

                if spec.probe_blob:
                    # Fail fast on a missing blob; the worker re-fetches at drain time.
                    try:
                        _file_content, _metadata = storage.download_file(attachment.blob_url)
                    except Exception as e:
                        tally.record_error(_line_error(line_item, str(e), rich=False))
                        continue

                refused_text = REFUSED_ERROR_RICH if spec.rich_errors else REFUSED_ERROR_PLAIN
                refused_error = lambda: _line_error(line_item, refused_text, rich=spec.rich_errors)

                outcome, queued = enqueue_attachment_file(
                    ms_outbox=ms_outbox,
                    tally=tally,
                    entity_type=spec.entity_type,
                    entity_public_id=str(spec.entity.public_id),
                    drive_id=drive.drive_id,
                    parent_item_id=folder_item_id,
                    filename=sharepoint_filename,
                    content_type=attachment.content_type or "application/octet-stream",
                    blob_path=attachment.blob_url,
                    attachment_id=attachment.id,
                    tally_key=attachment_link.attachment_id,
                    refused_error=refused_error,
                )
                if outcome == "refused":
                    if spec.rich_errors:
                        log.error(f"SharePoint upload enqueue refused for '{sharepoint_filename}'")
                    continue
                narration.queued(log, sharepoint_filename, queued, attachment, outcome)

            except Exception as e:
                if spec.rich_errors:
                    log.exception(f"Error processing line item {line_item.id}")
                    tally.record_error(_line_error(line_item, f"Unexpected error: {str(e)}", rich=True))
                else:
                    tally.record_error(_line_error(line_item, str(e), rich=False))

        if spec.lenient_success:
            success = tally.synced_count > 0 or tally.skipped_count > 0 or len(tally.errors) == 0
            result = tally.as_dict(success=success, message=tally.message(with_error_count=True))
        else:
            result = tally.as_dict(success=not tally.errors, message=tally.message())
        if spec.has_holding_line is not None:
            result["deferred_holding"] = False
        return result

    except Exception as e:
        log.exception(f"Error uploading attachments for project {project_id}")
        result = {
            "success": False,
            "message": f"{spec.outer_error_prefix}{str(e)}",
            "synced_count": 0,
            "skipped_count": 0,
        }
        if spec.has_holding_line is not None:
            result["deferred_holding"] = False
        result["errors"] = [{"error": str(e)}]
        return result
