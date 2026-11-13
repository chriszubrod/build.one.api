# Python Standard Library Imports
import base64
from dataclasses import asdict, dataclass
from typing import Optional

# The house defaults for the optional Application for Payment blocks (U-792). A
# project with no settings row, or a NULL switch, follows these — so a later change
# here reaches every project that never chose otherwise.
DEFAULT_SHOW_CONTRACTOR_STATEMENT = True
DEFAULT_SHOW_OWNER_APPROVAL = True
DEFAULT_SHOW_ARCHITECT_CERTIFICATE = False
DEFAULT_SHOW_NOTARY = True


@dataclass(frozen=True)
class DrawPacketBlocks:
    """The resolved on/off state of each optional Application for Payment block."""

    contractor_statement: bool = DEFAULT_SHOW_CONTRACTOR_STATEMENT
    owner_approval: bool = DEFAULT_SHOW_OWNER_APPROVAL
    architect_certificate: bool = DEFAULT_SHOW_ARCHITECT_CERTIFICATE
    notary: bool = DEFAULT_SHOW_NOTARY

    def to_dict(self) -> dict:
        return asdict(self)


def _pick(stored: Optional[bool], default: bool) -> bool:
    # `is None`, never truthiness: a stored False must win over a True default.
    return default if stored is None else bool(stored)


@dataclass
class ProjectDrawPacketSetting:
    """One project's stored switches. Each is tri-state: True / False / None (= default)."""

    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    created_by_user_id: Optional[int] = None
    project_id: Optional[int] = None
    show_contractor_statement: Optional[bool] = None
    show_owner_approval: Optional[bool] = None
    show_architect_certificate: Optional[bool] = None
    show_notary: Optional[bool] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def blocks(self) -> DrawPacketBlocks:
        return DrawPacketBlocks(
            contractor_statement=_pick(self.show_contractor_statement, DEFAULT_SHOW_CONTRACTOR_STATEMENT),
            owner_approval=_pick(self.show_owner_approval, DEFAULT_SHOW_OWNER_APPROVAL),
            architect_certificate=_pick(self.show_architect_certificate, DEFAULT_SHOW_ARCHITECT_CERTIFICATE),
            notary=_pick(self.show_notary, DEFAULT_SHOW_NOTARY),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_blocks(setting: Optional[ProjectDrawPacketSetting]) -> DrawPacketBlocks:
    """The effective switches: the stored row's, or the house defaults when none."""
    return setting.blocks() if setting else DrawPacketBlocks()
