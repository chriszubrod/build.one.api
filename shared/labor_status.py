"""Canonical status vocabulary for labor-shaped entities.

The lifecycle covers every entity that goes through an author → reviewer →
downstream-action pipeline. Landed on Labor first (2026-07-02); other
entities (Bill / Expense / BillCredit / Invoice / Budget) migrate in
follow-up work.

State machine:

    draft ──► submitted ──► in_review ──► approved ──► completed
                    ▲            │                        ▲
                    │            ▼                        │
                    └── draft ◄ declined                   │
                                                  (terminal — no exits)

- `draft`      — author is editing; not yet in the reviewer inbox
- `submitted`  — sent for review; reviewer inbox has it
- `in_review`  — reviewer has picked it up but not decided yet (optional
                 intermediate; useful for multi-reviewer or in-flight
                 workflows)
- `approved`   — reviewer signed off; queued for the downstream action
                 (Generate Bills for ContractLabor; Generate Invoice for
                 EmployeeLabor; etc.)
- `declined`   — reviewer sent back; author fixes → resubmits (or the
                 workflow drops it back to draft)
- `completed`  — terminal; downstream external effect has landed
                 (billed / invoiced / paid / posted)

Any downstream code that used to filter on the legacy values should read
these constants instead. Migration 2026_07_02_unify_labor_status_vocab.sql
does the in-place rename of prod rows.
"""

# Canonical values
STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "submitted"
STATUS_IN_REVIEW = "in_review"
STATUS_APPROVED = "approved"
STATUS_DECLINED = "declined"
STATUS_COMPLETED = "completed"

# The full set (in lifecycle order) — useful for validation.
ALL_STATUSES: tuple[str, ...] = (
    STATUS_DRAFT,
    STATUS_SUBMITTED,
    STATUS_IN_REVIEW,
    STATUS_APPROVED,
    STATUS_DECLINED,
    STATUS_COMPLETED,
)

# Transition machine. Permissive — a specific service may narrow further.
# Notably `submitted → draft` is allowed so an author can recall before
# a reviewer acts; and `declined → submitted` allows a direct resubmit.
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_DRAFT: frozenset({STATUS_SUBMITTED, STATUS_APPROVED}),
    STATUS_SUBMITTED: frozenset(
        {STATUS_IN_REVIEW, STATUS_APPROVED, STATUS_DECLINED, STATUS_DRAFT}
    ),
    STATUS_IN_REVIEW: frozenset(
        {STATUS_APPROVED, STATUS_DECLINED, STATUS_DRAFT}
    ),
    STATUS_APPROVED: frozenset({STATUS_COMPLETED, STATUS_DRAFT}),
    STATUS_DECLINED: frozenset({STATUS_DRAFT, STATUS_SUBMITTED}),
    STATUS_COMPLETED: frozenset(),  # terminal
}


def is_terminal(status: str) -> bool:
    """True when the row is locked from further edits (completed)."""
    return status == STATUS_COMPLETED


def is_editable(status: str) -> bool:
    """True when the row can still be edited by the author. Currently
    everything except `completed`. `approved` remains editable because
    Chris' workflow sometimes revises after approval."""
    return status != STATUS_COMPLETED


# Back-compat helpers for the transition period. If any caller still
# hands us the old value, translate it. Remove once every entity has
# migrated + code no longer references the old names.
_LEGACY_ALIASES = {
    "pending_review": STATUS_DRAFT,
    "ready": STATUS_APPROVED,
    "billed": STATUS_COMPLETED,
    "invoiced": STATUS_COMPLETED,
    "rejected": STATUS_DECLINED,
}


def canonicalize(status: str | None) -> str | None:
    """Translate a legacy value to its canonical replacement. Returns
    the input unchanged for unknown / already-canonical values."""
    if status is None:
        return None
    return _LEGACY_ALIASES.get(status, status)
