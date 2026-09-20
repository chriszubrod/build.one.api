# Python Standard Library Imports
from typing import Optional

# Local Imports
from entities.asset.business.service import AssetService
from entities.asset_attachment.business.model import AssetAttachment
from entities.asset_attachment.persistence.repo import AssetAttachmentRepository
from entities.attachment.business.service import AttachmentService
from shared.access import EntityNotAccessibleError


class AssetAttachmentService:
    def __init__(self, repo: Optional[AssetAttachmentRepository] = None):
        self.repo = repo or AssetAttachmentRepository()

    def _assert_parent_asset_access(self, link: AssetAttachment) -> None:
        if not link.asset_id:
            raise EntityNotAccessibleError("AssetAttachment", link.id or 0)
        asset = AssetService().repo.read_by_id(int(link.asset_id))
        if not asset:
            raise EntityNotAccessibleError("AssetAttachment", link.id or 0)
        AssetService()._assert_company_access(asset)

    def create(
        self,
        *,
        tenant_id: int = None,
        asset_public_id: str,
        attachment_public_id: str,
    ) -> AssetAttachment:
        asset = AssetService().read_by_public_id(public_id=asset_public_id)
        attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)

        if not asset or not asset.id:
            raise ValueError(f"Asset with public_id '{asset_public_id}' not found")
        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")

        asset_id = int(asset.id)
        attachment_id = int(attachment.id)

        for existing in self.repo.read_by_asset_id(asset_id):
            if existing.attachment_id and int(existing.attachment_id) == attachment_id:
                return existing

        return self.repo.create(asset_id=asset_id, attachment_id=attachment_id)

    def read_by_asset_public_id(self, asset_public_id: str) -> list[AssetAttachment]:
        asset = AssetService().read_by_public_id(public_id=asset_public_id)
        if not asset or not asset.id:
            return []
        return self.repo.read_by_asset_id(int(asset.id))

    def read_by_public_id(self, public_id: str) -> Optional[AssetAttachment]:
        link = self.repo.read_by_public_id(public_id)
        if link:
            self._assert_parent_asset_access(link)
        return link

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[AssetAttachment]:
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return None
        self._assert_parent_asset_access(existing)
        if existing.id:
            return self.repo.delete_by_id(int(existing.id))
        return None
