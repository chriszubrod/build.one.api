"""U-650 — one registry decides which loggers reach App Insights at INFO.

The Azure exporter sits on the root logger, which stays at WARNING. Only the
loggers named in shared.observability.EXPORTED_INFO_LOGGERS are raised to INFO.
"""

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from intelligence.observability import cascade_telemetry
from shared.observability import (
    APP_INSIGHTS_ENV,
    EXPORTED_INFO_LOGGERS,
    configure_app_insights,
    export_info_loggers,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

BUSINESS_LOGGER_NAMES = (
    "intelligence.outbox",
    "integrations.intuit.qbo.outbox",
    "integrations.intuit.qbo.reconciliation",
    "integrations.ms.outbox",
    "integrations.box.outbox",
    "shared.scheduler",
)

NOISY_LOGGER_NAMES = (
    "integrations.box.base.client",
    "integrations.ms.base.client",
    "integrations.intuit.qbo.base.client",
    "integrations.box.excel",
    "integrations.box.folder",
    "integrations.box.file",
)

CASCADE_MODULE_LOGGERS = (
    "intelligence.cascade.core",
    "intelligence.cascade.agent_cascade",
    "intelligence.transport.cascade",
)


def _real_loggers():
    for name, logger in logging.root.manager.loggerDict.items():
        if isinstance(logger, logging.Logger):
            yield name, logger


@pytest.fixture
def restore_levels():
    """Restore the root + registry levels AND every logger's propagate flag.

    Every bootstrap/export test uses this, so a regression that flips
    `propagate` (or a level) in one test cannot leak into the next one's
    snapshot. Loggers created during the test get propagate=True back.
    """
    names = ("", *EXPORTED_INFO_LOGGERS)
    saved_levels = {name: logging.getLogger(name).level for name in names}
    saved_propagate = {name: logger.propagate for name, logger in _real_loggers()}
    yield
    for name, level in saved_levels.items():
        logging.getLogger(name).setLevel(level)
    for name, logger in _real_loggers():
        logger.propagate = saved_propagate.get(name, True)


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def root_capture(restore_levels):
    handler = _CaptureHandler()
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


def _set_registry_to_warning():
    for name in EXPORTED_INFO_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _level_snapshot():
    levels = {"<root>": logging.getLogger().level}
    levels.update({name: logger.level for name, logger in _real_loggers()})
    return levels


def _callee_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _app_tree():
    return ast.parse((REPO_ROOT / "app.py").read_text())


def test_app_insights_env_is_azures_connection_string_variable():
    assert APP_INSIGHTS_ENV == "APPLICATIONINSIGHTS_CONNECTION_STRING"


@pytest.mark.parametrize("environ", [{}, {APP_INSIGHTS_ENV: ""}], ids=["absent", "empty"])
def test_configure_app_insights_is_a_no_op_without_a_connection_string(restore_levels, environ):
    _set_registry_to_warning()
    calls = []

    enabled = configure_app_insights(environ=environ, configure=lambda **kw: calls.append(kw))

    assert enabled is False
    assert calls == []
    for name in EXPORTED_INFO_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_configure_configures_azure_then_exports_only_the_registry(restore_levels):
    _set_registry_to_warning()
    logging.getLogger().setLevel(logging.WARNING)
    before = _level_snapshot()
    calls = []
    levels_at_configure = {}

    def configure(**kwargs):
        calls.append(kwargs)
        levels_at_configure.update({name: logging.getLogger(name).level for name in EXPORTED_INFO_LOGGERS})

    enabled = configure_app_insights(environ={APP_INSIGHTS_ENV: "InstrumentationKey=test"}, configure=configure)

    after = _level_snapshot()
    assert enabled is True
    assert calls == [{"logger_name": "", "enable_live_metrics": False}]
    assert levels_at_configure == {name: logging.WARNING for name in EXPORTED_INFO_LOGGERS}
    assert {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)} == set(
        EXPORTED_INFO_LOGGERS
    )
    assert after["<root>"] == before["<root>"] == logging.WARNING
    for name in EXPORTED_INFO_LOGGERS:
        assert logging.getLogger(name).level == logging.INFO, name


def test_registry_is_exactly_the_cascade_and_business_loggers():
    assert sorted(EXPORTED_INFO_LOGGERS) == sorted(
        (
            "intelligence.cascade",
            "intelligence.transport.cascade",
            *BUSINESS_LOGGER_NAMES,
            "shared.api.request_log",  # U-663 time-tracking write audit line
            "entities.coding_suggestion",  # U-693 coding-suggestion outcome lines
        )
    )


@pytest.mark.parametrize("module_logger", CASCADE_MODULE_LOGGERS)
def test_cascade_module_loggers_fall_under_a_registry_entry(module_logger):
    assert any(module_logger == name or module_logger.startswith(name + ".") for name in EXPORTED_INFO_LOGGERS)


