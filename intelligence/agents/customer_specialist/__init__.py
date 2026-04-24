"""Customer specialist agent — Customer CRUD + Project read for child queries.

Importing this package triggers tool + agent registration.
"""
import entities.customer.intelligence.tools  # noqa: F401
import entities.project.intelligence.tools  # noqa: F401

from intelligence.agents.customer_specialist.definition import (  # noqa: F401
    customer_specialist,
)
