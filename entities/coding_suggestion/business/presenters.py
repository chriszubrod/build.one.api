"""API-facing shapes for coding suggestions (U-676b)."""
from __future__ import annotations

from typing import Callable, Optional

from entities.project.business.service import ProjectService


def suggestion_to_api_payload(
    suggestion,
    *,
    project_service_factory: Optional[Callable[[], ProjectService]] = None,
) -> dict:
    ps_factory = project_service_factory or (lambda: ProjectService())
    project_public_id = None
    project_name = None
    if suggestion.project_id is not None:
        project = ps_factory().read_by_id(suggestion.project_id)
        if project is not None:
            project_public_id = project.public_id
            project_name = project.name

    confidence = (
        str(suggestion.confidence) if suggestion.confidence is not None else None
    )
    return {
        "project_public_id": project_public_id,
        "project_name": project_name,
        "sub_cost_code_public_id": suggestion.sub_cost_code_public_id,
        "sub_cost_code_number": suggestion.sub_cost_code_number,
        "sub_cost_code_name": suggestion.sub_cost_code_name,
        "source": suggestion.source,
        "reason": suggestion.reason,
        "confidence": confidence,
        "model_version": suggestion.model_version,
        "budget_check": suggestion.budget_check,
        "budget_remaining": (
            str(suggestion.budget_remaining)
            if suggestion.budget_remaining is not None
            else None
        ),
        "text_status": suggestion.text_status,
    }
