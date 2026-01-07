# Python Standard Library Imports
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.auth.business.model import QboAuth
from integrations.intuit.qbo.auth.persistence.repo import QboAuthRepository
from integrations.intuit.qbo.auth.external.client import connect_intuit_oauth_2_token_endpoint_refresh

logger = logging.getLogger(__name__)


class QboAuthService:
    """
    Service for QboAuth entity business operations.
    """

    def __init__(self, repo: Optional[QboAuthRepository] = None):
        """Initialize the QboAuthService."""
        self.repo = repo or QboAuthRepository()

    def create(self, *, code: str, realm_id: str, state: str, token_type: str, id_token: str, access_token: str, expires_in: int, refresh_token: str, x_refresh_token_expires_in: int) -> QboAuth:
        """
        Create a new QboAuth.
        """
        return self.repo.create(
            code=code,
            realm_id=realm_id,
            state=state,
            token_type=token_type,
            id_token=id_token,
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=refresh_token,
            x_refresh_token_expires_in=x_refresh_token_expires_in,
        )

    def read_all(self) -> list[QboAuth]:
        """
        Read all QboAuths.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[QboAuth]:
        """
        Read a QboAuth by ID.
        """
        return self.repo.read_by_id(id)
    
    def read_by_public_id(self, public_id: str) -> Optional[QboAuth]:
        """
        Read a QboAuth by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_realm_id(self, realm_id: str) -> Optional[QboAuth]:
        """
        Read a QboAuth by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def update_by_realm_id(self, code: str, realm_id: str, state: str, token_type: str, id_token: str, access_token: str, expires_in: int, refresh_token: str, x_refresh_token_expires_in: int) -> Optional[QboAuth]:
        """
        Update a QboAuth by realm ID.
        """
        existing = self.read_by_realm_id(realm_id)
        if existing:
            existing.code = code
            existing.realm_id = realm_id
            existing.state = state
            existing.token_type = token_type
            existing.id_token = id_token
            existing.access_token = access_token
            existing.expires_in = expires_in
            existing.refresh_token = refresh_token
            existing.x_refresh_token_expires_in = x_refresh_token_expires_in
            return self.repo.update_by_realm_id(existing)
        return None

    def delete_by_realm_id(self, realm_id: str) -> Optional[QboAuth]:
        """
        Delete a QboAuth by realm ID.
        """
        return self.repo.delete_by_realm_id(realm_id)

    def is_token_expired(self, qbo_auth: QboAuth, buffer_seconds: int = 60) -> bool:
        """
        Check if the access token has expired.
        
        Args:
            qbo_auth: The QboAuth object to check
            buffer_seconds: Number of seconds before actual expiration to consider token expired (default: 60)
                           This prevents using tokens that are about to expire.
        
        Returns:
            True if token is expired or will expire within buffer_seconds, False otherwise
        """
        if not qbo_auth or not qbo_auth.modified_datetime or not qbo_auth.expires_in:
            # If we don't have the necessary data, consider it expired for safety
            logger.warning("QboAuth missing expiration data, considering expired")
            return True
        
        try:
            # Parse the modified_datetime (format: "YYYY-MM-DD HH:MM:SS")
            modified_time = datetime.strptime(qbo_auth.modified_datetime, "%Y-%m-%d %H:%M:%S")
            # Convert to UTC if not already timezone-aware
            if modified_time.tzinfo is None:
                modified_time = modified_time.replace(tzinfo=timezone.utc)
            else:
                modified_time = modified_time.astimezone(timezone.utc)
            
            # Calculate expiration time
            expiration_time = modified_time + timedelta(seconds=qbo_auth.expires_in)
            
            # Apply buffer - consider expired if within buffer_seconds of expiration
            expiration_time_with_buffer = expiration_time - timedelta(seconds=buffer_seconds)
            
            # Check if current time is past the expiration (with buffer)
            current_time = datetime.now(timezone.utc)
            is_expired = current_time >= expiration_time_with_buffer
            
            if is_expired:
                logger.info(f"Token expired. Modified: {qbo_auth.modified_datetime}, Expires in: {qbo_auth.expires_in}s, Current: {current_time}")
            
            return is_expired
        except Exception as e:
            logger.error(f"Error checking token expiration: {e}")
            # On error, consider expired for safety
            return True

    def ensure_valid_token(self, realm_id: Optional[str] = None, buffer_seconds: int = 60) -> Optional[QboAuth]:
        """
        Ensure the access token is valid. If expired, automatically refresh it.
        
        Since this is a single-tenant system with only one auth record, realm_id is optional.
        If not provided, uses the first (and only) auth record.
        
        Args:
            realm_id: Optional QuickBooks realm ID. If None, uses the first auth record found.
            buffer_seconds: Number of seconds before actual expiration to consider token expired (default: 60)
        
        Returns:
            QboAuth object with valid token, or None if refresh failed
        """
        # Get auth record - use specific realm_id if provided, otherwise use first
        if realm_id:
            qbo_auth = self.read_by_realm_id(realm_id)
        else:
            # Single-tenant: just get the first (and only) auth record
            all_auths = self.read_all()
            if not all_auths or len(all_auths) == 0:
                logger.error("No QboAuth records found")
                return None
            qbo_auth = all_auths[0]
            # Get realm_id from the auth record for logging/refresh
            realm_id = qbo_auth.realm_id
        
        if not qbo_auth:
            logger.error(f"No QboAuth found for realm_id: {realm_id}")
            return None
        
        # Check if token is expired
        if not self.is_token_expired(qbo_auth, buffer_seconds):
            logger.debug(f"Token for realm_id {realm_id} is still valid")
            return qbo_auth
        
        # Token is expired, refresh it
        logger.info(f"Token for realm_id {realm_id} is expired, refreshing...")
        
        try:
            # Call the refresh endpoint (it uses read_all()[0] internally, which is fine for single-tenant)
            refresh_result = connect_intuit_oauth_2_token_endpoint_refresh()

            if isinstance(refresh_result, dict) and refresh_result.get("status_code") == 201:
                # Refresh successful, read the updated auth
                if realm_id:
                    updated_auth = self.read_by_realm_id(realm_id)
                else:
                    all_auths = self.read_all()
                    updated_auth = all_auths[0] if all_auths else None
                
                if updated_auth:
                    logger.info(f"Token refreshed successfully for realm_id: {realm_id}")
                    return updated_auth
                else:
                    logger.error(f"Token refresh succeeded but could not read updated QboAuth for realm_id: {realm_id}")
                    return None
            else:
                error_message = refresh_result.get("message", "Unknown error") if isinstance(refresh_result, dict) else str(refresh_result)
                logger.error(f"Token refresh failed for realm_id {realm_id}: {error_message}")
                return None
        except Exception as e:
            logger.exception(f"Exception during token refresh for realm_id {realm_id}: {e}")
            return None
