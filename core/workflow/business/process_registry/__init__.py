"""
core.workflow.process_registry
==============================
Process registry package. Exposes the full public API from registry_loader
so callers can import directly from the package rather than the module.

Usage:
    from core.workflow.process_registry import (
        get_email_process,
        get_entity_process,
        is_valid_transition,
        get_initial_stage,
        get_sla_for_stage,
        requires_action,
        should_auto_advance,
        get_entity_handoff,
        get_all_email_process_types,
        get_all_entity_process_types,
    )
"""

from core.workflow.business.process_registry.registry_loader import (
    get_email_process,
    get_entity_process,
    is_valid_transition,
    get_initial_stage,
    get_action_required_stages,
    get_auto_advance_stages,
    get_sla_for_stage,
    get_all_email_process_types,
    get_all_entity_process_types,
    get_entity_handoff,
    requires_action,
    should_auto_advance,
)

__all__ = [
    "get_email_process",
    "get_entity_process",
    "is_valid_transition",
    "get_initial_stage",
    "get_action_required_stages",
    "get_auto_advance_stages",
    "get_sla_for_stage",
    "get_all_email_process_types",
    "get_all_entity_process_types",
    "get_entity_handoff",
    "requires_action",
    "should_auto_advance",
]