@pytest.mark.parametrize("noisy", NOISY_LOGGER_NAMES)
def test_registry_excludes_the_per_request_and_per_row_loggers(noisy):
    for name in EXPORTED_INFO_LOGGERS:
        assert not (noisy == name or noisy.startswith(name + ".")), (name, noisy)


def test_exported_child_loggers_inherit_info_and_excluded_clients_do_not(restore_levels):
    _set_registry_to_warning()

    export_info_loggers()

    assert logging.getLogger("integrations.ms.outbox.business.worker").getEffectiveLevel() == logging.INFO
    assert logging.getLogger("integrations.intuit.qbo.reconciliation.business.service").getEffectiveLevel() == logging.INFO
    assert logging.getLogger("integrations.ms.base.client").getEffectiveLevel() != logging.INFO
    # The cascade module loggers sit at exactly INFO (not DEBUG, which delivery and
    # prefix checks would both accept).
    for name in ("intelligence.cascade.core", "intelligence.cascade.agent_cascade", "intelligence.transport.cascade"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.INFO, name


def test_app_calls_configure_app_insights_once_at_module_level_before_fastapi():
    tree = _app_tree()

    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _callee_name(n) == "configure_app_insights"]
    module_statements = [
        s for s in tree.body
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and _callee_name(s.value) == "configure_app_insights"
    ]
    fastapi_import = next(
        i for i, s in enumerate(tree.body) if isinstance(s, ast.ImportFrom) and s.module == "fastapi"
    )

    assert len(calls) == 1
    assert len(module_statements) == 1
    assert tree.body.index(module_statements[0]) < fastapi_import


def test_app_imports_configure_app_insights_from_shared_observability():
    tree = _app_tree()

    imports = [
        s for s in ast.walk(tree)
        if isinstance(s, ast.ImportFrom) and s.module == "shared.observability"
        and any(alias.name == "configure_app_insights" for alias in s.names)
    ]

    assert len(imports) == 1


def test_app_does_not_wire_the_exporter_directly():
    tree = _app_tree()

    direct = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _callee_name(n) in {"configure_azure_monitor", "export_info_loggers"}
    ]

    assert direct == []


def test_setup_has_one_home_in_shared_observability():
    assert not hasattr(cascade_telemetry, "configure_app_insights")
    assert not hasattr(cascade_telemetry, "APP_INSIGHTS_ENV")


@pytest.mark.parametrize("present", [True, False], ids=["present", "absent"])
def test_configure_reads_the_real_environment_connection_string(restore_levels, monkeypatch, present):
    _set_registry_to_warning()
    if present:
        monkeypatch.setenv(APP_INSIGHTS_ENV, "InstrumentationKey=test")
    else:
        monkeypatch.delenv(APP_INSIGHTS_ENV, raising=False)
    calls = []

    enabled = configure_app_insights(configure=lambda **kw: calls.append(kw))

    assert enabled is present
    assert calls == ([{"logger_name": "", "enable_live_metrics": False}] if present else [])


_FORBIDDEN_EAGER_IMPORTS = ("httpx", "pyodbc", "fastapi", "azure", "opentelemetry", "config")

# Run in isolated mode (-I: no PYTHON* env vars, no user site, no implicit cwd on
# sys.path) with the repo inserted explicitly, and diff sys.modules around the
# import so anything preloaded at interpreter startup is not blamed on it.
_IMPORT_PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
before = {name.split(".")[0] for name in sys.modules}
import shared.observability
after = {name.split(".")[0] for name in sys.modules}
print(json.dumps(sorted(after - before)))
"""


def test_importing_observability_loads_no_heavy_dependency():
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_PROBE, str(REPO_ROOT)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=60,
    )

    loaded_by_import = set(json.loads(completed.stdout))
    assert "shared" in loaded_by_import  # the probe really imported the module under test
    assert loaded_by_import.isdisjoint(_FORBIDDEN_EAGER_IMPORTS), sorted(
        loaded_by_import & set(_FORBIDDEN_EAGER_IMPORTS)
    )


def test_exported_info_reaches_the_root_handler_and_excluded_info_does_not(root_capture):
    export_info_loggers()

    for name in ("integrations.ms.outbox.business.worker", "intelligence.cascade.core"):
        logging.getLogger(name).info("delivered from %s", name)
    logging.getLogger("integrations.ms.base.client").info("excluded")

    delivered = {record.name for record in root_capture.records}
    assert {"integrations.ms.outbox.business.worker", "intelligence.cascade.core"} <= delivered
    assert "integrations.ms.base.client" not in delivered


def test_every_registry_logger_propagates_to_root(restore_levels):
    export_info_loggers()

    assert [name for name in EXPORTED_INFO_LOGGERS if not logging.getLogger(name).propagate] == []
