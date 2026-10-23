# Python Standard Library Imports
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Set

# Local Imports
from integrations.intuit.qbo.base.client import _format_datetime_for_qbo_query
from integrations.intuit.qbo.base.errors import QboUnexpectedError
from integrations.intuit.qbo.base.ids import normalize_qbo_id

logger = logging.getLogger(__name__)

_CDC_MAX_LOOKBACK = timedelta(days=30)


def _raise_if_fault(payload: Any, *, detail: str) -> None:
    if isinstance(payload, dict) and "Fault" in payload:
        raise QboUnexpectedError(
            "QBO CDC returned a Fault",
            detail=detail,
        )


def fetch_cdc_deleted_ids(
    http_client: Any,
    *,
    entities: List[str],
    changed_since: datetime,
) -> Dict[str, Set[str]]:
    """
    One CDC GET; return normalized QBO ids with status Deleted per entity name.

    STRICT — raises QboUnexpectedError on malformed payloads. ``changed_since``
    must be timezone-aware UTC and no older than QBO's 30-day CDC window.
    """
    if changed_since.tzinfo is None:
        raise ValueError("changed_since must be timezone-aware UTC")
    utc = changed_since.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if utc < now - _CDC_MAX_LOOKBACK:
        raise ValueError(
            "changed_since is older than QBO CDC's 30-day limit"
        )

    formatted = _format_datetime_for_qbo_query(utc, logger=logger)
    assert formatted is not None
    entity_param = ",".join(entities)
    data = http_client.get(
        "cdc",
        params={"entities": entity_param, "changedSince": formatted},
        operation_name="qbo.cdc.fetch",
    )

    if not isinstance(data, dict):
        raise QboUnexpectedError(
            "QBO CDC returned a non-dict body",
            detail=f"entities={entity_param}",
        )
    _raise_if_fault(data, detail=f"entities={entity_param}")

    cdc_response = data.get("CDCResponse")
    if not cdc_response or not isinstance(cdc_response, list):
        raise QboUnexpectedError(
            "QBO CDC returned a missing or empty CDCResponse",
            detail=f"entities={entity_param}",
        )

    out: Dict[str, Set[str]] = {name: set() for name in entities}
    first = cdc_response[0]
    if first is None or not isinstance(first, dict):
        raise QboUnexpectedError(
            "QBO CDC CDCResponse element is not a dict",
            detail=f"entities={entity_param}",
        )
    _raise_if_fault(first, detail=f"entities={entity_param}")

    if "QueryResponse" not in first:
        return out
    query_blocks = first["QueryResponse"]
    if query_blocks is None:
        raise QboUnexpectedError(
            "QBO CDC QueryResponse is null",
            detail=f"entities={entity_param}",
        )
    if not isinstance(query_blocks, list):
        raise QboUnexpectedError(
            "QBO CDC QueryResponse is not a list",
            detail=f"entities={entity_param}",
        )

    for block in query_blocks:
        if block is None or not isinstance(block, dict):
            raise QboUnexpectedError(
                "QBO CDC QueryResponse block is not a dict",
                detail=f"entities={entity_param}",
            )
        _raise_if_fault(block, detail=f"entities={entity_param}")
        for entity_name in entities:
            if entity_name not in block:
                continue
            payload = block[entity_name]
            if payload is None:
                raise QboUnexpectedError(
                    f"QBO CDC {entity_name} payload is null",
                    detail=f"entities={entity_param}",
                )
            if not isinstance(payload, list):
                raise QboUnexpectedError(
                    f"QBO CDC {entity_name} payload is not a list",
                    detail=f"entities={entity_param}",
                )
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                if entry.get("status") != "Deleted":
                    continue
                norm = normalize_qbo_id(entry.get("Id"))
                if norm:
                    out[entity_name].add(norm)

    return out
