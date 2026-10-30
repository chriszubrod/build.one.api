"""U-535b b2: system-admin review API for Box -> bill link candidates.

Every route is behind ``require_system_admin()``. Runs and candidates are addressed by PublicId only.
Approve/reject/undo write the candidate's review columns only -- no Attachment, no link, no Bill.
Handlers are plain ``def`` (pyodbc + Box I/O run on the threadpool), like shared/api/admin_users.py.
Nothing here logs PDF text or snippets.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from entities.attachment_backfill.business.review import (
    DECISIONS,
    EVIDENCE_GROUPS,
    QUEUE_DEFAULT_LIMIT,
    QUEUE_MAX_LIMIT,
    REJECT_REASONS,
    REVIEW_STATUSES,
    AttachmentBackfillReviewService,
    CandidateNotFound,
    PdfUnavailable,
    ReviewRefused,
    RunNotFound,
    decode_row_version,
)
from shared.api.errors import ApiError, ErrorCode
from shared.api.responses import item_response, list_response, parse_public_id, raise_not_found
from shared.rbac import require_system_admin

router = APIRouter(prefix="/api/v1/admin/attachment-backfill", tags=["api", "admin-attachment-backfill"])

# Built from review.py's tuples (Literal[tuple] == Literal[*items]): one Python source for each value set.
Decision = Literal[DECISIONS]
EvidenceGroup = Literal[EVIDENCE_GROUPS]
ReviewStatusFilter = Literal[REVIEW_STATUSES + ("all",)]
RejectReason = Literal[REJECT_REASONS]
# Base64 in the JSON body, decoded once at validation to the 8-byte ROWVERSION (ValueError -> 422).
RowVersion = Annotated[bytes, BeforeValidator(decode_row_version)]

# Refusal code -> HTTP status. Anything unmapped is a 409 (a state conflict).
_REFUSAL_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "stale": status.HTTP_409_CONFLICT,
    "not_pending": status.HTTP_409_CONFLICT,
    "bill_missing": status.HTTP_409_CONFLICT,
    "bill_claimed_elsewhere": status.HTTP_409_CONFLICT,
    "not_reviewed": status.HTTP_409_CONFLICT,
    "run_not_ready": status.HTTP_409_CONFLICT,
    "linked": status.HTTP_409_CONFLICT,
}
_PDF_STATUS = {
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "fetch_failed": status.HTTP_502_BAD_GATEWAY,
    "not_pdf": status.HTTP_422_UNPROCESSABLE_CONTENT,
}
PDF_HEADERS = {
    "Content-Disposition": 'inline; filename="candidate.pdf"',
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "sandbox",
}
_MATCH_HEADER = {True: "true", False: "false", None: "unknown"}


def get_review_service() -> AttachmentBackfillReviewService:
    return AttachmentBackfillReviewService()


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reject_reason: Optional[RejectReason] = None
    row_version: RowVersion

    @model_validator(mode="after")
    def _check(self):
        if self.decision == "rejected" and self.reject_reason is None:
            raise ValueError("reject_reason is required when decision is 'rejected'")
        if self.decision == "approved" and self.reject_reason is not None:
            raise ValueError("reject_reason is only allowed when decision is 'rejected'")
        return self


class UndoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_version: RowVersion


def _refused(code: str) -> ApiError:
    return ApiError(
        status_code=_REFUSAL_STATUS.get(code, status.HTTP_409_CONFLICT),
        detail=f"Review refused: {code}",
        error_code=code,
    )


def _parse_year(year: Optional[str]) -> tuple[bool, Optional[int]]:
    """(no_year, year): 'none' selects candidates whose bill is gone (the NULL year the facets emit)."""
    if year is None:
        return False, None
    if year.strip().lower() == "none":
        return True, None
    try:
        value = int(year)
    except ValueError:
        value = None
    if value is None or not 1900 <= value <= 2999:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="year must be a year between 1900 and 2999, or 'none'",
            error_code=ErrorCode.VALIDATION_ERROR,
        )
    return False, value


def _user_id(current_user: dict) -> int:
    user_id = current_user.get("user_id")
    if user_id is None:
        raise ApiError(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    return int(user_id)


@router.get("/runs")
def list_runs(
    current_user: dict = Depends(require_system_admin()),
    service: AttachmentBackfillReviewService = Depends(get_review_service),
):
    return list_response(service.list_runs())


@router.get("/runs/{run_public_id}/facets")
def run_facets(
    run_public_id: str,
    current_user: dict = Depends(require_system_admin()),
    service: AttachmentBackfillReviewService = Depends(get_review_service),
):
    run_public_id = parse_public_id(run_public_id, "run_public_id")
    try:
        rows = service.facets(run_public_id)
    except RunNotFound:
        raise_not_found("Run")
    return list_response(rows)


@router.get("/runs/{run_public_id}/queue")
def run_queue(
    run_public_id: str,
    group: Optional[EvidenceGroup] = Query(default=None),
    project_id: Optional[str] = Query(default=None, description="Project PublicId, or 'none' for no project"),
    year: Optional[str] = Query(default=None, description="Bill year, or 'none' for candidates whose bill is gone"),
    review_status: ReviewStatusFilter = Query(default="pending"),
    after: Optional[str] = Query(default=None, description="PublicId of the previous page's last row"),
    limit: int = Query(default=QUEUE_DEFAULT_LIMIT, ge=1, le=QUEUE_MAX_LIMIT),
    current_user: dict = Depends(require_system_admin()),
    service: AttachmentBackfillReviewService = Depends(get_review_service),
):
    run_public_id = parse_public_id(run_public_id, "run_public_id")
    no_project = project_id is not None and project_id.strip().lower() == "none"
    project_public_id = None if (project_id is None or no_project) else parse_public_id(project_id, "project_id")
    after_public_id = parse_public_id(after, "after") if after else None
    no_year, bill_year = _parse_year(year)
    try:
        rows, next_after = service.queue(
            run_public_id,
            group=group,
            project_public_id=project_public_id,
            no_project=no_project,
            year=bill_year,
            no_year=no_year,
            review_status=None if review_status == "all" else review_status,
            after=after_public_id,
            limit=limit,
        )
    except RunNotFound:
        raise_not_found("Run")
    body = list_response(rows)
    body["next_after"] = next_after
    return body


@router.get("/candidates/{candidate_public_id}")
def get_candidate(
    candidate_public_id: str,
    current_user: dict = Depends(require_system_admin()),
    service: AttachmentBackfillReviewService = Depends(get_review_service),
):
    candidate_public_id = parse_public_id(candidate_public_id, "candidate_public_id")
    try:
        return item_response(service.candidate_detail(candidate_public_id))
    except CandidateNotFound:
        raise_not_found("Candidate")


@router.get("/candidates/{candidate_public_id}/pdf")
def get_candidate_pdf(
    candidate_public_id: str,
    current_user: dict = Depends(require_system_admin()),
    service: AttachmentBackfillReviewService = Depends(get_review_service),
):
    candidate_public_id = parse_public_id(candidate_public_id, "candidate_public_id")
    try:
        content, matches = service.candidate_pdf(candidate_public_id)
    except CandidateNotFound:
        raise_not_found("Candidate")
    except PdfUnavailable as exc:
        raise ApiError(
            status_code=_PDF_STATUS.get(exc.code, status.HTTP_502_BAD_GATEWAY),
            detail=f"PDF unavailable: {exc.code}",
            error_code=exc.code,
        ) from None
    headers = dict(PDF_HEADERS)
    headers["X-Content-Matches-Check"] = _MATCH_HEADER[matches]
    return Response(content=content, media_type="application/pdf", headers=headers)


@router.post("/candidates/{candidate_public_id}/review")
def review_candidate(
    candidate_public_id: str,
    body: ReviewBody,
    current_user: dict = Depends(require_system_admin()),
    service: AttachmentBackfillReviewService = Depends(get_review_service),
):
    candidate_public_id = parse_public_id(candidate_public_id, "candidate_public_id")
    try:
        row = service.review(
            candidate_public_id,
            decision=body.decision,
            reject_reason=body.reject_reason,
            row_version=body.row_version,
            reviewed_by_user_id=_user_id(current_user),
        )
    except ReviewRefused as exc:
        raise _refused(exc.code) from None
    return item_response(row)


@router.post("/candidates/{candidate_public_id}/undo")
def undo_candidate_review(
    candidate_public_id: str,
    body: UndoBody,
    current_user: dict = Depends(require_system_admin()),
    service: AttachmentBackfillReviewService = Depends(get_review_service),
):
    candidate_public_id = parse_public_id(candidate_public_id, "candidate_public_id")
    try:
        row = service.undo(candidate_public_id, row_version=body.row_version)
    except ReviewRefused as exc:
        raise _refused(exc.code) from None
    return item_response(row)

