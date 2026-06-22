# Python Standard Library Imports
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# Local Imports
from integrations.box.base.errors import (
    BoxError,
    BoxLockedError,
    BoxServerError,
)
from integrations.box.base.locking import box_app_lock
from integrations.box.base.logger import get_box_logger

logger = get_box_logger(__name__)


WORKBOOK_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# How long our Box file lock is held. Long enough to download + edit + upload a
# version, short enough that a crash doesn't strand the file for humans for long
# (Box auto-expires the lock at this horizon even if our unlock never runs).
BOX_LOCK_TTL_SECONDS = 300  # 5 minutes

# Operation discriminator string constants. Shared across enqueue (BoxOutboxService
# writes the value) and drain (BoxExcelUpdateService reads + branches), so a typo
# at the enqueue site is impossible. handle() dead-letters any payload whose
# operation is outside KNOWN_OPERATIONS — a rolling deploy that ships a new
# enqueue path without the matching worker branch fails loudly instead of
# silently falling through to "insert".
OP_INSERT = "insert"
OP_STAMP_DRAW_REQUEST = "stamp_draw_request"
KNOWN_OPERATIONS = (OP_INSERT, OP_STAMP_DRAW_REQUEST)


class BoxExcelUpdateService:
    """
    Drain-time handler for `KIND_UPDATE_EXCEL` Box outbox rows.

    Box has no cell-level / workbook API, so — unlike the MS Graph Excel path —
    ALL read / idempotency / insertion / write happens HERE, in the drain
    handler: download the whole .xlsx, edit the DETAILS sheet with openpyxl
    (formulas preserved), upload a NEW VERSION. The enqueue is lightweight.

    Serialization is belt-and-suspenders: a process-local-and-cross-process
    `box_app_lock` keyed on the file, PLUS a real Box file lock (so a human in
    Excel-for-web can't co-edit underneath us — and we back off if they already
    hold one).
    """

    def handle(self, row, payload: Dict[str, Any]) -> None:
        """
        Algorithm (per the Phase-3 contract):

          1. Pull box_file_id / worksheet / entity refs from payload.
          2. Acquire box_app_lock(box_file_write:{id}); not got → retryable.
          3. GET file meta (etag, lock, name). A live human WOPI co-edit lock →
             BoxLockedError (retryable contention; don't fight it).
          4. Take a Box lock (PUT lock, if_match=etag0); capture the bumped
             etag1 from the lock response.
          5. Build rows fresh; download the .xlsx; apply rows with openpyxl.
          6. bytes is None (all keys present) → skip upload (still unlock).
             else → upload_file_version(if_match=etag1) + register
             [box].[File] (Kind='workbook') + [box].[PushLog].
          7. finally: best-effort unlock (swallow + log).

        Error split is handled by the outbox worker via the typed BoxError
        hierarchy: BoxPreconditionError(412) and BoxLockedError(403/WOPI) are
        retryable; everything else dead-letters + escalates to a critical
        ReconciliationIssue (same as the upload kind).
        """
        box_file_id = payload.get("box_file_id")
        worksheet_name = payload.get("worksheet_name") or "DETAILS"
        entity_type = payload.get("entity_type") or row.entity_type
        entity_public_id = payload.get("entity_public_id") or row.entity_public_id
        project_id = payload.get("project_id")
        # Batch rows carry a list of {entity_type, entity_public_id}; single rows
        # carry one entity ref. Only the row-build differs — the lock / download /
        # apply / upload path below is identical.
        entities = payload.get("entities")
        # Operation discriminator. Absent → "insert" (bill/expense/bill_credit
        # path, builds new DETAILS rows). "stamp_draw_request" → invoice path,
        # only updates column H on existing rows by col-Z match.
        operation = (payload.get("operation") or "insert").strip().lower()

        # KNOWN_OPERATIONS is the closed set we recognize; anything else gets
        # dead-lettered HERE rather than silently running the insert branch,
        # so a future operation kind added to enqueue-side code is forced to
        # land its worker-side branch in the same deploy (catches the BR-MAIN-25
        # rolling-deploy race in reverse).
        if operation not in KNOWN_OPERATIONS:
            raise ValueError(
                f"update_box_excel payload has unknown operation {operation!r} "
                f"(expected one of {KNOWN_OPERATIONS})"
            )

        if not box_file_id:
            raise ValueError("update_box_excel payload missing box_file_id")
        if entities is not None:
            if not isinstance(entities, list) or not entities:
                raise ValueError("update_box_excel batch payload has empty/invalid entities")
            if operation == OP_STAMP_DRAW_REQUEST:
                # stamp_draw_request is single-entity; reject batch payloads
                # HERE so we don't waste a Box file lock acquire before
                # discovering the misconfiguration inside _run_locked.
                raise ValueError(
                    "update_box_excel: operation='stamp_draw_request' is not "
                    "compatible with a batch 'entities' payload — use one row "
                    "per invoice"
                )
        elif not entity_type or not entity_public_id:
            raise ValueError(
                "update_box_excel payload missing entity_type / entity_public_id"
            )

        # Step 2: cross-process serialization on the file. Lock busy → raise a
        # retryable error so the row requeues (do NOT mark done).
        with box_app_lock(f"box_file_write:{box_file_id}") as got:
            if not got:
                raise BoxServerError(
                    f"box_app_lock busy for file {box_file_id}; requeue",
                    request_method="LOCK",
                    request_path=f"files/{box_file_id}",
                )

            from integrations.box.base.client import BoxHttpClient

            with BoxHttpClient() as client:
                self._run_locked(
                    client=client,
                    row=row,
                    box_file_id=str(box_file_id),
                    worksheet_name=worksheet_name,
                    entity_type=entity_type,
                    entity_public_id=str(entity_public_id),
                    project_id=project_id,
                    entities=entities,
                    operation=operation,
                )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _run_locked(
        self,
        *,
        client,
        row,
        box_file_id: str,
        worksheet_name: str,
        entity_type: str,
        entity_public_id: str,
        project_id: Optional[int],
        entities: Optional[list] = None,
        operation: str = "insert",
    ) -> None:
        # Step 3: read meta (etag + lock + name).
        meta = client.get(
            f"files/{box_file_id}",
            params={"fields": "etag,lock,name"},
            operation_name="box.excel.get_meta",
        )
        lock = meta.get("lock")
        if self._is_live_human_lock(lock):
            # A human co-edit session is live (Office-for-web WOPI lock). Don't
            # fight it — raise a retryable contention error and log it so the
            # deferral is visible. The runbook (box-excel-conflict-storm) covers
            # the workday-scale escalation if a sheet stays open for days.
            logger.warning(
                "box.outbox.excel.deferred_locked",
                extra={
                    "event_name": "box.outbox.excel.deferred_locked",
                    "box_file_id": box_file_id,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "lock_app_type": str((lock or {}).get("app_type")),
                },
            )
            raise BoxLockedError(
                f"file {box_file_id} held by a live human co-edit (WOPI) lock; defer",
                code="item_locked",
                request_method="GET",
                request_path=f"files/{box_file_id}",
            )

        etag0 = meta.get("etag")

        # Step 4: take a Box lock (if_match=etag0). Taking the lock bumps the
        # etag — capture etag1 from the lock response for the version upload.
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=BOX_LOCK_TTL_SECONDS)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        lock_resp = client.put(
            f"files/{box_file_id}",
            json_body={"lock": {"access": "lock", "expires_at": expires_at}},
            if_match=etag0,
            params={"fields": "etag,lock,name"},
            operation_name="box.excel.lock",
        )
        etag1 = lock_resp.get("etag") or etag0
        file_name = lock_resp.get("name") or meta.get("name") or "workbook.xlsx"

        try:
            # Step 5: build mutation plan + download + apply. Two operations
            # share the same lock/download/upload machinery but differ in HOW
            # they mutate the workbook bytes:
            #   "insert" (default) — bill/expense/bill_credit add new DETAILS
            #     rows; col-Z dedups across re-runs.
            #   "stamp_draw_request" — invoice stamps column H on existing
            #     rows by col-Z match; never inserts. Re-stamping the same
            #     value is a no-op.
            from integrations.box.excel.business.row_builder import (
                build_details_rows,
                build_invoice_draw_stamp_pairs,
                DRAW_REQUEST_COL_INDEX,
            )
            from integrations.box.excel.business.workbook_editor import (
                apply_rows_to_details,
                stamp_columns_by_key,
            )

            if operation == OP_STAMP_DRAW_REQUEST:
                # Invoice path. Batch payloads are rejected upstream in handle().
                pairs = build_invoice_draw_stamp_pairs(entity_public_id)
                if not pairs:
                    logger.info(
                        "box.outbox.excel.no_rows",
                        extra={
                            "event_name": "box.outbox.excel.no_rows",
                            "box_file_id": box_file_id,
                            "entity_type": entity_type,
                            "entity_public_id": entity_public_id,
                            "operation": operation,
                        },
                    )
                    return
                updates = [
                    (source_pid, {DRAW_REQUEST_COL_INDEX: draw_value})
                    for source_pid, draw_value in pairs
                ]
                data = client.download_file(
                    box_file_id, operation_name="box.excel.download"
                )
                result = stamp_columns_by_key(data, worksheet_name, updates)
            else:
                if entities:
                    # Batch: concatenate fresh rows for every entity. Each
                    # entity is scoped to `project_id` from the payload so a
                    # multi-project parent never writes lines that belong to
                    # other projects into this workbook (review finding F1).
                    rows = []
                    for ent in entities:
                        rows.extend(build_details_rows(
                            ent["entity_type"],
                            ent["entity_public_id"],
                            project_id=project_id,
                        ))
                else:
                    rows = build_details_rows(
                        entity_type, entity_public_id, project_id=project_id,
                    )
                if not rows:
                    logger.info(
                        "box.outbox.excel.no_rows",
                        extra={
                            "event_name": "box.outbox.excel.no_rows",
                            "box_file_id": box_file_id,
                            "entity_type": entity_type,
                            "entity_public_id": entity_public_id,
                            "operation": operation,
                        },
                    )
                    return

                data = client.download_file(
                    box_file_id, operation_name="box.excel.download"
                )
                result = apply_rows_to_details(data, worksheet_name, rows)

            # Step 6: no-op when nothing was written. Two distinct reasons,
            # both yield bytes=None from the editor — an invoice stamp that
            # found ZERO matching col-Z keys logs `stamp_lost_no_match`
            # (warning), everything else is `noop_all_present` (info). The
            # outbox row marks done either way; re-stamping is cheap and
            # future invoice completions will retry.
            if result.get("bytes") is None:
                stamp_lost = (
                    operation == OP_STAMP_DRAW_REQUEST
                    and result.get("matched", 0) == 0
                    and result.get("skipped", 0) > 0
                )
                event = "box.outbox.excel.stamp_lost_no_match" if stamp_lost else "box.outbox.excel.noop_all_present"
                log_fn = logger.warning if stamp_lost else logger.info
                log_fn(
                    event,
                    extra={
                        "event_name": event,
                        "box_file_id": box_file_id,
                        "entity_type": entity_type,
                        "entity_public_id": entity_public_id,
                        "operation": operation,
                        "matched": result.get("matched"),
                        "skipped": result.get("skipped"),
                    },
                )
                return

            new_bytes = result["bytes"]
            # Upload a new version — use etag1 from the LOCK response (the lock
            # changed the etag), NOT etag0. 412 → BoxPreconditionError →
            # retryable refetch + reapply at the worker.
            up = client.upload_file_version(
                box_file_id,
                file_name,
                new_bytes,
                content_type=WORKBOOK_CONTENT_TYPE,
                if_match=etag1,
                operation_name="box.excel.upload_version",
            )

            # For stamp_draw_request, pass NULL EntityType/EntityPublicId so
            # the UpsertBoxFile MERGE preserves the workbook's prior entity
            # ownership (typically a bill/expense/credit insert that registered
            # the workbook first). A stamp is a passive update to column H —
            # it shouldn't claim the workbook in the registry. PushLog still
            # records the new sha1/version (review finding F11).
            push_entity_type = None if operation == OP_STAMP_DRAW_REQUEST else entity_type
            push_entity_public_id = None if operation == OP_STAMP_DRAW_REQUEST else entity_public_id
            self._record_push(
                row=row,
                upload_result=up,
                box_file_id=box_file_id,
                file_name=file_name,
                new_bytes=new_bytes,
                entity_type=push_entity_type,
                entity_public_id=push_entity_public_id,
                project_id=project_id,
            )

            logger.info(
                "box.outbox.excel.applied",
                extra={
                    "event_name": "box.outbox.excel.applied",
                    "box_file_id": box_file_id,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "operation": operation,
                    "applied": result.get("applied"),
                    "skipped": result.get("skipped"),
                    "matched": result.get("matched"),
                    "outcome": "success",
                },
            )
        finally:
            # Step 7: best-effort unlock. A human may have force-unlocked us;
            # never let unlock failure fail/raise the operation.
            self._best_effort_unlock(client, box_file_id)

    @staticmethod
    def _is_live_human_lock(lock: Optional[Dict[str, Any]]) -> bool:
        """
        True iff `lock` is an Office-for-web (WOPI) co-edit lock that has not
        expired. app_type startswith 'office_wopi' AND (no expires_at OR
        expires_at in the future). Our own Box lock from a prior crashed attempt
        is NOT app_type office_wopi, so it won't trip this — we re-take it.
        """
        if not lock or not isinstance(lock, dict):
            return False
        app_type = str(lock.get("app_type") or "")
        if not app_type.startswith("office_wopi"):
            return False
        expires_at = lock.get("expires_at")
        if not expires_at:
            return True  # no expiry => treat as live
        parsed = _parse_box_datetime(expires_at)
        if parsed is None:
            # Unparseable expiry — be conservative and treat as live.
            return True
        return parsed > datetime.now(timezone.utc)

    @staticmethod
    def _best_effort_unlock(client, box_file_id: str) -> None:
        try:
            client.put(
                f"files/{box_file_id}",
                json_body={"lock": None},
                operation_name="box.excel.unlock",
            )
        except Exception as error:
            logger.warning(
                "box.excel.unlock_failed",
                extra={
                    "event_name": "box.excel.unlock_failed",
                    "box_file_id": box_file_id,
                    "error_class": type(error).__name__,
                },
            )

    @staticmethod
    def _record_push(
        *,
        row,
        upload_result: Dict[str, Any],
        box_file_id: str,
        file_name: str,
        new_bytes: bytes,
        entity_type: str,
        entity_public_id: str,
        project_id: Optional[int],
    ) -> None:
        """
        Upsert the [box].[File] registry row (Kind='workbook') + append a
        [box].[PushLog] row. Registry write is load-bearing (let it propagate so
        a failure retries); the push-log write is best-effort (log + swallow).
        """
        from integrations.box.file.persistence.repo import (
            BoxFileRepository,
            BoxPushLogRepository,
        )

        entry = _unwrap_upload_entry(upload_result)
        file_version = entry.get("file_version") or {}
        file_version_id = file_version.get("id")
        etag = entry.get("etag")
        # sha1 of OUR bytes (what we uploaded) — the integrity anchor for the
        # push log; Box echoes the same sha1 on the entry when content matches.
        computed_sha1 = hashlib.sha1(new_bytes).hexdigest()
        resolved_box_file_id = str(entry.get("id") or box_file_id)

        BoxFileRepository().upsert(
            box_file_id=resolved_box_file_id,
            box_folder_id="",  # workbook is collaborated-in; parent folder N/A
            name=entry.get("name") or file_name,
            kind="workbook",
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            attachment_id=None,
            project_id=project_id,
            sha1=computed_sha1,
            etag=etag,
            file_version_id=file_version_id,
            last_pushed_at=datetime.now(timezone.utc),
        )

        try:
            BoxPushLogRepository().create(
                box_file_id=resolved_box_file_id,
                file_version_id=file_version_id,
                sha1=computed_sha1,
                request_id=getattr(row, "request_id", None),
                outbox_id=getattr(row, "id", None),
                actor_user_id=getattr(row, "created_by_user_id", None),
            )
        except Exception as error:
            logger.warning(
                "box.excel.push_log.create_failed",
                extra={
                    "event_name": "box.excel.push_log.create_failed",
                    "box_file_id": resolved_box_file_id,
                    "outbox_id": getattr(row, "id", None),
                    "error_class": type(error).__name__,
                },
            )


def _unwrap_upload_entry(result: Dict[str, Any]) -> Dict[str, Any]:
    """Box version-upload returns a collection: entries[0] is the file."""
    entries = result.get("entries") if isinstance(result, dict) else None
    if not entries or not isinstance(entries[0], dict):
        # Defensive — don't crash the push registry on an odd shape.
        return result if isinstance(result, dict) else {}
    return entries[0]


def _parse_box_datetime(value: str) -> Optional[datetime]:
    """
    Parse a Box RFC3339 timestamp (e.g. '2026-06-15T09:30:00-07:00') into an
    aware UTC datetime. Returns None on any parse failure.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    # Python's fromisoformat handles offsets like +00:00 but not a trailing 'Z'.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# Module-level dispatch entry point so the worker can register a bound callable
# matching the `_dispatch_table` signature `(row, payload) -> None`.
def handle_update_box_excel(row, payload: Dict[str, Any]) -> None:
    BoxExcelUpdateService().handle(row, payload)
