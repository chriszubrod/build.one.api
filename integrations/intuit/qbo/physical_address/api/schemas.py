# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboPhysicalAddressCreate(BaseModel):
    qbo_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The QBO ID of the physical address.",
    )
    line1: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The first line of the physical address.",
    )
    line2: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The second line of the physical address.",
    )
    city: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The city of the physical address.",
    )
    country: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The country of the physical address.",
    )
    country_sub_division_code: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The state/province code of the physical address.",
    )
    postal_code: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The postal code of the physical address.",
    )


class QboPhysicalAddressUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the physical address (base64 encoded).",
    )
    qbo_id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The ID of the QBO physical address.",
    )
    line1: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The first line of the physical address.",
    )
    line2: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The second line of the physical address.",
    )
    city: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The city of the physical address.",
    )
    country: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The country of the physical address.",
    )
    country_sub_division_code: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The state/province code of the physical address.",
    )
    postal_code: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The postal code of the physical address.",
    )


# QboPhysicalAddressSyncRequest DELETED with the /sync route it served (U-519
# fix round 2). Its `address_id` was a caller-controlled LOCAL upsert key over an
# unscoped lookup, not a remote selector; see the note in api/router.py. Its
# `access_token` was separately a required field the service documents as ignored
# ("QboHttpClient resolves and refreshes the token lazily"), i.e. an OpenAPI
# contract instructing callers to put a live OAuth bearer token in a request body
# for no reason — that surface goes away with the model.
