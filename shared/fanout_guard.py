"""
Emergency operator kill switch for fan-out idempotency guards.

Fan-out surfaces (Box push, SharePoint enqueue) skip re-upload when identity
fields match a prior successful push. The asymmetry: a wrong upload costs one
PUT; a wrong skip loses a document forever, so this switch exists to force the
safe direction globally when a guard is suspected of wrongly skipping.

Set DISABLE_FANOUT_IDEMPOTENCY_GUARDS=true, restart, re-run the completion
or let the next pull tick re-push, then unset.
"""
import os


def idempotency_guards_disabled() -> bool:
    return os.getenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", "").strip().lower() == "true"


def same_attachment_id(a, b) -> bool:
    """Three-state identity: both None matches; exactly one None never matches;
    otherwise int()-compare. May raise on non-numeric input — callers deliberately
    run this inside their guard's try so a raise falls through to UPLOAD."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return int(a) == int(b)
