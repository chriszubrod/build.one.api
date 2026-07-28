"""Single source of truth for mapping a SQL Server constraint violation to a
transport status and a clean, schema-free client message.

Consumed by BOTH shared/database.py::map_database_error (workflow /
ProcessEngine path) and shared/api/responses.py::raise_database_error
(entity-router path), so the mapping cannot drift between the two handlers.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kind constants
FK_REFERENCE = 'fk_reference'
FK_MISSING = 'fk_missing'
UNIQUE = 'unique'

FK_REFERENCE_MESSAGE = (
    'This record is still referenced by other records and cannot be deleted.'
)
FK_MISSING_MESSAGE = (
    'This record references another record that no longer exists.'
)
# Deliberately retains the token 'duplicates' (contains the substring
# 'duplicate') because the iOS offline client's duplicate-claim recovery
# matches on a message containing 'duplicate'/'unique'/the constraint name
# (BuildOne/Services/TimeEntry/TimeEntryService.swift isDuplicateKeyRejection).
# Deliberately AVOIDS the phrase 'already exists', because raise_workflow_error
# maps 'already exists' to 409 and 409 is the status the iOS client treats as
# an optimistic-concurrency conflict (which discards the queued local edit).
UNIQUE_MESSAGE = (
    'This record duplicates an existing record and cannot be saved.'
)


@dataclass(frozen=True)
class ConstraintViolation:
    kind: str
    message: str
    http_status: int


FK_REFERENCE_VIOLATION = ConstraintViolation(FK_REFERENCE, FK_REFERENCE_MESSAGE, 422)
FK_MISSING_VIOLATION = ConstraintViolation(FK_MISSING, FK_MISSING_MESSAGE, 422)
UNIQUE_VIOLATION = ConstraintViolation(UNIQUE, UNIQUE_MESSAGE, 422)

# Derived, never hand-maintained: the workflow path (ProcessEngine) stringifies the
# exception, so status_for_clean_message has only the message to go on. Deriving the
# reverse index from the violations above means the two directions cannot drift.
_STATUS_BY_CLEAN_MESSAGE: dict[str, int] = {
    v.message: v.http_status
    for v in (FK_REFERENCE_VIOLATION, FK_MISSING_VIOLATION, UNIQUE_VIOLATION)
}


def _has_error_number(text: str, number: str) -> bool:
    """True when *text* carries the SQL Server native error number the way the
    driver emits it: parenthesized, e.g. '... (547) (SQLExecDirectW)'.

    A bare substring test matched the number anywhere — inside a hostname, a
    port, a timestamp, a row id, or a duplicate-key VALUE — which let a genuine
    connection failure ('...connecting to db-unique.internal:2601') classify as
    a UNIQUE violation and surface a 422 constraint message.
    """
    return f'({number})' in text


def classify_constraint_violation(text: str) -> ConstraintViolation | None:
    """Classify a SQL Server constraint violation by error NUMBER *and* phrase.

    Two-signal detection is required because SQL 547 also covers CHECK-constraint
    violations, and matching 'constraint' alone would swallow CHECK and UNIQUE
    too. SQL Server's two 547 phrasings:
      - 'REFERENCE constraint' = a blocked DELETE/UPDATE (children still point here)
      - 'FOREIGN KEY constraint' = an INSERT/UPDATE naming a row that does not exist

    2601 is the unique-INDEX flavor and 2627 the unique-CONSTRAINT flavor — both
    must be caught (iOS's UX_TimeLog is an index).

    Error numbers are matched only in parenthesized form (see _has_error_number).
    That check is deliberately conservative: a message that carries the number in
    some other format falls through to the existing keyword classification, which
    is a coverage trade-off, never a new failure mode.
    """
    lower = text.lower()
    # Plain `if` (not `elif`) throughout: a (547) carrying neither FK phrase — a
    # CHECK-constraint violation — must fall through unclassified, not short-circuit.
    if _has_error_number(text, '547'):
        if 'reference constraint' in lower:
            return FK_REFERENCE_VIOLATION
        if 'foreign key constraint' in lower:
            return FK_MISSING_VIOLATION
    if (
        _has_error_number(text, '2627') or _has_error_number(text, '2601')
    ) and ('duplicate key' in lower or 'unique' in lower):
        return UNIQUE_VIOLATION
    return None


def status_for_clean_message(text: str) -> int | None:
    """Reverse lookup: if *text* is exactly one of the three clean messages,
    return its http_status, else None.

    The workflow path (ProcessEngine) stringifies the exception, so only the
    message survives to the router.
    """
    return _STATUS_BY_CLEAN_MESSAGE.get(text)
