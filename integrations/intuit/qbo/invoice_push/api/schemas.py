"""U-778d1 b2 -- request bodies for the invoice-push dry-run / compare endpoints."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from integrations.intuit.qbo.invoice_push.business.model import FeeLine, Proposal


# A dbo bigint identity: out of range would otherwise surface as a DB overflow 500 instead of a clean 422.
MAX_BIGINT = 2 ** 63 - 1
BigId = Annotated[int, Field(ge=1, le=MAX_BIGINT)]


class SourceLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: str = Field(max_length=32)
    source_line_id: BigId


class FeeLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sub_cost_code_id: BigId
    description: str = Field(max_length=1000)
    qty: Decimal
    unit_price: Decimal


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: BigId
    doc_number: str = Field(max_length=100)
    txn_date: date  # validated ISO calendar date, serialised YYYY-MM-DD
    due_date: date  # required
    payment_term_id: Optional[str] = Field(default=None, max_length=50)
    source_lines: List[SourceLineIn] = Field(default_factory=list, max_length=300)
    fee_lines: List[FeeLineIn] = Field(default_factory=list, max_length=50)

    @field_validator("payment_term_id")
    @classmethod
    def _payment_term_in_bigint_range(cls, v):
        """A numeric term id must be a valid bigint; a non-numeric one stays "unmapped" (the resolver refuses it)."""
        try:
            n = int(v) if v is not None else None
        except ValueError:
            return v
        if n is not None and not 1 <= n <= MAX_BIGINT:
            raise ValueError(f"payment_term_id must be between 1 and {MAX_BIGINT}")
        return v

    def to_proposal(self) -> Proposal:
        return Proposal(
            project_id=self.project_id, doc_number=self.doc_number, txn_date=self.txn_date.isoformat(),
            due_date=self.due_date.isoformat(), payment_term_id=self.payment_term_id,
            source_lines=tuple((s.source_type, s.source_line_id) for s in self.source_lines),
            fee_lines=tuple(FeeLine(f.sub_cost_code_id, f.description, f.qty, f.unit_price) for f in self.fee_lines),
        )


class CompareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal: ProposalIn
    qbo_invoice_id: str = Field(pattern=r"^[0-9]{1,50}$")
