"""
Process Registry Loader
=======================
Loads, validates, and exposes both the email and entity process registries
to the rest of the application. Acts as the single source of truth for all
process definitions, stage maps, and transition rules.

Usage:
    from core.workflow.process_registry import registry_loader

    # Look up a process definition
    process = registry_loader.get_email_process("BILL_DOCUMENT")

    # Validate a stage transition before applying it
    valid = registry_loader.is_valid_transition("BILL_DOCUMENT", "RECEIVED", "EXTRACTING", registry_type="email")

    # Get all stages that require owner action
    stages = registry_loader.get_action_required_stages("BILL", registry_type="entity")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry file paths — relative to this loader file
# ---------------------------------------------------------------------------
_REGISTRY_DIR = Path(__file__).parent
_EMAIL_REGISTRY_PATH  = _REGISTRY_DIR / "email_processes.json"
_ENTITY_REGISTRY_PATH = _REGISTRY_DIR / "entity_processes.json"


# ---------------------------------------------------------------------------
# Internal state — loaded once at import time
# ---------------------------------------------------------------------------
_email_registry:  dict[str, Any] = {}
_entity_registry: dict[str, Any] = {}


def _load_registry(path: Path) -> dict[str, Any]:
    """Load and parse a registry JSON file. Raises on missing file or invalid JSON."""
    if not path.exists():
        raise FileNotFoundError(f"Process registry file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _validate_registry(registry: dict[str, Any], registry_type: str) -> None:
    """
    Basic structural validation of a loaded registry.
    Raises ValueError if required keys are missing or transitions reference
    stages not declared in the stages list.
    """
    processes = registry.get("processes", {})
    if not processes:
        raise ValueError(f"{registry_type} registry contains no process definitions.")

    for process_type, definition in processes.items():
        required_keys = ["description", "initial_stage", "stages", "transitions",
                         "requires_action_at", "auto_advance_at"]
        for key in required_keys:
            if key not in definition:
                raise ValueError(
                    f"Process '{process_type}' in {registry_type} registry "
                    f"is missing required key: '{key}'"
                )

        declared_stages = set(definition["stages"])

        # initial_stage must be declared
        if definition["initial_stage"] not in declared_stages:
            raise ValueError(
                f"Process '{process_type}': initial_stage "
                f"'{definition['initial_stage']}' is not in stages list."
            )

        # All transition keys and values must be declared stages
        for from_stage, to_stages in definition["transitions"].items():
            if from_stage not in declared_stages:
                raise ValueError(
                    f"Process '{process_type}': transition source "
                    f"'{from_stage}' is not in stages list."
                )
            for to_stage in to_stages:
                if to_stage not in declared_stages:
                    raise ValueError(
                        f"Process '{process_type}': transition target "
                        f"'{to_stage}' (from '{from_stage}') is not in stages list."
                    )

        # requires_action_at and auto_advance_at must reference declared stages
        for stage in definition.get("requires_action_at", []):
            if stage not in declared_stages:
                raise ValueError(
                    f"Process '{process_type}': requires_action_at stage "
                    f"'{stage}' is not in stages list."
                )
        for stage in definition.get("auto_advance_at", []):
            if stage not in declared_stages:
                raise ValueError(
                    f"Process '{process_type}': auto_advance_at stage "
                    f"'{stage}' is not in stages list."
                )

    logger.info(
        f"{registry_type.capitalize()} process registry loaded: "
        f"{len(processes)} process(es) — {list(processes.keys())}"
    )


def _initialize() -> None:
    """Load and validate both registries. Called once at module import."""
    global _email_registry, _entity_registry

    _email_registry  = _load_registry(_EMAIL_REGISTRY_PATH)
    _entity_registry = _load_registry(_ENTITY_REGISTRY_PATH)

    _validate_registry(_email_registry,  registry_type="email")
    _validate_registry(_entity_registry, registry_type="entity")


_initialize()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_email_process(process_type: str) -> dict[str, Any]:
    """
    Return the full process definition for an email process type.
    Raises KeyError if the process type is not registered.
    """
    processes = _email_registry.get("processes", {})
    if process_type not in processes:
        raise KeyError(
            f"Email process type '{process_type}' is not defined in the registry. "
            f"Available: {list(processes.keys())}"
        )
    return processes[process_type]


def get_entity_process(process_type: str) -> dict[str, Any]:
    """
    Return the full process definition for an entity process type.
    Raises KeyError if the process type is not registered.
    """
    processes = _entity_registry.get("processes", {})
    if process_type not in processes:
        raise KeyError(
            f"Entity process type '{process_type}' is not defined in the registry. "
            f"Available: {list(processes.keys())}"
        )
    return processes[process_type]


def is_valid_transition(
    process_type: str,
    from_stage: str,
    to_stage: str,
    registry_type: str = "email"
) -> bool:
    """
    Return True if transitioning from from_stage to to_stage is permitted
    for the given process type. Returns False (does not raise) on invalid input.
    """
    try:
        definition = (
            get_email_process(process_type)
            if registry_type == "email"
            else get_entity_process(process_type)
        )
    except KeyError:
        logger.warning(f"is_valid_transition: unknown process type '{process_type}'")
        return False

    allowed = definition.get("transitions", {}).get(from_stage, [])
    return to_stage in allowed


def get_initial_stage(process_type: str, registry_type: str = "email") -> str:
    """Return the initial stage for a process type."""
    definition = (
        get_email_process(process_type)
        if registry_type == "email"
        else get_entity_process(process_type)
    )
    return definition["initial_stage"]


def get_action_required_stages(
    process_type: str,
    registry_type: str = "email"
) -> list[str]:
    """Return the list of stages that require owner action for a process type."""
    definition = (
        get_email_process(process_type)
        if registry_type == "email"
        else get_entity_process(process_type)
    )
    return definition.get("requires_action_at", [])


def get_auto_advance_stages(
    process_type: str,
    registry_type: str = "email"
) -> list[str]:
    """Return the list of stages that auto-advance without owner action."""
    definition = (
        get_email_process(process_type)
        if registry_type == "email"
        else get_entity_process(process_type)
    )
    return definition.get("auto_advance_at", [])


def get_sla_for_stage(
    process_type: str,
    stage: str,
    registry_type: str = "email"
) -> Optional[dict[str, Any]]:
    """
    Return the SLA definition for a stage, or None if no SLA is defined.

    Returns a dict with keys:
        max_hours (int)  — maximum hours allowed in this stage
        on_breach (str)  — EventType value fired when SLA is exceeded
    """
    definition = (
        get_email_process(process_type)
        if registry_type == "email"
        else get_entity_process(process_type)
    )
    return definition.get("sla", {}).get(stage)


def get_all_email_process_types() -> list[str]:
    """Return all registered email process type keys."""
    return list(_email_registry.get("processes", {}).keys())


def get_all_entity_process_types() -> list[str]:
    """Return all registered entity process type keys."""
    return list(_entity_registry.get("processes", {}).keys())


def get_entity_handoff(email_process_type: str) -> Optional[str]:
    """
    Return the entity process type that an email process hands off to
    once the business entity is created. Returns None if no handoff defined.
    """
    try:
        definition = get_email_process(email_process_type)
        return definition.get("entity_handoff")
    except KeyError:
        return None


def requires_action(
    process_type: str,
    stage: str,
    registry_type: str = "email"
) -> bool:
    """Return True if the given stage requires owner action."""
    return stage in get_action_required_stages(process_type, registry_type)


def should_auto_advance(
    process_type: str,
    stage: str,
    registry_type: str = "email"
) -> bool:
    """Return True if the given stage should auto-advance without owner action."""
    return stage in get_auto_advance_stages(process_type, registry_type)
