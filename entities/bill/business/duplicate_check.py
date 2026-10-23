"""Bill possible-duplicate detection (U-723 Rules A/B/C)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Literal, Optional, Sequence

import config
from entities.attachment.business.text_fingerprint import FINGERPRINT_VERSION
from entities.bill.persistence.repo import BillRepository
from shared.api.errors import ApiError, ErrorCode
from shared.authz import current_is_system_admin, current_user_id

DuplicateCheckMode = Literal["off", "warn", "record"]
DuplicateStage = Literal["create", "submit", "complete"]

@dataclass(frozen=True)
class DuplicateMatch:
    public_id: str
    bill_id: Optional[int]
    bill_number: Optional[str]
    bill_date: Optional[str]
    total_amount: Optional[str]
    status: Optional[str]
    vendor_name: Optional[str]
    project_names: list[str]
    created_datetime: Any
    reasons: list[str]


@dataclass
class DuplicateCheckResult:
    visible_matches: list[DuplicateMatch] = field(default_factory=list)
    hidden_match_count: int = 0

    @property
    def has_matches(self) -> bool:
        return bool(self.visible_matches) or self.hidden_match_count > 0

    def to_api_matches(self) -> list[dict[str, Any]]:
        return [
            {
                "public_id": m.public_id,
                "bill_number": m.bill_number,
                "bill_date": m.bill_date,
                "total_amount": m.total_amount,
                "status": m.status,
                "vendor_name": m.vendor_name,
                "project_names": m.project_names,
                "created_datetime": m.created_datetime.isoformat()
                if hasattr(m.created_datetime, "isoformat")
                else m.created_datetime,
                "reasons": m.reasons,
            }
            for m in self.visible_matches
        ]


class PossibleDuplicateRefused(ApiError):
    """422 when warn-mode duplicate check is not confirmed."""

    def __init__(
        self,
        message: str,
        *,
        duplicate_matches: list[dict[str, Any]],
        hidden_match_count: int,
    ) -> None:
        super().__init__(
            status_code=422,
            detail=message,
            error_code=ErrorCode.POSSIBLE_DUPLICATE,
            extra={
                "duplicate_matches": duplicate_matches,
                "hidden_match_count": hidden_match_count,
            },
        )


def effective_duplicate_amount(
    *,
    header_total: Optional[Decimal],
    line_amounts: Iterable[Optional[Decimal]],
) -> Optional[Decimal]:
    if header_total is not None and header_total != 0:
        return header_total
    parts = [a for a in line_amounts if a is not None]
    if not parts:
        return None
    total = sum(parts, Decimal("0"))
    if total == 0:
        return None
    return total


def global_bill_duplicate_warn_mode() -> DuplicateCheckMode:
    raw = (config.Settings().bill_duplicate_warn or "record").strip().lower()
    if raw in ("off", "warn", "record"):
        return raw  # type: ignore[return-value]
    return "record"


def effective_mode_for_path(
    *,
    path: Literal["human_create", "human_submit", "human_complete", "record_path"],
    explicit: DuplicateCheckMode = "off",
) -> DuplicateCheckMode:
    # The global BILL_DUPLICATE_WARN=off is a kill switch: it wins over any
    # explicit per-path mode, so "off" means no duplicate reads anywhere.
    global_mode = global_bill_duplicate_warn_mode()
    if global_mode == "off":
        return "off"
    if explicit != "off":
        return explicit
    if global_mode == "record":
        return "record"
    if path == "record_path":
        return "record"
    return "warn"


def is_agent_username(username: Optional[str]) -> bool:
    return bool(username and username.endswith("_agent"))


def confirmation_covers_matches(
    *,
    confirm_not_duplicate: bool,
    confirmed_public_ids: Sequence[str],
    visible_matches: Sequence[DuplicateMatch],
) -> bool:
    if not confirm_not_duplicate:
        return False
    needed = {str(m.public_id).lower() for m in visible_matches}
    if not needed:
        return True
    got = {str(pid).lower() for pid in confirmed_public_ids}
    return needed.issubset(got)


def run_possible_duplicate_check(
    *,
    vendor_id: Optional[int],
    bill_date: Optional[str],
    total_amount: Optional[Decimal],
    bill_number: Optional[str],
    text_fingerprints: Sequence[str],
    exclude_bill_id: Optional[int],
    project_ids: Sequence[int],
    repo: Optional[BillRepository] = None,
) -> DuplicateCheckResult:
    bill_repo = repo or BillRepository()
    rows = bill_repo.read_possible_duplicate_bills(
        vendor_id=vendor_id,
        bill_date=bill_date,
        total_amount=total_amount,
        bill_number=bill_number,
        text_fingerprints=list(text_fingerprints),
        fingerprint_version=FINGERPRINT_VERSION,
        exclude_bill_id=exclude_bill_id,
        project_ids=list(project_ids),
        actor_user_id=current_user_id.get(),
        actor_is_system_admin=bool(current_is_system_admin.get()),
    )
    visible: list[DuplicateMatch] = []
    hidden = 0
    for row in rows:
        can_access = bool(getattr(row, "CallerCanAccess", False))
        reasons_raw = getattr(row, "ReasonsCsv", "") or ""
        reasons = [r.strip() for r in reasons_raw.split(",") if r.strip()]
        project_csv = getattr(row, "ProjectNamesCsv", None)
        project_names = (
            [p.strip() for p in str(project_csv).split(",") if p.strip()]
            if project_csv
            else []
        )
        amount = getattr(row, "TotalAmount", None)
        amount_str = f"{Decimal(str(amount)):.2f}" if amount is not None else None
        bill_id_raw = getattr(row, "BillId", None)
        match = DuplicateMatch(
            public_id=str(getattr(row, "PublicId")),
            bill_id=int(bill_id_raw) if bill_id_raw is not None else None,
            bill_number=getattr(row, "BillNumber", None),
            bill_date=getattr(row, "BillDate", None),
            total_amount=amount_str,
            status=getattr(row, "Status", None),
            vendor_name=getattr(row, "VendorName", None),
            project_names=project_names,
            created_datetime=getattr(row, "CreatedDatetime", None),
            reasons=reasons,
        )
        if can_access:
            visible.append(match)
        else:
            hidden += 1
    return DuplicateCheckResult(visible_matches=visible, hidden_match_count=hidden)


def assert_duplicate_check_passes_or_raise(
    *,
    mode: DuplicateCheckMode,
    result: DuplicateCheckResult,
    confirm_not_duplicate: bool,
    confirmed_duplicate_public_ids: Sequence[str],
    agent_caller: bool,
) -> None:
    if mode == "off" or not result.has_matches:
        return
    if mode == "record":
        return
    if agent_caller:
        raise PossibleDuplicateRefused(
            "Possible duplicate bill: agent callers cannot confirm duplicates.",
            duplicate_matches=result.to_api_matches(),
            hidden_match_count=result.hidden_match_count,
        )
    if confirmation_covers_matches(
        confirm_not_duplicate=confirm_not_duplicate,
        confirmed_public_ids=confirmed_duplicate_public_ids,
        visible_matches=result.visible_matches,
    ):
        return
    raise PossibleDuplicateRefused(
        "Possible duplicate bill: review matches and confirm to continue.",
        duplicate_matches=result.to_api_matches(),
        hidden_match_count=result.hidden_match_count,
    )


def persist_duplicate_overrides(
    *,
    bill_id: int,
    matches: Sequence[DuplicateMatch],
    stage: DuplicateStage,
    disposition: Literal["confirmed_not_duplicate", "recorded"],
    actor_user_id: Optional[int],
    repo: Optional[BillRepository] = None,
) -> None:
    if not matches or actor_user_id is None:
        return
    bill_repo = repo or BillRepository()
    for match in matches:
        primary_reason = match.reasons[0] if match.reasons else "vendor_date_amount"
        bill_repo.create_bill_duplicate_override(
            bill_id=bill_id,
            matched_bill_id=match.bill_id,
            match_reason=primary_reason,
            stage=stage,
            disposition=disposition,
            actor_user_id=actor_user_id,
        )


def record_duplicate_audit(
    *,
    bill_id: int,
    result: DuplicateCheckResult,
    mode: DuplicateCheckMode,
    confirm_not_duplicate: bool,
    stage: DuplicateStage,
    actor_user_id: Optional[int],
) -> Optional[list[dict[str, Any]]]:
    if mode == "off" or not result.has_matches or actor_user_id is None:
        return None
    disposition = (
        "confirmed_not_duplicate"
        if mode == "warn" and confirm_not_duplicate
        else "recorded"
    )
    persist_duplicate_overrides(
        bill_id=bill_id,
        matches=result.visible_matches,
        stage=stage,
        disposition=disposition,
        actor_user_id=actor_user_id,
    )
    if mode == "record":
        return result.to_api_matches()
    return None
