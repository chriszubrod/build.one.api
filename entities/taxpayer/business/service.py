# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.taxpayer.business.model import Taxpayer, TaxpayerClassification
from entities.taxpayer.persistence.repo import TaxpayerRepository
from shared.encryption import encrypt_sensitive_data, decrypt_sensitive_data



class TaxpayerService:
    """
    Service for Taxpayer entity business operations.
    """

    def __init__(self, repo: Optional[TaxpayerRepository] = None):
        """Initialize the TaxpayerService."""
        self.repo = repo or TaxpayerRepository()

    def create(self, *, tenant_id: int = None, entity_name: Optional[str], business_name: Optional[str], classification: Optional[str] = None, taxpayer_id_number: Optional[str] = None) -> Taxpayer:
        """
        Create a new taxpayer.
        """
        # Check for duplicate EntityName
        if entity_name:
            existing = self.read_by_name(entity_name=entity_name)
            if existing:
                raise ValueError(f"Taxpayer with entity name '{entity_name}' already exists.")
        
        # Check for duplicate BusinessName
        if business_name:
            existing = self.read_by_business_name(business_name=business_name)
            if existing:
                raise ValueError(f"Taxpayer with business name '{business_name}' already exists.")
        
        # Check for duplicate TaxpayerIdNumber (encrypt before checking)
        if taxpayer_id_number:
            existing = self.read_by_taxpayer_id_number(taxpayer_id_number=taxpayer_id_number)
            if existing:
                raise ValueError(f"Taxpayer with taxpayer ID number already exists.")
        
        # TODO: In Phase 10, use tenant_id for tenant isolation
        encrypted_taxpayer_id_number = encrypt_sensitive_data(taxpayer_id_number) if taxpayer_id_number else None
        taxpayer = self.repo.create(entity_name=entity_name, business_name=business_name, classification=classification, taxpayer_id_number=encrypted_taxpayer_id_number)
        # Decrypt for return value
        if taxpayer and taxpayer.taxpayer_id_number:
            taxpayer.taxpayer_id_number = decrypt_sensitive_data(taxpayer.taxpayer_id_number)
        return taxpayer

    def read_all(self) -> list[Taxpayer]:
        """
        Read all taxpayers.
        """
        taxpayers = self.repo.read_all()
        for taxpayer in taxpayers:
            if taxpayer and taxpayer.taxpayer_id_number:
                taxpayer.taxpayer_id_number = decrypt_sensitive_data(taxpayer.taxpayer_id_number)
        return taxpayers

    def read_by_id(self, id: int) -> Optional[Taxpayer]:
        """
        Read a taxpayer by ID.
        """
        taxpayer = self.repo.read_by_id(id)
        if taxpayer and taxpayer.taxpayer_id_number:
            taxpayer.taxpayer_id_number = decrypt_sensitive_data(taxpayer.taxpayer_id_number)
        return taxpayer

    def read_by_public_id(self, public_id: str) -> Optional[Taxpayer]:
        """
        Read a taxpayer by public ID.
        """
        taxpayer = self.repo.read_by_public_id(public_id)
        if taxpayer and taxpayer.taxpayer_id_number:
            taxpayer.taxpayer_id_number = decrypt_sensitive_data(taxpayer.taxpayer_id_number)
        return taxpayer

    def read_by_name(self, entity_name: str) -> Optional[Taxpayer]:
        """
        Read a taxpayer by entity name.
        """
        taxpayer = self.repo.read_by_name(entity_name)
        if taxpayer and taxpayer.taxpayer_id_number:
            taxpayer.taxpayer_id_number = decrypt_sensitive_data(taxpayer.taxpayer_id_number)
        return taxpayer

    def read_by_business_name(self, business_name: str) -> Optional[Taxpayer]:
        """
        Read a taxpayer by business name.
        """
        taxpayer = self.repo.read_by_business_name(business_name)
        if taxpayer and taxpayer.taxpayer_id_number:
            taxpayer.taxpayer_id_number = decrypt_sensitive_data(taxpayer.taxpayer_id_number)
        return taxpayer

    def read_by_taxpayer_id_number(self, taxpayer_id_number: str) -> Optional[Taxpayer]:
        """
        Read a taxpayer by taxpayer ID number (plain text - will be encrypted for lookup).
        """
        encrypted_id = encrypt_sensitive_data(taxpayer_id_number) if taxpayer_id_number else None
        if not encrypted_id:
            return None
        taxpayer = self.repo.read_by_taxpayer_id_number(encrypted_id)
        if taxpayer and taxpayer.taxpayer_id_number:
            taxpayer.taxpayer_id_number = decrypt_sensitive_data(taxpayer.taxpayer_id_number)
        return taxpayer

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        entity_name: str = None,
        business_name: str = None,
        classification: str = None,
        taxpayer_id_number: str = None,
    ) -> Optional[Taxpayer]:
        """
        Update a taxpayer by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            # Check for duplicate EntityName if it's being changed
            if entity_name is not None and entity_name != existing.entity_name:
                duplicate = self.read_by_name(entity_name=entity_name)
                if duplicate and duplicate.public_id != public_id:
                    raise ValueError(f"Taxpayer with entity name '{entity_name}' already exists.")
            
            # Check for duplicate BusinessName if it's being changed
            if business_name is not None and business_name != existing.business_name:
                duplicate = self.read_by_business_name(business_name=business_name)
                if duplicate and duplicate.public_id != public_id:
                    raise ValueError(f"Taxpayer with business name '{business_name}' already exists.")
            
            # Check for duplicate TaxpayerIdNumber if it's being changed
            if taxpayer_id_number is not None:
                # existing.taxpayer_id_number is already decrypted from read_by_public_id
                if taxpayer_id_number != existing.taxpayer_id_number:
                    duplicate = self.read_by_taxpayer_id_number(taxpayer_id_number=taxpayer_id_number)
                    if duplicate and duplicate.public_id != public_id:
                        raise ValueError(f"Taxpayer with taxpayer ID number already exists.")
            
            existing.row_version = row_version
            if entity_name is not None:
                existing.entity_name = entity_name
            if business_name is not None:
                existing.business_name = business_name
            if classification is not None:
                existing.classification = classification
            if taxpayer_id_number is not None:
                encrypted_id = encrypt_sensitive_data(taxpayer_id_number)
                existing.taxpayer_id_number = encrypted_id
            updated = self.repo.update_by_id(existing)
            # Decrypt for return value
            if updated and updated.taxpayer_id_number:
                updated.taxpayer_id_number = decrypt_sensitive_data(updated.taxpayer_id_number)
            return updated
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Taxpayer]:
        """
        Delete a taxpayer by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
