"""HTTP-mappable refusals for transactional POST /apply/review-decision/* (U-541/U-551b)."""

from __future__ import annotations


class ReviewDecisionApplyError(Exception):
    """HTTP-mappable refusal for transactional reviewer edit-and-decide routes."""

    def __init__(self, *, status_code: int, detail: str, error_code: str):
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        super().__init__(detail)

    @property
    def code(self) -> str:
        """Alias for tests and transitional callers."""
        return self.error_code


_REVIEW_DECISION_APPLY_DETAIL_TEMPLATES: dict[str, str] = {
    "not_a_reviewer": "You are not a reviewer for this {parent}.",
    "line_row_version_stale": (
        "The {parent} line was updated by someone else; refetch and re-present."
    ),
    "review_state_stale": (
        "The review state has changed; drop this queued action."
    ),
    "idempotency_key_conflict": (
        "This idempotency key was already used for a different {parent}."
    ),
    "multi_line_not_supported": (
        "Multi-line {parent}s are not supported for reviewer decisions in the app."
    ),
}


def review_decision_apply_error(
    error_code: str,
    *,
    status_code: int = 409,
    detail: str | None = None,
    parent: str,
) -> ReviewDecisionApplyError:
    if detail is None:
        detail = _REVIEW_DECISION_APPLY_DETAIL_TEMPLATES[error_code].format(
            parent=parent
        )
    return ReviewDecisionApplyError(
        status_code=status_code,
        detail=detail,
        error_code=error_code,
    )
