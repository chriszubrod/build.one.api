"""
Shared mock-service builders for the QBO entity-sync lock test suite
(test_u337, test_u340, test_u347's DI smoke test) — extracted (U-347
/simplify pass) so `_outcome`/`_patch_module_service`/
`_patch_vendorcredit_service` have exactly one definition instead of being
byte-for-byte copy-pasted per test file.
"""

from unittest.mock import MagicMock

from integrations.intuit.qbo.base.sync_outcome import SyncOutcome


def _outcome():
    fake_item = MagicMock()
    fake_item.to_dict.return_value = {"id": 1}
    outcome = SyncOutcome.for_service_pull()
    outcome.record_synced(fake_item)
    return outcome


def _patch_module_service(monkeypatch, module):
    """bill/purchase/vendor/customer/company_info/item hold a module-level `service`."""
    mock_service = MagicMock()
    mock_service.sync_from_qbo.return_value = _outcome()
    monkeypatch.setattr(module, "service", mock_service)
    return mock_service


def _patch_vendorcredit_service(monkeypatch, module):
    """vendorcredit instantiates `QboVendorCreditService()` inside the handler —
    patch the class so every instantiation returns the same mock."""
    mock_service = MagicMock()
    mock_service.sync_from_qbo.return_value = _outcome()
    monkeypatch.setattr(module, "QboVendorCreditService", lambda: mock_service)
    return mock_service
