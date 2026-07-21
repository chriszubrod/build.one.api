# Python Standard Library Imports
import logging
import uuid
from typing import Any, Dict, Optional, Set, Tuple

# Local Imports
import config
from integrations.box.auth.business.service import BoxAuthService
from integrations.box.base.client import BoxHttpClient
from integrations.box.base.errors import (
    BoxError,
    BoxNotFoundError,
    BoxPermissionError,
)
from integrations.box.file.persistence.repo import BoxFileRepository
from integrations.box.folder.business.service import BoxProjectFolderService
from integrations.box.folder.persistence.repo import BoxVendorFolderRepository
from integrations.box.reconciliation.business.service import BoxReconciliationIssueService

logger = logging.getLogger(__name__)

# Bound the registry spot-check so the canary stays far under Box's
# rate limits (240 uploads/min, 1000 calls/min per user) regardless of how
# large the registry grows. The folder set is naturally small (one row per
# mapped project), so every mapping is checked.
REGISTRY_SPOT_CHECK_LIMIT = 25


class BoxReconcileService:
    """
    Daily read-only canary for the Box integration. Three checks, all GETs
    plus internal `[box].[ReconciliationIssue]` writes — it needs neither
    `ALLOW_BOX_WRITES` nor any external mutation, so it can (and should) run
    even before write cutover to validate setup:

      1. Auth canary — mint a CCG token and `GET /users/me`. A non-retryable
         failure (bad/rotated secret, app deauthorized) is flagged critical;
         a transient failure skips the run (next tick retries).
      2. Folder visibility — for every `[box].[ProjectFolder]` and
         `[box].[VendorFolder]` mapping, `GET /folders/{id}`. A 404/403
         means the service account lost collaboration on (or someone deleted)
         a mapped folder — the #1 day-2 failure. Flagged critical.
      3. Registry spot-check — for the most-recently-pushed files,
         `GET /files/{id}`. A 404 means a human deleted/purged a document we
         filed. Flagged high.

    Findings are de-duplicated against still-`open` issues so a persistent
    drift is flagged once, not every day until an operator resolves it.
    """

    def __init__(
        self,
        *,
        auth_service: Optional[BoxAuthService] = None,
        folder_service: Optional[BoxProjectFolderService] = None,
        file_repo: Optional[BoxFileRepository] = None,
        issue_service: Optional[BoxReconciliationIssueService] = None,
        settings: Optional[Any] = None,
    ):
        self.auth_service = auth_service or BoxAuthService()
        self.folder_service = folder_service or BoxProjectFolderService()
        self.file_repo = file_repo or BoxFileRepository()
        self.issue_service = issue_service or BoxReconciliationIssueService()
        self.settings = settings or config.Settings()
        self._tenant_id = self.settings.box_enterprise_id or "box"

    def run(self, registry_limit: int = REGISTRY_SPOT_CHECK_LIMIT) -> Dict[str, Any]:
        if not self.auth_service.is_configured():
            return {"skipped": True, "reason": "box_not_configured"}

        run_id = str(uuid.uuid4())
        summary: Dict[str, Any] = {
            "run_id": run_id,
            "auth_ok": False,
            "folders_checked": 0,
            "folders_missing": 0,
            "folders_transient": 0,
            "files_checked": 0,
            "files_missing": 0,
            "files_transient": 0,
            "issues_flagged": 0,
        }

        open_keys = self.issue_service.open_drift_keys()

        def _flag(
            *,
            drift_type: str,
            severity: str,
            entity_type: str,
            details: str,
            drive_item_id: Optional[str] = None,
            entity_public_id: Optional[str] = None,
        ) -> None:
            # De-dup: skip if an identical (drift_type, drive_item_id) issue
            # is already open. Track within this run too so a single canary
            # pass never double-flags.
            key: Tuple[Optional[str], Optional[str]] = (drift_type, drive_item_id)
            if key in open_keys:
                return
            self.issue_service.flag_drift(
                drift_type=drift_type,
                severity=severity,
                entity_type=entity_type,
                tenant_id=self._tenant_id,
                details=details,
                drive_item_id=drive_item_id,
                entity_public_id=entity_public_id,
                reconcile_run_id=run_id,
            )
            open_keys.add(key)
            summary["issues_flagged"] += 1

        with BoxHttpClient() as client:
            # 1. Auth canary
            try:
                self.auth_service.ensure_valid_token()
                client.get("users/me", operation_name="reconcile.auth_canary")
                summary["auth_ok"] = True
            except BoxError as error:
                if error.is_retryable:
                    logger.warning(
                        "box.reconcile.auth_canary.transient",
                        extra={
                            "event_name": "box.reconcile.auth_canary.transient",
                            "error_class": type(error).__name__,
                            "http_status": error.http_status,
                        },
                    )
                    summary["skipped"] = True
                    summary["reason"] = "auth_unavailable_transient"
                    return summary
                _flag(
                    drift_type="auth_canary_failed",
                    severity="critical",
                    entity_type="box_auth",
                    details=f"CCG auth canary failed: {type(error).__name__}: {error}",
                )
                summary["reason"] = "auth_canary_failed"
                return summary

            # 2. Folder visibility
            for mapping in self._safe_list_mappings():
                box_folder_id = mapping.get("box_folder_id")
                if not box_folder_id:
                    continue
                summary["folders_checked"] += 1
                try:
                    client.get(
                        f"folders/{box_folder_id}",
                        params={"fields": "id,name"},
                        operation_name="reconcile.folder_visibility",
                    )
                except (BoxNotFoundError, BoxPermissionError) as error:
                    summary["folders_missing"] += 1
                    _flag(
                        drift_type="folder_not_visible",
                        severity="critical",
                        entity_type="project",
                        details=(
                            f"Mapped Box folder {box_folder_id} "
                            f"(project '{mapping.get('project_name')}', id "
                            f"{mapping.get('project_id')}) is not visible to the "
                            f"service account: {type(error).__name__}. Likely "
                            f"collaboration removed or folder deleted."
                        ),
                        drive_item_id=str(box_folder_id),
                    )
                except BoxError as error:
                    # Transient/other — don't flag on a blip; next run retries.
                    summary["folders_transient"] += 1
                    logger.warning(
                        "box.reconcile.folder_check.transient",
                        extra={
                            "event_name": "box.reconcile.folder_check.transient",
                            "box_folder_id": box_folder_id,
                            "error_class": type(error).__name__,
                        },
                    )

            for mapping in self._safe_list_vendor_mappings():
                box_folder_id = mapping.get("box_folder_id")
                if not box_folder_id:
                    continue
                summary["folders_checked"] += 1
                try:
                    client.get(
                        f"folders/{box_folder_id}",
                        params={"fields": "id,name"},
                        operation_name="reconcile.folder_visibility",
                    )
                except (BoxNotFoundError, BoxPermissionError) as error:
                    summary["folders_missing"] += 1
                    _flag(
                        drift_type="folder_not_visible",
                        severity="critical",
                        entity_type="vendor",
                        details=(
                            f"Mapped Box folder {box_folder_id} "
                            f"(vendor '{mapping.get('vendor_name')}', id "
                            f"{mapping.get('vendor_id')}) is not visible to the "
                            f"service account: {type(error).__name__}. Likely "
                            f"collaboration removed or folder deleted."
                        ),
                        drive_item_id=str(box_folder_id),
                        entity_public_id=mapping.get("vendor_public_id"),
                    )
                except BoxError as error:
                    summary["folders_transient"] += 1
                    logger.warning(
                        "box.reconcile.folder_check.transient",
                        extra={
                            "event_name": "box.reconcile.folder_check.transient",
                            "box_folder_id": box_folder_id,
                            "error_class": type(error).__name__,
                        },
                    )

            # 3. Registry spot-check
            for box_file in self._safe_recent_files(registry_limit):
                box_file_id = getattr(box_file, "box_file_id", None)
                if not box_file_id:
                    continue
                summary["files_checked"] += 1
                try:
                    client.get(
                        f"files/{box_file_id}",
                        params={"fields": "id"},
                        operation_name="reconcile.registry_spot_check",
                    )
                except BoxNotFoundError:
                    summary["files_missing"] += 1
                    _flag(
                        drift_type="registry_file_missing",
                        severity="high",
                        entity_type=getattr(box_file, "entity_type", None) or "box_file",
                        entity_public_id=getattr(box_file, "entity_public_id", None),
                        details=(
                            f"Registry file {box_file_id} "
                            f"('{getattr(box_file, 'name', None)}') no longer exists "
                            f"in Box — deleted or purged on the Box side."
                        ),
                        drive_item_id=str(box_file_id),
                    )
                except BoxPermissionError:
                    summary["files_missing"] += 1
                    _flag(
                        drift_type="registry_file_denied",
                        severity="high",
                        entity_type=getattr(box_file, "entity_type", None) or "box_file",
                        entity_public_id=getattr(box_file, "entity_public_id", None),
                        details=(
                            f"Registry file {box_file_id} is no longer accessible to "
                            f"the service account (403) — collaboration scope changed."
                        ),
                        drive_item_id=str(box_file_id),
                    )
                except BoxError as error:
                    summary["files_transient"] += 1
                    logger.warning(
                        "box.reconcile.file_check.transient",
                        extra={
                            "event_name": "box.reconcile.file_check.transient",
                            "box_file_id": box_file_id,
                            "error_class": type(error).__name__,
                        },
                    )

        logger.info(
            "box.reconcile.completed",
            extra={"event_name": "box.reconcile.completed", **summary},
        )
        return summary

    # ------------------------------------------------------------------ #
    # Internals — failure-isolated reads so a DB hiccup on one source
    # never aborts the whole canary.
    # ------------------------------------------------------------------ #

    def _safe_list_mappings(self) -> list:
        try:
            return self.folder_service.list_mappings()
        except Exception as error:
            logger.warning(
                "box.reconcile.list_mappings.failed",
                extra={"event_name": "box.reconcile.list_mappings.failed", "error": str(error)},
            )
            return []

    def _safe_list_vendor_mappings(self) -> list:
        try:
            return BoxVendorFolderRepository().read_all()
        except Exception as error:
            logger.warning(
                "box.reconcile.list_vendor_mappings.failed",
                extra={
                    "event_name": "box.reconcile.list_vendor_mappings.failed",
                    "error": str(error),
                },
            )
            return []

    def _safe_recent_files(self, limit: int) -> list:
        try:
            return self.file_repo.read_recent(limit=limit)
        except Exception as error:
            logger.warning(
                "box.reconcile.recent_files.failed",
                extra={"event_name": "box.reconcile.recent_files.failed", "error": str(error)},
            )
            return []
