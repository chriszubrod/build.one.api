"""
U-778a: backfill the ReimburseCharge link onto staged Bill / Purchase / VendorCredit lines.

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run writes NO staging links, but it still READS
QBO and the DB: the QBO transport meters every call (IncrementQboApiUsage, fail-open by design) and may
refresh the OAuth token - the transport's normal, intended side effects of any QBO read.
All logic lives in QboChargeLinkBackfillService. On abort or --max-batches the result carries
`resume_from` for the entity named in `entity`; re-run with --entity <that entity> --resume-from <id>.
--resume-from requires a single --entity (the checkpoint belongs to one entity); `--entity all` is refused.

Usage:
  PYTHONPATH=. python scripts/backfill_u778a_charge_links.py
  PYTHONPATH=. python scripts/backfill_u778a_charge_links.py --entity bill --apply --max-batches 5
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional, Sequence

from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.bill.external.client import QboBillClient
from integrations.intuit.qbo.bill.persistence.repo import QboBillLineRepository
from integrations.intuit.qbo.charge_link.business.backfill import ALL_ENTITIES, QboChargeLinkBackfillService
from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseLineRepository
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository
from scripts.sync_helper import assert_cli_system_admin

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(
        "Backfill line ReimburseCharge links. Dry-run by default: writes no staging links, but still reads QBO - "
        "the QBO transport meters every call (IncrementQboApiUsage, fail-open) and may refresh the OAuth token."))
    ap.add_argument("--entity", choices=[*ALL_ENTITIES, "all"], default="all")
    ap.add_argument("--apply", action="store_true", help="Write the links (default: dry-run).")
    ap.add_argument("--resume-from", default=None, help="Skip headers with QboId <= this (numeric). Requires a single --entity.")
    ap.add_argument("--max-batches", type=int, default=None, help="Stop cleanly after N batches of 100 headers.")
    args = ap.parse_args(argv)

    if args.resume_from not in (None, "") and args.entity == "all":
        print("error: --resume-from requires a single --entity (bill, purchase or vendor_credit), not 'all'",
              file=sys.stderr)
        return 2

    assert_cli_system_admin()
    realm_id = QboAuthService().resolve_realm_id()  # the single connected realm (first QboAuth row)

    with QboBillClient(realm_id=realm_id) as bills, QboPurchaseClient(realm_id=realm_id) as purchases, \
            QboVendorCreditClient(realm_id=realm_id) as credits:
        service = QboChargeLinkBackfillService(
            realm_id,
            bill_client=bills,
            purchase_client=purchases,
            vendor_credit_client=credits,
            bill_line_repo=QboBillLineRepository(),
            purchase_line_repo=QboPurchaseLineRepository(),
            vendor_credit_repo=QboVendorCreditRepository(),
        )
        result = service.run(
            entities=ALL_ENTITIES if args.entity == "all" else (args.entity,),
            dry_run=not args.apply,
            resume_from=args.resume_from,
            max_batches=args.max_batches,
        )
    print(json.dumps(result, indent=2, default=str))
    return 1 if result["aborted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
