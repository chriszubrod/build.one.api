from pydantic import BaseModel, Field


class AssetAttachmentCreate(BaseModel):
    asset_public_id: str = Field(min_length=1)
    attachment_public_id: str = Field(min_length=1)
