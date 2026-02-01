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
