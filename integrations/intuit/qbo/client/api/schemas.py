# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboClientCreate(BaseModel):
    app: str = Field(
        ...,
        max_length=512,
        description="App name.",
    )
    client_id: str = Field(
        ...,
        max_length=512,
        description="Client ID issued by Intuit.",
    )
    client_secret: str = Field(
        ...,
        max_length=512,
        description="Client secret issued by Intuit.",
    )


class QboClientUpdate(BaseModel):
    app: str = Field(
        ...,
        max_length=512,
        description="App name.",
    )
    client_id: str = Field(
        ...,
        max_length=512,
        description="Client ID issued by Intuit.",
    )
    client_secret: str = Field(
        ...,
        max_length=512,
        description="Client secret issued by Intuit.",
    )
