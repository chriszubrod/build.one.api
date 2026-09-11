"""Pure-logic lifecycle + review-kind resolution.

Keys ONLY on ReviewStatus flags + position (IsDeclined / IsFinal / SortOrder),
never on the admin-editable Name. See
`docs/design/u357-unified-status-review-status.md` §2.
"""

from typing import Optional

LIFECYCLE_STATUSES = (
    "draft",
    "submitted",
    "in_review",
    "approved",
    "declined",
    "completed",
)

REVIEW_STATUS_KINDS = (
    "none",
    "submitted",
    "in_review",
    "approved",
    "declined",
)


def review_kind_from_flags(
    *,
    is_declined: Optional[bool],
    is_final: Optional[bool],
    is_initial: Optional[bool],
) -> str:
    """Map a ReviewStatus row's flags to the canonical kind.

    `declined` if IsDeclined; else `approved` if IsFinal; else `submitted` if
    IsInitial; else `in_review` (any admin-added intermediate stage collapses
    here).

    All three key on FLAGS (U-444). `submitted` used to key on POSITION —
    `sort_order == MIN(active non-declined sort_order)`, resolved per request —
    which had two failure modes nothing guarded: two rows sharing a SortOrder
    both derived `submitted`, and inserting any row at or below the current MIN
    retroactively relabelled every stored `submitted` as `in_review`, because
    the kind is computed at read time. Codex caught that U-444's first cut moved
    only the SOURCE of the boundary to a flag and left this comparison
    positional.

    Losing the `first_sort_order` parameter also removed a DB round-trip per
    request from every caller: the boundary is now on the Review row itself.
    """
    if is_declined:
        return "declined"
    if is_final:
        return "approved"
    if is_initial:
        return "submitted"
    return "in_review"


def resolve_document_status(
    *,
    is_draft: Optional[bool],
    review_kind: Optional[str],
) -> str:
    """Derive lifecycle `status` from IsDraft × latest review kind.

    `completed ⇔ is_draft is False`. A draft with no review is `draft`. A draft
    with a review rests at that review kind. A finalized document with a
    still-open review is `completed` with a stale kind (the misfit the
    inbox predicate later suppresses) — we never fabricate an Approved row.
    """
    if is_draft is False:
        return "completed"
    if review_kind in (None, "none"):
        return "draft"
    return review_kind


def attach_lifecycle(
    payload: dict,
    *,
    is_draft: Optional[bool],
    review,
) -> dict:
    """Stamp `status` + `review_status*` onto an already-serialized dict.

    Mutates and returns `payload`. `review` is a Review dataclass or None.
    `review_status` stays today's meaning (admin Name, or null). Clients
    branch on `review_status_kind`, not the Name.
    """
    if review is None:
        kind = "none"
        payload["review_status"] = None
        payload["review_status_kind"] = kind
        payload["review_status_is_final"] = None
        payload["review_status_is_declined"] = None
    else:
        kind = review_kind_from_flags(
            is_declined=getattr(review, "status_is_declined", None),
            is_final=getattr(review, "status_is_final", None),
            is_initial=getattr(review, "status_is_initial", None),
        )
        payload["review_status"] = getattr(review, "status_name", None)
        payload["review_status_kind"] = kind
        # Raw, NOT bool()-coerced: the Bill list has always emitted these
        # straight off the row, so coercing would turn a hypothetical "unknown"
        # (None) into a confident False. `kind` above already treats None as
        # falsy, so preserving the raw value changes nothing it decides.
        payload["review_status_is_final"] = getattr(review, "status_is_final", None)
        payload["review_status_is_declined"] = getattr(review, "status_is_declined", None)

    payload["status"] = resolve_document_status(
        is_draft=is_draft,
        review_kind=None if kind == "none" else kind,
    )
    return payload
