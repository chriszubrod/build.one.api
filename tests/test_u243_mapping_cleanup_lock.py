"""Pure-logic tests for U-243 — sp_getapplock serialization on mapping cleanup delete."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.base.mapping_cleanup import delete_own_qbo_mapping_before_header


@contextmanager
def _granted_lock(*_args, **_kwargs):
    yield True


@contextmanager
def _denied_lock(*_args, **_kwargs):
    yield False


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _denied_lock)
def test_lock_acquisition_failure_raises_value_error_without_read_or_delete():
    read_mapping = Mock()
    delete_header = Mock()
    with pytest.raises(ValueError, match="could not acquire mapping-cleanup lock") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=read_mapping,
            delete_mapping=Mock(),
            recreate_mapping=Mock(),
            delete_header=delete_header,
            entity_label="Bill",
            entity_id=42,
        )
    assert "Bill" in str(exc_info.value)
    assert "42" in str(exc_info.value)
    read_mapping.assert_not_called()
    delete_header.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock")
def test_lock_resource_key_includes_entity_label_and_id(mock_lock):
    mock_lock.side_effect = [_granted_lock(), _granted_lock()]

    delete_own_qbo_mapping_before_header(
        read_mapping=Mock(return_value=None),
        delete_mapping=Mock(),
        recreate_mapping=Mock(),
        delete_header=Mock(return_value="ok"),
        entity_label="Bill",
        entity_id=7,
    )
    delete_own_qbo_mapping_before_header(
        read_mapping=Mock(return_value=None),
        delete_mapping=Mock(),
        recreate_mapping=Mock(),
        delete_header=Mock(return_value="ok"),
        entity_label="Expense",
        entity_id=7,
    )

    assert mock_lock.call_args_list[0].args == ("qbo_mapping_delete:Bill:7",)
    assert mock_lock.call_args_list[1].args == ("qbo_mapping_delete:Expense:7",)


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_no_mapping_still_calls_delete_header():
    delete_header = Mock(return_value="header-result")
    recreate_mapping = Mock()
    delete_own_qbo_mapping_before_header(
        read_mapping=Mock(return_value=None),
        delete_mapping=Mock(),
        recreate_mapping=recreate_mapping,
        delete_header=delete_header,
        entity_label="Bill",
        entity_id=42,
    )
    delete_header.assert_called_once()
    recreate_mapping.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock")
def test_lock_is_held_across_the_full_critical_section(mock_lock):
    call_order = []
    mapping = SimpleNamespace(id=99)

    @contextmanager
    def _tracking_lock(*_args, **_kwargs):
        call_order.append("lock_acquired")
        try:
            yield True
        finally:
            call_order.append("lock_released")

    mock_lock.side_effect = _tracking_lock

    delete_own_qbo_mapping_before_header(
        read_mapping=lambda: (call_order.append("read_mapping"), mapping)[1],
        delete_mapping=lambda _m: call_order.append("delete_mapping"),
        recreate_mapping=Mock(),
        delete_header=lambda: call_order.append("delete_header") or "header-result",
        entity_label="Bill",
        entity_id=42,
    )

    assert call_order == [
        "lock_acquired",
        "read_mapping",
        "delete_mapping",
        "delete_header",
        "lock_released",
    ]


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_success_returns_delete_header_result():
    mapping = SimpleNamespace(id=99)
    delete_mapping = Mock()
    delete_header = Mock(return_value="header-result")
    recreate_mapping = Mock()
    on_restore_failed = Mock()
    result = delete_own_qbo_mapping_before_header(
        read_mapping=Mock(return_value=mapping),
        delete_mapping=delete_mapping,
        recreate_mapping=recreate_mapping,
        delete_header=delete_header,
        entity_label="Bill",
        entity_id=42,
        on_restore_failed=on_restore_failed,
    )
    delete_mapping.assert_called_once_with(mapping)
    delete_header.assert_called_once()
    recreate_mapping.assert_not_called()
    on_restore_failed.assert_not_called()
    assert result == "header-result"


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_read_failure_raises_value_error():
    read_exc = RuntimeError("db blip")
    delete_header = Mock()
    with pytest.raises(ValueError, match="failed to read qbo mapping") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(side_effect=read_exc),
            delete_mapping=Mock(),
            recreate_mapping=Mock(),
            delete_header=delete_header,
            entity_label="BillCredit",
            entity_id=42,
        )
    assert exc_info.value.__cause__ is read_exc
    delete_header.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_delete_failure_raises_value_error():
    mapping = SimpleNamespace(id=99)
    delete_exc = RuntimeError("FK 547")
    delete_header = Mock()
    with pytest.raises(ValueError, match="failed to delete qbo mapping") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(return_value=mapping),
            delete_mapping=Mock(side_effect=delete_exc),
            recreate_mapping=Mock(),
            delete_header=delete_header,
            entity_label="Expense",
            entity_id=42,
        )
    assert exc_info.value.__cause__ is delete_exc
    delete_header.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_header_failure_restores_mapping():
    mapping = SimpleNamespace(id=99)
    header_exc = RuntimeError("header delete 547")
    delete_header = Mock(side_effect=header_exc)
    recreate_mapping = Mock()
    on_restore_failed = Mock()
    with pytest.raises(RuntimeError, match="header delete 547") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(return_value=mapping),
            delete_mapping=Mock(),
            recreate_mapping=recreate_mapping,
            delete_header=delete_header,
            entity_label="Bill",
            entity_id=42,
            on_restore_failed=on_restore_failed,
        )
    assert exc_info.value is header_exc
    recreate_mapping.assert_called_once_with(mapping)
    on_restore_failed.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_header_failure_restore_also_fails():
    mapping = SimpleNamespace(id=99)
    header_exc = RuntimeError("header delete 547")
    restore_exc = RuntimeError("restore failed")
    delete_header = Mock(side_effect=header_exc)
    recreate_mapping = Mock(side_effect=restore_exc)
    on_restore_failed = Mock()
    with pytest.raises(RuntimeError, match="header delete 547") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(return_value=mapping),
            delete_mapping=Mock(),
            recreate_mapping=recreate_mapping,
            delete_header=delete_header,
            entity_label="Bill",
            entity_id=42,
            on_restore_failed=on_restore_failed,
        )
    assert exc_info.value is header_exc
    recreate_mapping.assert_called_once_with(mapping)
    on_restore_failed.assert_called_once_with(mapping, restore_exc)
