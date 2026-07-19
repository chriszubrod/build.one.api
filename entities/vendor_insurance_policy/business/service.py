# Python Standard Library Imports
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Third-party Imports

# Local Imports
from entities.vendor_compliance_document.business.service import VendorComplianceDocumentService
from entities.vendor_insurance_policy.business.model import VendorInsurancePolicy
from entities.vendor_insurance_policy.persistence.repo import VendorInsurancePolicyRepository
from shared.authz import current_user_id


class VendorInsurancePolicyService:
    """
    Service for VendorInsurancePolicy entity business operations.
    """

    def __init__(self, repo: Optional[VendorInsurancePolicyRepository] = None):
        """Initialize the VendorInsurancePolicyService."""
        self.repo = repo or VendorInsurancePolicyRepository()

    @staticmethod
    def _coerce_decimal(value: Union[str, Decimal, int, float, None]) -> Optional[Decimal]:
        """Decimal(str(...)) coerce per memory's financial-precision rule."""
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Invalid decimal value: {value!r}") from e

    def create(
        self,
        *,
        tenant_id: int = 1,
        compliance_document_public_id: str,
        coverage_type: str,
        carrier: Optional[str] = None,
        policy_number: Optional[str] = None,
        each_occurrence=None,
        aggregate=None,
        effective_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
    ) -> VendorInsurancePolicy:
        """
        Create a new vendor insurance policy.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        parent = VendorComplianceDocumentService().read_by_public_id(public_id=compliance_document_public_id)
        if not parent or not parent.id:
            raise ValueError(f"Compliance document with public_id '{compliance_document_public_id}' not found")
        if parent.document_type != "CERTIFICATE_OF_INSURANCE":
            raise ValueError("Insurance policies can only be added to a Certificate of Insurance")

        return self.repo.create(
            vendor_compliance_document_id=int(parent.id),
            coverage_type=coverage_type,
            carrier=carrier,
            policy_number=policy_number,
            each_occurrence=self._coerce_decimal(each_occurrence),
            aggregate=self._coerce_decimal(aggregate),
            effective_date=effective_date,
            expiry_date=expiry_date,
            created_by_user_id=current_user_id.get(),
        )

    def read_by_id(self, id: str) -> Optional[VendorInsurancePolicy]:
        """
        Read a vendor insurance policy by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[VendorInsurancePolicy]:
        """
        Read a vendor insurance policy by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_compliance_document_public_id(self, doc_public_id: str) -> list[VendorInsurancePolicy]:
        """
        Read vendor insurance policies by compliance document public ID.
        """
        parent = VendorComplianceDocumentService().read_by_public_id(public_id=doc_public_id)
        if not parent or not parent.id:
            raise ValueError(f"Compliance document with public_id '{doc_public_id}' not found")
        return self.repo.read_by_compliance_document_id(int(parent.id))

    def read_by_compliance_document_id(self, compliance_document_id: int):
        return self.repo.read_by_compliance_document_id(compliance_document_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = 1,
        row_version: str,
        coverage_type: Optional[str] = None,
        carrier: Optional[str] = None,
        policy_number: Optional[str] = None,
        each_occurrence=None,
        aggregate=None,
        effective_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
    ) -> Optional[VendorInsurancePolicy]:
        """
        Update a vendor insurance policy by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = row_version
        if coverage_type is not None:
            existing.coverage_type = coverage_type
        if carrier is not None:
            existing.carrier = carrier
        if policy_number is not None:
            existing.policy_number = policy_number
        if each_occurrence is not None:
            existing.each_occurrence = self._coerce_decimal(each_occurrence)
        if aggregate is not None:
            existing.aggregate = self._coerce_decimal(aggregate)
        if effective_date is not None:
            existing.effective_date = effective_date
        if expiry_date is not None:
            existing.expiry_date = expiry_date

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = 1) -> Optional[VendorInsurancePolicy]:
        """
        Delete a vendor insurance policy by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            if self.repo.delete_by_id(int(existing.id)):
                return existing
        return None
