# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports
from entities.taxpayer.business.model import TaxpayerClassification


class TaxpayerCreate(BaseModel):
    entity_name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The entity name of the taxpayer.",
    )
    business_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The business name of the taxpayer.",
    )
    classification: Optional[TaxpayerClassification] = Field(
        default=None,
        description="The classification of the taxpayer.",
    )
    taxpayer_id_number: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The taxpayer ID number of the taxpayer.",
    )
    is_signed: Optional[int] = Field(
        default=0,
        description="Whether the taxpayer form is signed (0 or 1).",
    )
    signature_date: Optional[str] = Field(
        default=None,
        description="ISO datetime when the taxpayer was signed.",
    )


class TaxpayerUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the taxpayer (base64 encoded).",
    )
    entity_name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The entity name of the taxpayer.",
    )
    business_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The business name of the taxpayer.",
    )
    classification: Optional[TaxpayerClassification] = Field(
        default=None,
        description="The classification of the taxpayer.",
    )
    taxpayer_id_number: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The taxpayer ID number of the taxpayer.",
    )
    is_signed: Optional[int] = Field(
        default=None,
        description="Whether the taxpayer form is signed (0 or 1).",
    )
    signature_date: Optional[str] = Field(
        default=None,
        description="ISO datetime when the taxpayer was signed.",
    )
