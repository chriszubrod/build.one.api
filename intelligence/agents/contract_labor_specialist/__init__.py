"""Contract Labor specialist agent — processes forwarded worker
timesheet emails into draft `ContractLabor` rows.

Invoked by `email_specialist` via `delegate_to_contract_labor_specialist`
when an incoming email classifies as `contract_labor_timesheet`. The
specialist:

  1. Binds the sender's email back to a contract-labor Vendor via
     `find_contract_labor_vendor_by_email`.
  2. Delegates the job-site address to `project_specialist` for Project
     lookup.
  3. Parses work_date / time_in / time_out / total_hours / description
     out of the email body + subject.
  4. Creates a `ContractLabor` row with `status='pending_review'` so a
     human reviewer can add rate / markup / SubCostCode before the row
     advances to `ready`.

Carries grants ONLY on Contract Labor (CRU), Vendors (R), and
Projects (R). All work flows through the agent's own user (auth via
`contract_labor_agent_username` + `contract_labor_agent_password`).

Importing this package triggers tool + delegation + agent registration.
"""
# Entity tools.
import entities.vendor.intelligence.tools  # noqa: F401
import entities.contract_labor.intelligence.tools  # noqa: F401

# project_specialist self-registers the `delegate_to_project_specialist`
# tool alongside its own agent registration — importing it here brings
# both into scope so contract_labor_specialist's tool list resolves.
import intelligence.agents.project_specialist  # noqa: F401

from intelligence.agents.contract_labor_specialist.definition import (  # noqa: F401
    contract_labor_specialist,
)
