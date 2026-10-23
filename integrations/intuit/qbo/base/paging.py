# Python Standard Library Imports
import logging
from typing import Any, Callable, List

# Local Imports
from integrations.intuit.qbo.base.errors import QboUnexpectedError
from integrations.intuit.qbo.base.ids import normalize_qbo_id

logger = logging.getLogger(__name__)


# QBO caps a query page at 1000 rows. The page ceiling is a runaway-loop
# backstop only — at 1000 rows/page it allows a million records per entity.
DEFAULT_ID_PAGE_SIZE = 1000
DEFAULT_ID_MAX_PAGES = 1000

_BY_IDS_DEFAULT_BATCH_SIZE = 100


def _empty_page_refusal(refuse_empty_page: bool) -> str:
    return " - refusing to treat as an empty page" if refuse_empty_page else ""


def _strict_query_rows(
    data: Any,
    *,
    entity: str,
    where: str,
    label: str,
    refuse_empty_page: bool = False,
) -> List[Any]:
    """
    Strict QueryResponse shape checks shared by id paging and by-ids fetches.

    Returns the entity's row list (empty when the key is absent). Coerces a
    lone dict row to a one-element list. Raises on malformed bodies.
    """
    refusal = _empty_page_refusal(refuse_empty_page)
    prefix = f"QBO {label}"

    if not isinstance(data, dict):
        raise QboUnexpectedError(
            f"{prefix} returned a non-dict body{refusal}",
            detail=where,
        )

    if "Fault" in data:
        raise QboUnexpectedError(
            f"{prefix} returned a Fault{refusal}",
            detail=where,
        )

    if "QueryResponse" not in data:
        raise QboUnexpectedError(
            f"{prefix} returned no QueryResponse{refusal}",
            detail=where,
        )

    query_response = data.get("QueryResponse")
    if not isinstance(query_response, dict):
        raise QboUnexpectedError(
            f"{prefix} returned a null or non-dict QueryResponse",
            detail=where,
        )

    if "Fault" in query_response:
        raise QboUnexpectedError(
            f"{prefix} returned a Fault inside QueryResponse{refusal}",
            detail=where,
        )

    if entity not in query_response:
        rows: List[Any] = []
    else:
        rows = query_response.get(entity)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise QboUnexpectedError(
            f"{prefix} returned a non-list {entity} payload",
            detail=where,
        )
    return rows


def query_records_by_ids(
    http_client: Any,
    *,
    entity: str,
    ids: List[str],
    operation_name: str,
    batch_size: int = _BY_IDS_DEFAULT_BATCH_SIZE,
    parse: Callable[[Any], Any],
) -> List[Any]:
    """Fetch full QBO entity rows by Id (batched IN queries, strict parsing)."""
    query_label = f"{entity} query"
    if not ids:
        return []
    parsed: List[Any] = []
    for offset in range(0, len(ids), batch_size):
        batch = ids[offset : offset + batch_size]
        numeric_ids: List[str] = []
        for raw_id in batch:
            norm = normalize_qbo_id(raw_id)
            if not norm or not norm.isdigit():
                logger.warning(
                    "Skipping non-numeric QBO %s id for by-ids query: %r",
                    entity,
                    raw_id,
                )
                continue
            numeric_ids.append(norm)
        if not numeric_ids:
            continue
        in_clause = ",".join(f"'{i}'" for i in numeric_ids)
        query_string = (
            f"SELECT * FROM {entity} WHERE Id IN ({in_clause}) MAXRESULTS 1000"
        )
        where = f"entity={entity} ids={len(numeric_ids)}"
        data = http_client.get(
            "query",
            params={"query": query_string},
            operation_name=operation_name,
        )
        rows = _strict_query_rows(
            data,
            entity=entity,
            where=where,
            label=query_label,
        )
        parsed.extend(parse(row) for row in rows)
    return parsed


def page_all_record_ids(
    http_client: Any,
    *,
    entity: str,
    operation_name: str,
    page_size: int = DEFAULT_ID_PAGE_SIZE,
    max_pages: int = DEFAULT_ID_MAX_PAGES,
) -> List[str]:
    """
    Page the complete set of live QBO record ids for one entity in one realm.

    STRICT — raises instead of ever returning a short list. This is the single
    home for that guarantee, and it exists because of how the *other* pagers
    behave: `query_all_<entity>s` returns [] when a response body lacks
    QueryResponse (the shared client returns {} for an unparseable 2xx) and its
    loop reads [] as end-of-pages, so a dropped or faulted page yields a partial
    list indistinguishable from a complete one.

    That is fatal for the caller this exists for. The reconciliation void
    detectors compute `mapped_ids - live_ids`; a truncated live set puts LIVE
    records in that difference and flags them as deleted. So every anomaly here
    raises, the detector aborts, and nothing is flagged on a doubtful id set.

    Keep this shared: the guards below were once triplicated per entity, and a
    fix applied to two of three copies silently restores the truncation bug.

    Raises:
        QboUnexpectedError: on a non-dict body, a Fault (top level or inside
            QueryResponse), a missing or non-dict QueryResponse, a non-list row
            payload, a row with no usable Id, or exhaustion of the page ceiling.
    """
    ids: List[str] = []
    start_position = 1

    for _page in range(max_pages):
        # Id-only projection: the loop reads nothing but Id, and full rows
        # (headers + line arrays) are ~50-100x the payload per 1000-row page.
        # NOTE (U-219): this id projection has NO `Active` predicate, so it sees only
        # ACTIVE records. Harmless today — the only callers are bill/purchase/vendorcredit,
        # transactions with no Active concept. But the reference entities' full-row pulls
        # now query `Active IN (true, false)`. Any future strict id pager for a REFERENCE
        # entity (vendor/customer/item/account/term) MUST add that predicate, or
        # delete_reconcile's `mapped_ids - live_ids` will flag every deactivated record
        # as hard-deleted — the exact inverse of what U-219 established.
        query_string = (
            f"SELECT Id FROM {entity} STARTPOSITION {start_position} "
            f"MAXRESULTS {page_size}"
        )
        data = http_client.get(
            "query",
            params={"query": query_string},
            operation_name=operation_name,
        )
        where = f"entity={entity} start_position={start_position}"

        rows = _strict_query_rows(
            data,
            entity=entity,
            where=where,
            label="id query",
            refuse_empty_page=True,
        )

        # An empty page legitimately ends pagination: QBO returns one for a truly
        # empty result and for the page after an exact multiple of the page size.
        # Only a *degraded* body is rejected, and every such shape raised above.
        if not rows:
            break

        for row in rows:
            record_id = normalize_qbo_id(row.get("Id") if isinstance(row, dict) else None)
            if not record_id:
                raise QboUnexpectedError(
                    "QBO id query returned a record with no usable Id",
                    detail=where,
                )
            ids.append(record_id)

        if len(rows) < page_size:
            break

        start_position += page_size
    else:
        raise QboUnexpectedError(
            "QBO id query exceeded the page ceiling",
            detail=f"entity={entity} max_pages={max_pages}",
        )

    logger.info(f"Retrieved {len(ids)} {entity} ids from QBO")
    return ids
