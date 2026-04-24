"""Project specialist agent — Project CRUD + Customer read for parent resolution.

Importing this package triggers tool + agent registration.
"""
import entities.project.intelligence.tools  # noqa: F401
import entities.customer.intelligence.tools  # noqa: F401

from intelligence.agents.project_specialist.definition import (  # noqa: F401
    project_specialist,
)
