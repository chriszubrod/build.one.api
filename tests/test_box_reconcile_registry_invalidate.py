"""U-256 Part C — BoxReconcileService registry spot-check invalidates missing files."""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.box.base.errors import BoxNotFoundError, BoxPermissionError
from integrations.box.file.business.model import BoxFile
from integrations.box.reconciliation.business.reconcile_service import BoxReconcileService
from tests.test_sproc_single_source import REPO_ROOT, _sproc_body

_READ_RECENT_HOME_PATHS = (
    REPO_ROOT / "integrations/box/file/sql/box.file.sql",
    REPO_ROOT / "scripts/migrations/u256_box_integrity.sql",
)


_BOX_FILE_ID = "box-file-404"
_ENTITY_PUBLIC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _box_file():
    return BoxFile(
        box_file_id=_BOX_FILE_ID,
        name="invoice.pdf",
        entity_type="bill",
        entity_public_id=_ENTITY_PUBLIC_ID,
    )


@pytest.fixture
def reconcile_deps():
    auth = MagicMock()
    auth.is_configured.return_value = True
    auth.ensure_valid_token.return_value = None

    folder_service = MagicMock()
    folder_service.list_mappings.return_value = []

    file_repo = MagicMock()
    file_repo.read_recent.return_value = [_box_file()]

    issue_service = MagicMock()
    issue_service.open_drift_keys.return_value = set()

    svc = BoxReconcileService(
        auth_service=auth,
        folder_service=folder_service,
        file_repo=file_repo,
        issue_service=issue_service,
        settings=MagicMock(box_enterprise_id="box-tenant"),
    )
    vendor_repo_patch = patch(
        "integrations.box.reconciliation.business.reconcile_service.BoxVendorFolderRepository"
    )
    return svc, file_repo, issue_service, vendor_repo_patch


def _run_reconcile(svc, client, vendor_repo_patch):
    with vendor_repo_patch as mock_vendor_repo_cls:
        mock_vendor_repo_cls.return_value.read_all.return_value = []
        with patch(
            "integrations.box.reconciliation.business.reconcile_service.BoxHttpClient"
        ) as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value = client
            return svc.run(registry_limit=1)


def _client_get_side_effect(error):
    def _get(path, **kwargs):
        if path == "users/me":
            return {"id": "me"}
        raise error

    return _get


def test_reconcile_404_flags_and_invalidates_registry_row(reconcile_deps):
    svc, file_repo, issue_service, vendor_repo_patch = reconcile_deps

    client = MagicMock()
    client.get.side_effect = _client_get_side_effect(BoxNotFoundError("gone"))

    summary = _run_reconcile(svc, client, vendor_repo_patch)

    assert summary["files_missing"] == 1
    assert summary["issues_flagged"] == 1
    issue_service.flag_drift.assert_called_once()
    assert issue_service.flag_drift.call_args.kwargs["drift_type"] == "registry_file_missing"
    file_repo.invalidate.assert_called_once_with(_BOX_FILE_ID)


def test_reconcile_invalidate_failure_does_not_abort_run(reconcile_deps):
    svc, file_repo, issue_service, vendor_repo_patch = reconcile_deps
    file_repo.invalidate.side_effect = RuntimeError("db down")

    client = MagicMock()
    client.get.side_effect = _client_get_side_effect(BoxNotFoundError("gone"))

    summary = _run_reconcile(svc, client, vendor_repo_patch)

    assert summary["files_missing"] == 1
    assert summary["issues_flagged"] == 1
    issue_service.flag_drift.assert_called_once()
    file_repo.invalidate.assert_called_once_with(_BOX_FILE_ID)


@pytest.mark.parametrize("sql_path", _READ_RECENT_HOME_PATHS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_read_recent_box_files_excludes_invalidated_rows(sql_path: Path):
    """Reconcile canary must not re-spend TOP-N budget on IsDeleted=1 rows."""
    body = _sproc_body(sql_path, "ReadRecentBoxFiles")
    assert re.search(
        r"FROM\s+\[box\]\.\[File\][\s\S]*?\[IsDeleted\]\s*=\s*0[\s\S]*?ORDER\s+BY",
        body,
        re.IGNORECASE,
    ), f"ReadRecentBoxFiles in {sql_path.name} must filter [IsDeleted] = 0 before ORDER BY"


def test_reconcile_403_does_not_invalidate_registry_row(reconcile_deps):
    svc, file_repo, issue_service, vendor_repo_patch = reconcile_deps

    client = MagicMock()
    client.get.side_effect = _client_get_side_effect(
        BoxPermissionError("denied", code="access_denied_insufficient_permissions")
    )

    summary = _run_reconcile(svc, client, vendor_repo_patch)

    assert summary["files_missing"] == 1
    assert summary["issues_flagged"] == 1
    issue_service.flag_drift.assert_called_once()
    assert issue_service.flag_drift.call_args.kwargs["drift_type"] == "registry_file_denied"
    file_repo.invalidate.assert_not_called()
