# Python Standard Library Imports
import logging
import threading
import time
from typing import List, Optional

# Local Imports
from entities.classification_override.business.model import ClassificationOverride
from entities.classification_override.persistence.repo import ClassificationOverrideRepository

logger = logging.getLogger(__name__)

# Cache TTL in seconds
_CACHE_TTL = 300  # 5 minutes


class ClassificationOverrideService:
    """
    Service for classification override business operations.

    Includes a simple in-memory cache for find_override() lookups since
    overrides change infrequently but are checked on every classification.
    """

    def __init__(self, repo: Optional[ClassificationOverrideRepository] = None):
        self.repo = repo or ClassificationOverrideRepository()
        self._cache: dict[str, Optional[ClassificationOverride]] = {}
        self._cache_time: float = 0.0
        self._lock = threading.Lock()

    def _invalidate_cache(self):
        """Clear the lookup cache after a write operation."""
        with self._lock:
            self._cache.clear()
            self._cache_time = 0.0

    def create(
        self,
        *,
        match_type: str,
        match_value: str,
        classification_type: str,
        notes: Optional[str] = None,
        is_active: bool = True,
        created_by: Optional[str] = None,
    ) -> ClassificationOverride:
        """Create a new classification override."""
        if match_type not in ("email", "domain"):
            raise ValueError("match_type must be 'email' or 'domain'.")
        if not match_value:
            raise ValueError("match_value is required.")
        if not classification_type:
            raise ValueError("classification_type is required.")

        result = self.repo.create(
            match_type=match_type,
            match_value=match_value.lower().strip(),
            classification_type=classification_type,
            notes=notes,
            is_active=is_active,
            created_by=created_by,
        )
        self._invalidate_cache()
        return result

    def read_all(self) -> List[ClassificationOverride]:
        """Read all classification overrides."""
        return self.repo.read_all()

    def read_by_public_id(self, public_id: str) -> Optional[ClassificationOverride]:
        """Read a single override by public ID."""
        return self.repo.read_by_public_id(public_id)

    def update(
        self,
        *,
        public_id: str,
        row_version: str,
        match_type: str,
        match_value: str,
        classification_type: str,
        notes: Optional[str] = None,
        is_active: bool = True,
    ) -> Optional[ClassificationOverride]:
        """Update an existing override."""
        result = self.repo.update(
            public_id=public_id,
            row_version=row_version,
            match_type=match_type,
            match_value=match_value.lower().strip(),
            classification_type=classification_type,
            notes=notes,
            is_active=is_active,
        )
        self._invalidate_cache()
        return result

    def delete(self, public_id: str) -> bool:
        """Delete an override by public ID."""
        result = self.repo.delete_by_public_id(public_id)
        self._invalidate_cache()
        return result

    def find_override(self, email: str) -> Optional[ClassificationOverride]:
        """
        Find an active override for the given email address.

        Results are cached in-memory for up to 5 minutes to avoid a DB call
        on every email classification.  Cache is invalidated on any write.
        """
        if not email:
            return None

        email_lower = email.lower().strip()

        with self._lock:
            now = time.monotonic()
            # Expire full cache after TTL
            if now - self._cache_time > _CACHE_TTL:
                self._cache.clear()
                self._cache_time = now

            if email_lower in self._cache:
                return self._cache[email_lower]

        # Cache miss — hit DB
        override = self.repo.find_by_email(email_lower)

        with self._lock:
            self._cache[email_lower] = override

        return override
