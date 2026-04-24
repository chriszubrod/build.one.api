"""CostCode specialist agent — handles CostCode catalog and relationships.

Scope: read-only for now (list + read). Writes come when we have a user
need for CostCode CRUD via agents. Credentials and role grants for
create/update/delete are already provisioned on `cost_code_agent`.

Importing this package triggers tool + agent registration.
"""
# Entity tools — idempotent; scout / sub_cost_code_specialist also import
# these so they may already be in the registry.
import entities.cost_code.intelligence.tools  # noqa: F401
import entities.sub_cost_code.intelligence.tools  # noqa: F401

from intelligence.agents.cost_code_specialist.definition import (  # noqa: F401
    cost_code_specialist,
)
