"""U-778a: backfill the ReimburseCharge link onto staged Bill / Purchase / VendorCredit lines.

Reads staged Billable/HasBeenBilled lines with no stored link, re-fetches their headers from QBO
(100 per batch), and writes each line's own link. Any client or repo exception aborts the WHOLE run
and reports where to resume; nothing is swallowed.

Dry-run contract: dry-run (the default) writes NO staging links. It is still a real QBO read: the QBO
transport meters every call (`IncrementQboApiUsage`, fail-open by design) and may refresh the OAuth
token. Those are the transport's normal, intended side effects of any QBO read, not backfill writes.

Resume: `resume_from` is the last header of the last FULLY completed batch of `result["entity"]`, so it
is only meaningful for one entity; `run()` therefore requires exactly one entity when it is given.

Counters (per entity): `not_written` = the fill-only narrow writer returned no row: the line already
had a link (e.g. a fresher pull wrote it between our read and our write) OR the staging row vanished.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from integrations.intuit.qbo.base.charge_link import reimburse_charge_link

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
ALL_ENTITIES = ("bill", "purchase", "vendor_credit")
_COUNTERS = ("written", "not_written", "would_write", "no_link_live", "ambiguous", "line_gone", "header_gone")


@dataclass(frozen=True)
class _EntityConfig:
    fetch: Callable[[List[str]], List[Any]]       # headers by QBO id (one QBO call per batch)
    read_missing: Callable[[str], List[Any]]      # staged Billable/HasBeenBilled lines with no link, by realm
    write: Callable[[int, str], bool]             # narrow link writer; True iff a row was written


class _Stop(Exception):
    """Internal: clean max_batches stop."""


class QboChargeLinkBackfillService:
    def __init__(
        self,
        realm_id: str,
        *,
        bill_client,
        purchase_client,
        vendor_credit_client,
        bill_line_repo,
        purchase_line_repo,
        vendor_credit_repo,
    ):
        self.realm_id = realm_id
        self._plan = {
            "bill": _EntityConfig(
                bill_client.query_bills_by_ids,
                bill_line_repo.read_lines_missing_reimburse_charge_by_realm_id,
                bill_line_repo.update_reimburse_charge_by_id,
            ),
            "purchase": _EntityConfig(
                purchase_client.query_purchases_by_ids,
                purchase_line_repo.read_lines_missing_reimburse_charge_by_realm_id,
                purchase_line_repo.update_reimburse_charge_by_id,
            ),
            "vendor_credit": _EntityConfig(
                vendor_credit_client.query_vendor_credits_by_ids,
                vendor_credit_repo.read_lines_missing_reimburse_charge_by_realm_id,
                vendor_credit_repo.update_line_reimburse_charge_by_id,
            ),
        }

    def run(
        self,
        *,
        entities: Iterable[str] = ALL_ENTITIES,
        dry_run: bool = True,
        resume_from: Optional[str] = None,
        max_batches: Optional[int] = None,
    ) -> Dict[str, Any]:
        entities = tuple(entities)
        if resume_from not in (None, "") and len(entities) != 1:
            raise ValueError("resume_from names a header id of ONE entity: pass exactly one entity with it")
        result: Dict[str, Any] = {
            "dry_run": dry_run, "entities": {}, "batches": 0,
            "aborted": None, "stopped": None, "entity": None, "resume_from": None,
        }
        floor = int(resume_from) if resume_from not in (None, "") else None
        try:
            for entity in entities:
                result["entity"] = entity
                result["resume_from"] = None  # per-entity: resume_from always refers to result["entity"]
                result["entities"][entity] = dict.fromkeys(_COUNTERS, 0)
                self._run_entity(entity, result, dry_run, floor, max_batches)
            result["entity"] = None
        except _Stop:
            result["stopped"] = "max_batches"
        except Exception as exc:  # systemic: stop everything, never continue past a fetch/write failure
            logger.exception("charge-link backfill aborted in %s", result["entity"])
            result["aborted"] = type(exc).__name__
        return result

    def _run_entity(self, entity, result, dry_run, floor, max_batches) -> None:
        cfg = self._plan[entity]
        fetch, write = cfg.fetch, cfg.write
        counts = result["entities"][entity]

        by_header: Dict[str, List[Any]] = {}
        for row in cfg.read_missing(self.realm_id):
            if floor is not None and int(row.header_qbo_id) <= floor:
                continue
            by_header.setdefault(row.header_qbo_id, []).append(row)

        header_ids = list(by_header)
        for start in range(0, len(header_ids), BATCH_SIZE):
            if max_batches is not None and result["batches"] >= max_batches:
                raise _Stop()
            batch = header_ids[start:start + BATCH_SIZE]
            live = {str(h.id): h for h in fetch(batch)}
            for hid in batch:
                header = live.get(hid)
                if header is None:
                    counts["header_gone"] += len(by_header[hid])
                    continue
                live_lines = {ln.id: ln for ln in (header.line or []) if ln.id}
                for row in by_header[hid]:
                    line = live_lines.get(row.qbo_line_id)
                    if line is None:
                        counts["line_gone"] += 1
                        continue
                    rc, ambiguous = reimburse_charge_link(line.linked_txn)
                    if ambiguous:
                        counts["ambiguous"] += 1
                    elif rc is None:
                        counts["no_link_live"] += 1
                    elif dry_run:
                        counts["would_write"] += 1
                    elif write(row.id, rc):
                        counts["written"] += 1
                    else:
                        counts["not_written"] += 1
            result["batches"] += 1
            result["resume_from"] = batch[-1]
            logger.info("charge-link backfill %s batch %d: %d headers, counts=%s",
                        entity, result["batches"], len(batch), counts)
