# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports
from entities.integration.business.model import IntegrationStatus


class IntegrationCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the integration."
    )
    status: Optional[IntegrationStatus] = Field(
        default=IntegrationStatus.DISCONNECTED,
        description="The status of the integration. Defaults to DISCONNECTED."
    )


class IntegrationUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the integration (base64 encoded)."
    )
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the integration."
    )
    status: IntegrationStatus = Field(
        description="The status of the integration."
    )
