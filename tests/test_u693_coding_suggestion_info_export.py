"""U-693: INFO lines from entities.coding_suggestion reach the App Insights handler on the root."""
import logging

import pytest

from entities.coding_suggestion.business import bill_text_suggestion_service as svc
from shared.observability import (
    EXPORTED_INFO_LOGGERS,
    export_info_loggers,
    install_log_redaction,
)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def exported_root():
    names = ("", *EXPORTED_INFO_LOGGERS)
    saved = {n: logging.getLogger(n).level for n in names}
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    for n in EXPORTED_INFO_LOGGERS:
        logging.getLogger(n).setLevel(logging.WARNING)
    handler = _Capture()
    root.addHandler(handler)
    export_info_loggers()
    yield handler
    root.removeHandler(handler)
    for n, lvl in saved.items():
        logging.getLogger(n).setLevel(lvl)


def test_suggestion_module_logger_is_info_and_root_stays_warning(exported_root):
    assert svc.logger.name.startswith("entities.coding_suggestion.")
    assert svc.logger.getEffectiveLevel() == logging.INFO
    assert logging.getLogger().level == logging.WARNING


def test_candidate_count_line_reaches_the_root_handler(exported_root):
    built = svc.RuntimePayloadBuild(
        payload={"candidate_sccs": [{"id": 1}, {"id": 2}]}, mode="narrowed", candidate_scc_narrowed=True
    )
    svc._log_bill_text_coding_suggestion_candidates(bill_id=7, vendor_id=9, built=built)
    lines = [
        r.getMessage() for r in exported_root.records
        if r.levelno == logging.INFO and r.name == svc.logger.name
    ]
    assert len(lines) == 1
    assert "candidate_scc_count=2" in lines[0] and "candidate_scc_narrowed=True" in lines[0]


def test_driver_text_on_an_exported_info_line_is_redacted(exported_root):
    install_log_redaction()
    svc.logger.info("coding lookup failed: %s", "[Microsoft][ODBC Driver 17 for SQL Server] value 12-3456789")
    msgs = [r.getMessage() for r in exported_root.records if r.name == svc.logger.name]
    assert msgs and "12-3456789" not in msgs[-1] and "ODBC Driver" not in msgs[-1]
