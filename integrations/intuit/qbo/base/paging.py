# Python Standard Library Imports
import logging
from typing import Any, List

# Local Imports
from integrations.intuit.qbo.base.errors import QboUnexpectedError
from integrations.intuit.qbo.base.ids import normalize_qbo_id

logger = logging.getLogger(__name__)


# QBO caps a query page at 1000 rows. The page ceiling is a runaway-loop
# backstop only — at 1000 rows/page it allows a million records per entity.
DEFAULT_ID_PAGE_SIZE = 1000
DEFAULT_ID_MAX_PAGES = 1000


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

        if not isinstance(data, dict):
            raise QboUnexpectedError(
                "QBO id query returned a non-dict body - refusing to treat as an empty page",
                detail=where,
            )

        # ONE rule governs every check below, and it is deliberately not truthiness:
        #   • an anomaly signal is detected by key PRESENCE  (a Fault key at all)
        #   • the sole legitimate empty-page signal is key ABSENCE, or an explicit []
        #   • everything else raises
        # Truthiness would reopen the exact hole this helper exists to close: {} , ""
        # , 0 and None are all falsy, so `if data.get("Fault")` misses an empty Fault
        # and `... or []` turns a malformed payload into an innocent-looking empty
        # page that silently truncates the id set.
        #
        # A body carrying a Fault is not a successful page whatever its status code.
        # Checked at both levels because QBO reports query faults in both positions.
        if "Fault" in data:
            raise QboUnexpectedError(
                "QBO id query returned a Fault - refusing to treat as an empty page",
                detail=where,
            )

        if "QueryResponse" not in data:
            raise QboUnexpectedError(
                "QBO id query returned no QueryResponse - refusing to treat as an empty page",
                detail=where,
            )

        # The key is present (absence raised above), so an explicit null here is a
        # malformed body, not an empty page — QBO signals empty with {}.
        query_response = data.get("QueryResponse")
        if not isinstance(query_response, dict):
            raise QboUnexpectedError(
                "QBO id query returned a null or non-dict QueryResponse",
                detail=where,
            )

        if "Fault" in query_response:
            raise QboUnexpectedError(
                "QBO id query returned a Fault inside QueryResponse - refusing to treat as an empty page",
                detail=where,
            )

        # Same rule one level down. An ABSENT entity key means "no rows on this
        # page" and legitimately ends pagination; an explicit [] does too. An
        # explicit null, or any other non-list, is malformed and raises.
        if entity not in query_response:
            rows = []
        else:
            rows = query_response.get(entity)
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise QboUnexpectedError(
                f"QBO id query returned a non-list {entity} payload",
                detail=where,
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
