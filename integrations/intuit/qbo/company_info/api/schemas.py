# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboCompanyInfoSync(BaseModel):
    realm_id: str = Field(
        description="QBO company realm ID.",
    )

