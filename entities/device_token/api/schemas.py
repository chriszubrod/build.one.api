# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class RegisterDeviceTokenRequest(BaseModel):
    device_token: str = Field(..., min_length=1, max_length=255)
    app_bundle_id: str = Field(..., min_length=1, max_length=255)


class DeactivateDeviceTokenRequest(BaseModel):
    device_token: str = Field(..., min_length=1, max_length=255)
