"""
One-time backfill for dbo.Company.APAccountQboId/APAccountName (U-281).

Not required for correctness: QboAccountService.sync_from_qbo (the existing
scheduled qbo.Account pull) already re-derives and stamps this cache after
every batch, and BillBillConnector._get_ap_account_ref falls back to a live
qbo.Account scan whenever the cache is still empty — a live Bill push cannot
break just because the cache hasn't been populated yet. This script exists
only to close that rollout-window gap immediately for a realm whose next
scheduled pull is slow to arrive, rather than waiting on it.

SAFE BY DEFAULT: dry-run unless --apply is passed. Reuses the same
select_ap_account() selection QboAccountService/_get_ap_account_ref use, so
this can never derive a different answer than the live code paths would.

Usage:
  PYTHONPATH=. python scripts/backfill_company_ap_account.py
  PYTHONPATH=. python scripts/backfill_company_ap_account.py --apply
"""
from __future__ import annotations

import argparse
import logging

from entities.company.business.service import CompanyService
from integrations.intuit.qbo.account.business.service import select_ap_account
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_company_ap_account")


def _connected_realm_ids() -> list:
    """Every realm_id a dbo.Company row is already QBO-connected to."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT [RealmId] FROM dbo.[Company] WHERE [RealmId] IS NOT NULL")
        return [row.RealmId for row in cur.fetchall()]


def backfill_realm(
    realm_id: str,
    *,
    apply: bool,
    account_repo: QboAccountRepository,
    company_service: CompanyService,
) -> None:
    accounts = account_repo.read_by_realm_id(realm_id)
    ap_account = select_ap_account(accounts)
    qbo_id = ap_account.qbo_id if ap_account else None
    name = ap_account.name if ap_account else None
    verb = "setting" if apply else "would set"
    print(f"  realm={realm_id}: {verb} APAccountQboId={qbo_id!r} APAccountName={name!r}")
    if apply:
        company_service.set_ap_account(
            realm_id=realm_id, ap_account_qbo_id=qbo_id, ap_account_name=name
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Backfill dbo.Company.APAccountQboId/APAccountName from qbo.Account "
            "(dry-run by default)."
        )
    )
    ap.add_argument("--apply", action="store_true", help="Run the UPDATE (default: dry-run/read-only).")
    args = ap.parse_args()

    assert_cli_system_admin()

    realm_ids = _connected_realm_ids()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: Company AP-account cache backfill ({len(realm_ids)} realm(s)) ===")

    account_repo = QboAccountRepository()
    company_service = CompanyService()
    for realm_id in realm_ids:
        backfill_realm(
            realm_id, apply=args.apply, account_repo=account_repo, company_service=company_service
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
