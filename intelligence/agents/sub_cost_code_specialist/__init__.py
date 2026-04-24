"""SubCostCode specialist agent — narrow-scope worker invoked via delegation.

Scope: SubCostCode (read + create/update/delete) and CostCode (read for
parent resolution). Has its own User + Auth + Role with permissions
limited to those two modules — least-privilege compared to scout's
delegating-only role.

Importing this package triggers tool + agent registration.
"""
# Ensure the entity tools are registered. These imports are idempotent —
# Python caches the module so scout's import doesn't conflict.
import entities.sub_cost_code.intelligence.tools  # noqa: F401
import entities.cost_code.intelligence.tools  # noqa: F401

from intelligence.agents.sub_cost_code_specialist.definition import (  # noqa: F401
    sub_cost_code_specialist,
)
