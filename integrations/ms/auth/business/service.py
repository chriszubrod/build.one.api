# Python Standard Library Imports
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

# Third-party Imports

# Local Imports
from integrations.ms.auth.business.model import MsAuth
from integrations.ms.auth.persistence.repo import MsAuthRepository
from integrations.ms.auth.external.client import connect_ms_oauth_2_token_endpoint_refresh

logger = logging.getLogger(__name__)


class MsAuthService:
    """
    Service for MsAuth entity business operations.
    """

    def __init__(self, repo: Optional[MsAuthRepository] = None):
        """Initialize the MsAuthService."""
        self.repo = repo or MsAuthRepository()

    def create(self, *, code: str, state: str, token_type: str, access_token: str, expires_in: int, refresh_token: str, scope: str, tenant_id: str, user_id: Optional[str] = None) -> MsAuth:
        """
        Create a new MsAuth.
        """
        return self.repo.create(
            code=code,
            state=state,
            token_type=token_type,
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=refresh_token,
            scope=scope,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    def read_all(self) -> list[MsAuth]:
        """
        Read all MsAuths.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[MsAuth]:
        """
        Read a MsAuth by ID.
        """
        return self.repo.read_by_id(id)
    
    def read_by_public_id(self, public_id: str) -> Optional[MsAuth]:
        """
        Read a MsAuth by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_tenant_id(self, tenant_id: str) -> Optional[MsAuth]:
        """
        Read a MsAuth by tenant ID.
        """
        return self.repo.read_by_tenant_id(tenant_id)

    def update_by_tenant_id(self, code: str, state: str, token_type: str, access_token: str, expires_in: int, refresh_token: str, scope: str, tenant_id: str, user_id: Optional[str] = None) -> Optional[MsAuth]:
        """
        Update a MsAuth by tenant ID.
        """
        existing = self.read_by_tenant_id(tenant_id)
        if existing:
            existing.code = code
            existing.state = state
            existing.token_type = token_type
            existing.access_token = access_token
            existing.expires_in = expires_in
            existing.refresh_token = refresh_token
            existing.scope = scope
            existing.user_id = user_id
            return self.repo.update_by_tenant_id(
                code=code,
                state=state,
                token_type=token_type,
                access_token=access_token,
                expires_in=expires_in,
                refresh_token=refresh_token,
                scope=scope,
                tenant_id=tenant_id,
                user_id=user_id
            )
        return None

    def delete_by_tenant_id(self, tenant_id: str) -> Optional[MsAuth]:
        """
        Delete a MsAuth by tenant ID.
        """
        return self.repo.delete_by_tenant_id(tenant_id)

    def is_token_expired(self, ms_auth: MsAuth, buffer_seconds: int = 60) -> bool:
        """
        Check if the access token has expired.
        
        Args:
            ms_auth: The MsAuth object to check
            buffer_seconds: Number of seconds before actual expiration to consider token expired (default: 60)
                           This prevents using tokens that are about to expire.
        
        Returns:
            True if token is expired or will expire within buffer_seconds, False otherwise
        """
        if not ms_auth or not ms_auth.modified_datetime or not ms_auth.expires_in:
            # If we don't have the necessary data, consider it expired for safety
            logger.warning("MsAuth missing expiration data, considering expired")
            return True
        
        try:
            # Parse the modified_datetime (format: "YYYY-MM-DD HH:MM:SS")
            modified_time = datetime.strptime(ms_auth.modified_datetime, "%Y-%m-%d %H:%M:%S")
            # Convert to UTC if not already timezone-aware
            if modified_time.tzinfo is None:
                modified_time = modified_time.replace(tzinfo=timezone.utc)
            else:
                modified_time = modified_time.astimezone(timezone.utc)
            
            # Calculate expiration time
            expiration_time = modified_time + timedelta(seconds=ms_auth.expires_in)
            
            # Apply buffer - consider expired if within buffer_seconds of expiration
            expiration_time_with_buffer = expiration_time - timedelta(seconds=buffer_seconds)
            
            # Check if current time is past the expiration (with buffer)
            current_time = datetime.now(timezone.utc)
            is_expired = current_time >= expiration_time_with_buffer
            
            if is_expired:
                logger.info(f"Token expired. Modified: {ms_auth.modified_datetime}, Expires in: {ms_auth.expires_in}s, Current: {current_time}")
            
            return is_expired
        except Exception as e:
            logger.error(f"Error checking token expiration: {e}")
            # On error, consider expired for safety
            return True

    def ensure_valid_token(
        self,
        tenant_id: Optional[str] = None,
        buffer_seconds: int = 60,
        force_refresh: bool = False,
    ) -> Optional[MsAuth]:
        """
        Ensure the access token is valid. If expired (or force_refresh=True),
        automatically refresh it.

        Since this is a single-tenant system with only one auth record, tenant_id is optional.
        If not provided, uses the first (and only) auth record.

        Args:
            tenant_id: Optional tenant ID. If None, uses the first auth record found.
            buffer_seconds: Seconds before actual expiration to consider the token expired.
            force_refresh: When True, skip the expiry check and refresh unconditionally.
                            Used by the shared MsGraphClient's 401-recovery path to force
                            a fresh token when the cached one appears revoked.

        Returns:
            MsAuth object with valid token, or None if refresh failed
        """
        # Get auth record - use specific tenant_id if provided, otherwise use first
        if tenant_id:
            ms_auth = self.read_by_tenant_id(tenant_id)
        else:
            # Single-tenant: just get the first (and only) auth record
            all_auths = self.read_all()
            if not all_auths or len(all_auths) == 0:
                logger.error("No MsAuth records found")
                return None
            ms_auth = all_auths[0]
            # Get tenant_id from the auth record for logging/refresh
            tenant_id = ms_auth.tenant_id

        if not ms_auth:
            logger.error(f"No MsAuth found for tenant_id: {tenant_id}")
            return None

        # Skip expiry check when force_refresh is requested
        if not force_refresh and not self.is_token_expired(ms_auth, buffer_seconds):
            logger.debug(f"Token for tenant_id {tenant_id} is still valid")
            return ms_auth

        # Serialize concurrent refreshes across processes (API + standalone scripts
        # can both reach this path simultaneously; without the lock, both would call
        # Microsoft with the same refresh_token and the loser would be rejected because
        # MS invalidates the old refresh_token on successful rotation).
        from integrations.ms.base.locking import ms_app_lock

        lock_resource = f"ms_auth_refresh:{tenant_id}"
        with ms_app_lock(lock_resource, timeout_ms=15000) as got_lock:
            if not got_lock:
                logger.error(
                    f"Could not acquire token refresh lock for tenant {tenant_id} within timeout"
                )
                return None

            # Re-read after acquiring the lock. Another caller may have just finished
            # refreshing while we waited — in which case the token is now fresh and
            # we don't need to call MS at all. Also ensures that if we DO still
            # need to refresh, we use the latest refresh_token (not the one we read
            # before the lock).
            if tenant_id:
                ms_auth = self.read_by_tenant_id(tenant_id)
            else:
                all_auths = self.read_all()
                ms_auth = all_auths[0] if all_auths else None

            if not ms_auth:
                logger.error(f"No MsAuth found for tenant_id {tenant_id} after acquiring lock")
                return None

            # If another caller refreshed while we waited and force_refresh is False,
            # the freshly-read token is already valid — skip the MS call entirely.
            if not force_refresh and not self.is_token_expired(ms_auth, buffer_seconds):
                logger.info(
                    f"Token for tenant_id {tenant_id} was refreshed by a concurrent caller"
                )
                return ms_auth

            if force_refresh:
                logger.info(f"Force-refreshing token for tenant_id {tenant_id}")
            else:
                logger.info(f"Token for tenant_id {tenant_id} is expired, refreshing...")

            try:
                # Call the refresh endpoint (it uses read_all()[0] internally, which is fine for single-tenant)
                refresh_result = connect_ms_oauth_2_token_endpoint_refresh()

                if isinstance(refresh_result, dict) and refresh_result.get("status_code") == 201:
                    # Refresh successful, read the updated auth
                    if tenant_id:
                        updated_auth = self.read_by_tenant_id(tenant_id)
                    else:
                        all_auths = self.read_all()
                        updated_auth = all_auths[0] if all_auths else None

                    if updated_auth:
                        logger.info(f"Token refreshed successfully for tenant_id: {tenant_id}")
                        return updated_auth
                    else:
                        logger.error(f"Token refresh succeeded but could not read updated MsAuth for tenant_id: {tenant_id}")
                        return None
                else:
                    error_message = refresh_result.get("message", "Unknown error") if isinstance(refresh_result, dict) else str(refresh_result)
                    logger.error(f"Token refresh failed for tenant_id {tenant_id}: {error_message}")
                    return None
            except Exception as e:
                logger.exception(f"Exception during token refresh for tenant_id {tenant_id}: {e}")
                return None
