"""Vendor specialist agent — Vendor CRUD with soft-delete semantics.

Importing this package triggers tool + agent registration.
"""
import entities.vendor.intelligence.tools  # noqa: F401

from intelligence.agents.vendor_specialist.definition import (  # noqa: F401
    vendor_specialist,
)
