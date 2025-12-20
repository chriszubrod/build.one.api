# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboAuthCreate(BaseModel):
    code: str = Field(
        ...,
        max_length=512,
        description="Code issued by Intuit.",
    )
    realm_id: str = Field(
        ...,
        max_length=512,
        description="Realm ID issued by Intuit.",
    )
    state: str = Field(
        ...,
        max_length=512,
        description="State issued by Intuit.",
    )
    token_type: str = Field(
        ...,
        max_length=512,
        description="Token type issued by Intuit.",
    )
    id_token: str = Field(
        ...,
        max_length=512,
        description="ID token issued by Intuit.",
    )
    access_token: str = Field(
        ...,
        max_length=512,
        description="Access token issued by Intuit.",
    )
    expires_in: int = Field(
        ...,
        description="Expires in issued by Intuit.",
    )

    refresh_token: str = Field(
        ...,
        max_length=512,
        description="Refresh token issued by Intuit.",
    )
    x_refresh_token_expires_in: int = Field(
        ...,
        description="X refresh token expires in issued by Intuit.",
    )


class QboAuthUpdate(BaseModel):
    code: str = Field(
        ...,
        max_length=512,
        description="Code issued by Intuit.",
    )
    realm_id: str = Field(
        ...,
        max_length=512,
        description="Realm ID issued by Intuit.",
    )
    state: str = Field(
        ...,
        max_length=512,
        description="State issued by Intuit.",
    )
    token_type: str = Field(
        ...,
        max_length=512,
        description="Token type issued by Intuit.",
    )
    id_token: str = Field(
        ...,
        max_length=512,
        description="ID token issued by Intuit.",
    )
    access_token: str = Field(
        ...,
        max_length=512,
        description="Access token issued by Intuit.",
    )
    expires_in: int = Field(
        ...,
        description="Expires in issued by Intuit.",
    )
    refresh_token: str = Field(
        ...,
        max_length=512,
        description="Refresh token issued by Intuit.",
    )
    x_refresh_token_expires_in: int = Field(
        ...,
        description="X refresh token expires in issued by Intuit.",
    )
