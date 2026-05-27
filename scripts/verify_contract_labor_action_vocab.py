"""Regression check on the `delegated_to_contract_labor_specialist`
AgentDecidedAction vocabulary rollout.

Phase 2 of the contract_labor_specialist agent build (TODO.md line 204,
item 5b). Added a counter column to ReadEmailSenderHistory + threaded
it through the Python hydrator + the mark_email_outcome tool description
+ the email_specialist prompt + CLAUDE.md.

This script locks the wiring by exercising the JR Scruggs fixture
(`jrscruggs07@gmail.com`) end-to-end:

  1. Raw sproc: confirms the new `ActionDelegatedContractLabor` column
     appears in result set 1.
  2. Service layer: confirms the hydrator surfaces the new value as
     `by_action["delegated_to_contract_labor_specialist"]`.

Until Phase 6 wires email_specialist to actually delegate, no email
should carry this action, so the value is expected to be `0`.

Read-only — no row creation.

Run:
    .venv/bin/python scripts/verify_contract_labor_action_vocab.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


JR_SCRUGGS_EMAIL = "jrscruggs07@gmail.com"
NEW_ACTION_KEY = "delegated_to_contract_labor_specialist"
NEW_COUNTER_COL = "ActionDelegatedContractLabor"


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    print("=== delegated_to_contract_labor_specialist vocab check ===")
    failures: list[str] = []

    # 1. Raw sproc — confirm the new counter column is in result set 1.
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "EXEC ReadEmailSenderHistory @FromEmail = ?",
            (JR_SCRUGGS_EMAIL,),
        )
        col_names = [d[0] for d in cur.description]
        print(f"  step 1 (raw sproc)         : columns include "
              f"{NEW_COUNTER_COL}? = {NEW_COUNTER_COL in col_names}")
        if NEW_COUNTER_COL not in col_names:
            failures.append(
                f"step 1: {NEW_COUNTER_COL!r} missing from sproc result "
                f"set 1. Columns: {col_names}"
            )
        else:
            row = cur.fetchone()
            counter_value = int(getattr(row, NEW_COUNTER_COL) or 0)
            print(f"           {NEW_COUNTER_COL} = {counter_value}")
            if counter_value != 0:
                failures.append(
                    f"step 1: {NEW_COUNTER_COL}={counter_value}, expected "
                    f"0 (no email should be stamped with the new action "
                    f"yet — routing lands in Phase 6)"
                )

    # 2. Service layer — confirm the hydrator surfaces the new key.
    from entities.email_message.business.service import EmailMessageService

    history = EmailMessageService().get_sender_history(JR_SCRUGGS_EMAIL)
    by_action = history.get("prior_emails", {}).get("by_action", {})
    print(f"  step 2 (hydrator by_action): {NEW_ACTION_KEY!r} present? = "
          f"{NEW_ACTION_KEY in by_action}")
    if NEW_ACTION_KEY not in by_action:
        failures.append(
            f"step 2: by_action[{NEW_ACTION_KEY!r}] missing. Got keys: "
            f"{sorted(by_action.keys())}"
        )
    else:
        value = by_action[NEW_ACTION_KEY]
        print(f"           by_action[{NEW_ACTION_KEY!r}] = {value}")
        if value != 0:
            failures.append(
                f"step 2: by_action[{NEW_ACTION_KEY!r}]={value}, expected 0"
            )

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(
        f"\nPASS — sproc surfaces {NEW_COUNTER_COL}, hydrator surfaces "
        f"{NEW_ACTION_KEY!r}; both at 0 until Phase 6 routing lands."
    )
    return 0


if __name__ == "__main__":
    sys.exit(verify())
