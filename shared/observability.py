"""Which loggers reach App Insights at INFO, decided in one place.

The Azure exporter is attached to the root logger, and the root stays at WARNING
so library and request INFO noise is not exported. A logger's INFO records reach
App Insights only when it is listed in EXPORTED_INFO_LOGGERS, which
export_info_loggers() raises to INFO and nothing else.
"""

import logging
import os
from typing import Any, Callable, Optional

APP_INSIGHTS_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"

# Clients and per-row loggers left out at INFO: docs/runbooks/observability-info-export.md
EXPORTED_INFO_LOGGERS: tuple[str, ...] = (
    # Cascade rung outcomes (U-649).
    "intelligence.cascade",
    "intelligence.transport.cascade",
    # Outbox row lifecycle: enqueue, claim, send, dead-letter.
    "intelligence.outbox",
    "integrations.intuit.qbo.outbox",
    "integrations.ms.outbox",
    "integrations.box.outbox",
    # Reconciliation runs started and completed.
    "integrations.intuit.qbo.reconciliation",
    # QBO sync pull started and completed (qbo.sync.pull.*), and scheduler lifecycle.
    "shared.scheduler",
)


def configure_app_insights(
    environ: Optional[dict[str, str]] = None,
    configure: Optional[Callable[..., Any]] = None,
) -> bool:
    env = os.environ if environ is None else environ
    if not env.get(APP_INSIGHTS_ENV):
        return False
    if configure is None:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure = configure_azure_monitor
    configure(logger_name="", enable_live_metrics=False)
    export_info_loggers()
    return True


def export_info_loggers() -> None:
    for name in EXPORTED_INFO_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
