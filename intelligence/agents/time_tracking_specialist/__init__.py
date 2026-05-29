"""Time Tracking specialist agent — auto-reviews iOS-submitted TimeEntries.

Invoked by the scheduler-driven `/admin/time-tracking/process_one` endpoint
(Phase 7) when a row appears in `dbo.TimeTrackingOutbox`. The specialist:

  1. Reads the TimeEntry via `validate_time_entry_completeness`, which
     runs a deterministic anomaly checklist (NULL project, overnight
     shift, >12h, <0.25h, missing clockout, future-dated, gps-no-project,
     no-logs).
  2. Maps the returned reason codes to a ReviewPriority bucket
     (`high` / `medium` / `low` / `clean`) per the rule in the tool's
     description.
  3. Stamps ReviewPriority + ReviewReasons via
     `flag_time_entry_for_human_review`. NO status transition — the
     human Approver still drives approve/reject. Flag-only v1.

Carries grants ONLY on Time Tracking (CRU), Projects (R), and Users (R).
Auth via `time_tracking_agent_username` + `time_tracking_agent_password`.

Importing this package triggers tool + agent registration.
"""
# Entity tools — self-register on import.
import entities.time_entry.intelligence.tools  # noqa: F401

from intelligence.agents.time_tracking_specialist.definition import (  # noqa: F401
    time_tracking_specialist,
)
