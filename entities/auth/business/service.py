# Python Standard Library Imports
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import base64
import hashlib
import hmac
import json
import logging
import secrets
import uuid

# Third-party Imports
import jwt
import bcrypt

# Local Imports
from config import Settings
from entities.auth.business.model import (
    Auth,
    AuthToken,
    RefreshToken
)
from entities.auth.persistence.repo import AuthRepository
from entities.auth.persistence.token_repo import AuthRefreshTokenRepository
from entities.user.business.service import UserService
from shared.authz import set_authz_context
from shared.authz.companies import (
    ActiveCompany,
    resolve_active_company_for_user,
    resolve_company_by_public_id_for_user,
)
from shared.database import (
    DatabaseConcurrencyError,
    DatabaseOperationError,
)
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)
CSRF_COOKIE_NAME = "token.csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Allow tokens that expired this many seconds ago (clock skew / in-flight requests)
JWT_EXP_LEEWAY_SECONDS = 30
logger = logging.getLogger(__name__)


def _hash_password(password: str) -> str:
    """
    Hash password using bcrypt with salt.
    Returns the hashed password as a string.
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def _verify_password(password: str, password_hash: str) -> Tuple[bool, Optional[str]]:
    """
    Verify password against bcrypt hash.
    Handles both old SHA-256 hashes (for migration) and new bcrypt hashes.
    """
    if not password_hash:
        return False, None
    try:
        # Try bcrypt verification first
        if bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8')):
            return True, None
    except (ValueError, TypeError):
        pass
    # Fallback for legacy SHA-256 hashes (for migration period)
    # TODO: Remove this fallback after all passwords are migrated
    legacy_hash = hashlib.sha256(password.encode()).hexdigest()
    if hmac.compare_digest(legacy_hash, password_hash):
        return True, _hash_password(password)
    return False, None

def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _base64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)

def _coerce_datetime(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


def verify_token(*, token: str, expected_token_type: Optional[str] = None, allow_legacy: bool = False) -> dict:
    """
    Verify a JWT token. Uses a small exp leeway to avoid 401s from clock skew or in-flight requests at expiry.
    """
    try:
        settings = Settings()
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            leeway=JWT_EXP_LEEWAY_SECONDS,
        )
        if expected_token_type:
            token_type = payload.get("token_type")
            if token_type:
                if token_type != expected_token_type:
                    raise ValueError("Invalid token type.")
            elif not allow_legacy:
                raise ValueError("Invalid token type.")
        return payload
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired.")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token.")

def _require_csrf(request: Request) -> None:
    if request.method.upper() not in UNSAFE_METHODS:
        return
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)
    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")
    if not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")

def _enrich_payload_with_authz(payload: dict) -> dict:
    """
    Read `uid`, `cid`, `isa` from the JWT payload and populate the
    access-control ContextVars + the returned payload dict.

    When the token is missing one of those claims (legacy token issued
    before the rebuild), behavior depends on `Settings.jwt_cid_grace_days`:

      - grace_days > 0: fall back via DB lookup (resolve the user from
        the Auth.sub link, pick the user's default Company).
      - grace_days == 0 (default since Phase 2): skip the fallback. The
        corresponding context field stays None and downstream RBAC fails
        closed — the user has to log in again to mint a fresh token
        carrying the claims.

    Mutates `payload` in place to add resolved `user_id`, `user_public_id`,
    `company_id`, `company_public_id`, and `is_system_admin` keys for
    downstream consumers, then returns it.
    """
    sub = payload.get("sub")
    cid_claim = payload.get("cid")
    uid_claim = payload.get("uid")
    isa_claim = payload.get("isa")

    grace_days = Settings().jwt_cid_grace_days
    legacy_fallback_allowed = grace_days > 0

    user_id: Optional[int] = None
    user_public_id: Optional[str] = uid_claim
    is_system_admin: bool = bool(isa_claim) if isa_claim is not None else False
    company_id: Optional[int] = None
    company_public_id: Optional[str] = cid_claim

    # Resolve user_id from `uid` if present, else from `sub` -> Auth.user_id
    # (only allowed during the grace window).
    if uid_claim:
        user = UserService().read_by_public_id(public_id=uid_claim)
        if user:
            user_id = user.id
            if isa_claim is None:
                is_system_admin = bool(getattr(user, "is_system_admin", False))
    elif sub and legacy_fallback_allowed:
        try:
            auth_row = AuthService().read_by_public_id(public_id=sub)
        except Exception:
            auth_row = None
        if auth_row and auth_row.user_id:
            user_id = auth_row.user_id
            user = UserService().read_by_id(id=user_id)
            if user:
                user_public_id = user.public_id
                is_system_admin = bool(getattr(user, "is_system_admin", False))

    # Resolve company_id from `cid` if present and accessible. Without a
    # `cid` claim, fall back to the user's default Company only during
    # the grace window — otherwise leave it unset so downstream RBAC
    # can't accidentally resolve to a stale Company.
    if user_id is not None:
        if cid_claim:
            company = resolve_company_by_public_id_for_user(
                user_id=user_id, company_public_id=cid_claim
            )
        elif legacy_fallback_allowed:
            company = resolve_active_company_for_user(user_id)
        else:
            company = None
        if company is not None:
            company_id = company.id
            company_public_id = company.public_id

    set_authz_context(
        user_id=user_id,
        company_id=company_id,
        is_system_admin=is_system_admin,
    )

    payload["user_id"] = user_id
    payload["user_public_id"] = user_public_id
    payload["company_id"] = company_id
    payload["company_public_id"] = company_public_id
    payload["is_system_admin"] = is_system_admin
    return payload


def get_current_user_api(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """
    Dependency to get the current user from the token.
    Accepts either Authorization header (Bearer) or HttpOnly cookie.
    """
    token = None
    errors = []

    if credentials and credentials.scheme.lower() == "bearer":
        header_token = credentials.credentials
        if header_token and header_token not in {"null", "undefined"}:
            try:
                payload = verify_token(
                    token=header_token,
                    expected_token_type="access",
                    allow_legacy=True,
                )
                return _enrich_payload_with_authz(payload)
            except ValueError as e:
                errors.append(str(e))

    cookie_token = request.cookies.get("token.access_token")
    if cookie_token:
        _require_csrf(request)
        try:
            payload = verify_token(
                token=cookie_token,
                expected_token_type="access",
                allow_legacy=True,
            )
            return _enrich_payload_with_authz(payload)
        except ValueError as e:
            errors.append(str(e))

    detail = errors[0] if errors else "Not authenticated."
    raise HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )

class AuthService:
    """
    Business logic for authentication flows and CRUD management.
    """

    def __init__(
        self,
        repo: Optional[AuthRepository] = None,
        token_repo: Optional[AuthRefreshTokenRepository] = None,
    ):
        """Initialize the AuthService."""
        self.repo = repo or AuthRepository()
        self.token_repo = token_repo or AuthRefreshTokenRepository()

    def create(self, *, username: str, password: str) -> Auth:
        """
        Create a new auth record.
        """
        if self.read_by_username(username=username):
            raise ValueError("Username already exists")

        password_hash = _hash_password(password)
        return self.repo.create(username=username, password_hash=password_hash)

    def read_by_public_id(self, *, public_id: str) -> Auth:
        """
        Get an auth record by public ID.
        """
        return self.repo.read_by_public_id(public_id=public_id)

    def read_by_username(self, *, username: str) -> Auth:
        """
        Get an auth record by username.
        """
        return self.repo.read_by_username(username=username)

    def read_by_user_id(self, *, user_id: int) -> Optional[Auth]:
        """
        Get the Auth row linked to the given UserId, if any.
        """
        return self.repo.read_by_user_id(user_id=user_id)

    def revoke_all_refresh_tokens_for_auth(self, *, auth_id: int) -> int:
        """
        Bulk-revoke every non-revoked refresh token for the given Auth.
        Returns the number of rows revoked. Called on password change
        (self-service or admin) to invalidate outstanding sessions.
        Failure is best-effort — caller logs and continues.
        """
        try:
            return self.token_repo.revoke_all_for_auth_id(
                auth_id=auth_id,
                revoked_datetime=datetime.now(timezone.utc),
            )
        except Exception as error:
            logger.warning(
                "Failed to revoke refresh tokens for auth_id=%s: %s",
                auth_id,
                error,
            )
            return 0

    def set_credentials_for_user(self, *, user_public_id: str, username: str, password: str) -> Auth:
        """
        Admin: create-or-update the Auth row for a User. Sets username and
        password. Validates password length (>= 8) and username uniqueness
        across other Auth rows.

        Side effect (Gap 3): on every password change, revokes all of the
        target user's outstanding refresh tokens so they're forced to
        re-login with the new password.
        """
        if not username or len(username.strip()) < 1:
            raise ValueError("Username is required.")
        username = username.strip()
        if not password or len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        user = UserService().read_by_public_id(public_id=user_public_id)
        if not user:
            raise ValueError(f"User with public ID {user_public_id} not found.")

        existing_auth = self.repo.read_by_user_id(user_id=user.id)

        # Username uniqueness — must not collide with another Auth row
        username_owner = self.read_by_username(username=username)
        if username_owner and (not existing_auth or username_owner.id != existing_auth.id):
            raise ValueError("Username already exists.")

        if existing_auth:
            existing_auth.username = username
            existing_auth.password_hash = _hash_password(password)
            existing_auth.user_id = user.id
            updated = self.repo.update_by_id(existing_auth)
            # Admin password change is a security operation — invalidate
            # the target user's outstanding sessions.
            self.revoke_all_refresh_tokens_for_auth(auth_id=updated.id)
            return updated

        # No Auth yet — create one and link to the user
        new_auth = self.repo.create(username=username, password_hash=_hash_password(password))
        new_auth.user_id = user.id
        return self.repo.update_by_id(new_auth)

    def change_password(
        self,
        *,
        user_sub: str,
        current_password: str,
        new_password: str,
    ) -> Tuple[Auth, AuthToken, RefreshToken]:
        """
        Self-service password change. Validates the current password,
        updates the hash, revokes every outstanding refresh token for
        this Auth, and mints a fresh access + refresh pair so the
        caller stays logged in (Q3.1 = (a)).
        """
        if not current_password or not new_password:
            raise ValueError("Current and new passwords are required.")
        if len(new_password) < 8:
            raise ValueError("New password must be at least 8 characters.")
        if current_password == new_password:
            raise ValueError("New password must differ from the current password.")

        auth = self.read_by_public_id(public_id=user_sub)
        if not auth:
            raise ValueError("Invalid session.")

        valid, upgraded_hash = _verify_password(current_password, auth.password_hash)
        if not valid:
            raise ValueError("Current password is incorrect.")

        # Apply new password. The upgraded_hash from the legacy SHA-256
        # path is irrelevant — we're about to overwrite it.
        auth.password_hash = _hash_password(new_password)
        updated = self.repo.update_by_id(auth)

        # Revoke every outstanding refresh token for this Auth — old
        # sessions die immediately. Then mint a fresh pair for the
        # caller so they stay logged in.
        self.revoke_all_refresh_tokens_for_auth(auth_id=updated.id)

        user_id, user_public_id, is_system_admin = self._resolve_user_for_auth(updated)
        active_company = (
            resolve_active_company_for_user(user_id) if user_id is not None else None
        )

        access_token = self.generate_auth_token(
            auth=updated,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )
        refresh_token, refresh_meta = self._issue_refresh_token(
            auth=updated,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )
        self._store_refresh_token(
            auth=updated,
            refresh_token=refresh_token,
            issued_at=refresh_meta["issued_at"],
            expires_at=refresh_meta["expires_at"],
            jti=refresh_meta["jti"],
        )
        return updated, access_token, refresh_token

    def update_by_public_id(self, *, public_id: str, auth) -> Auth:
        _auth = self.read_by_public_id(public_id=public_id)
        if not _auth:
            raise ValueError(f"Auth with public ID {public_id} not found.")
        _auth.row_version = auth.row_version
        _auth.username = auth.username
        if getattr(auth, "password", None):
            _auth.password_hash = _hash_password(auth.password)
        _auth.user_id = auth.user_id

        return self.repo.update_by_id(_auth)

    def update_user_id_by_public_id(self, *, public_id: str, user_public_id: str) -> Auth:
        _auth = self.read_by_public_id(public_id=public_id)
        if not _auth:
            raise ValueError(f"Auth with public ID {public_id} not found.")
        _user = UserService().read_by_public_id(public_id=user_public_id)
        if not _user:
            raise ValueError(f"User with public ID {user_public_id} not found.")
        _auth.user_id = _user.id
        return self.repo.update_by_id(_auth)


    def delete_by_public_id(self, *, public_id: str) -> Auth:
        _auth = self.read_by_public_id(public_id=public_id)
        if not _auth:
            raise ValueError(f"Auth with public ID {public_id} not found.")
        return self.repo.delete_by_id(_auth.id)

    def _build_token_payload(
        self,
        *,
        auth: Auth,
        token_type: str,
        expires_in: int,
        user_public_id: Optional[str] = None,
        active_company: Optional[ActiveCompany] = None,
        is_system_admin: Optional[bool] = None,
    ) -> Tuple[dict, datetime, datetime, str]:
        now = datetime.now(timezone.utc)
        exp = now + timedelta(seconds=expires_in)
        jti = str(uuid.uuid4())
        payload = {
            "sub": auth.public_id,
            "username": auth.username,
            "tenant_id": 1,  # Legacy claim kept for backwards-compat; replaced by `cid`.
            "iat": now,
            "exp": exp,
            "jti": jti,
            "token_type": token_type,
        }
        # New access-control claims (Phase 0). Tokens issued before the
        # rebuild won't carry these — the auth dependency falls back via
        # DB lookup during the JWT_CID_GRACE_DAYS window.
        if user_public_id:
            payload["uid"] = user_public_id
        if active_company is not None:
            payload["cid"] = active_company.public_id
        if is_system_admin is not None:
            payload["isa"] = bool(is_system_admin)
        return payload, now, exp, jti

    def _resolve_user_for_auth(self, auth: Auth) -> Tuple[Optional[int], Optional[str], bool]:
        """
        Look up (user_id, user_public_id, is_system_admin) for a given Auth.
        Returns (None, None, False) if the Auth row isn't linked to a user.
        """
        if not auth or not auth.user_id:
            return None, None, False
        user = UserService().read_by_id(id=auth.user_id)
        if not user:
            return auth.user_id, None, False
        return (
            auth.user_id,
            user.public_id,
            bool(getattr(user, "is_system_admin", False)),
        )

    def _hash_refresh_token(self, refresh_token: str) -> str:
        settings = Settings()
        return hmac.new(
            settings.secret_key.encode("utf-8"),
            refresh_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _store_refresh_token(
        self,
        *,
        auth: Auth,
        refresh_token: RefreshToken,
        issued_at: datetime,
        expires_at: datetime,
        jti: str,
        revoked_at: Optional[datetime] = None,
        replaced_by_jti: Optional[str] = None,
    ) -> None:
        if not auth.id:
            raise ValueError("Auth ID missing for refresh token storage.")
        token_hash = self._hash_refresh_token(refresh_token.refresh_token)
        self.token_repo.create_refresh_token(
            auth_id=auth.id,
            token_hash=token_hash,
            token_jti=jti,
            issued_datetime=issued_at,
            expires_datetime=expires_at,
            revoked_datetime=revoked_at,
            replaced_by_token_jti=replaced_by_jti,
        )

    def _issue_refresh_token(
        self,
        *,
        auth: Auth,
        user_public_id: Optional[str] = None,
        active_company: Optional[ActiveCompany] = None,
        is_system_admin: Optional[bool] = None,
    ) -> Tuple[RefreshToken, dict]:
        settings = Settings()
        payload, issued_at, expires_at, jti = self._build_token_payload(
            auth=auth,
            token_type="refresh",
            expires_in=settings.refresh_token_expire_seconds,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )
        token = jwt.encode(
            payload,
            settings.secret_key,
            algorithm=settings.algorithm
        )
        refresh_token = RefreshToken(
            refresh_token=token,
            token_type="Bearer",
            expires_in=settings.refresh_token_expire_seconds
        )
        meta = {
            "issued_at": issued_at,
            "expires_at": expires_at,
            "jti": jti,
        }
        return refresh_token, meta

    def generate_auth_token(
        self,
        *,
        auth: Auth,
        user_public_id: Optional[str] = None,
        active_company: Optional[ActiveCompany] = None,
        is_system_admin: Optional[bool] = None,
    ) -> AuthToken:
        """
        Generate an access token for a given Auth row, optionally
        embedding the new access-control claims (`uid`, `cid`, `isa`).
        """
        settings = Settings()
        payload, _, _, _ = self._build_token_payload(
            auth=auth,
            token_type="access",
            expires_in=settings.access_token_expire_seconds,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )

        token = jwt.encode(
            payload,
            settings.secret_key,
            algorithm=settings.algorithm
        )

        return AuthToken(
            access_token=token,
            token_type="Bearer",
            expires_in=settings.access_token_expire_seconds
        )

    def generate_refresh_token(
        self,
        *,
        auth: Auth,
        user_public_id: Optional[str] = None,
        active_company: Optional[ActiveCompany] = None,
        is_system_admin: Optional[bool] = None,
    ) -> RefreshToken:
        """
        Generate a refresh token for a given Auth row.
        """
        refresh_token, _ = self._issue_refresh_token(
            auth=auth,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )
        return refresh_token

    def login(self, *, username: str, password: str) -> Tuple[Auth, AuthToken, RefreshToken]:
        """
        Authenticate the credentials, resolve the user's active Company,
        and mint access + refresh tokens carrying `uid`, `cid`, `isa`
        claims.

        Per Q7 (Phase 0 design): a non-system-admin user with zero
        accessible Companies is rejected here with a clear error rather
        than minted a `cid`-less token.
        """
        auth = self.read_by_username(username=username)

        if not auth:
            raise ValueError("Invalid credentials.")

        valid, upgraded_hash = _verify_password(password, auth.password_hash)
        if not valid:
            raise ValueError("Invalid credentials.")
        if upgraded_hash:
            try:
                auth.password_hash = upgraded_hash
                self.repo.update_by_id(auth)
            except Exception as error:
                logger.warning("Failed to upgrade legacy password hash for %s: %s", auth.public_id, error)

        user_id, user_public_id, is_system_admin = self._resolve_user_for_auth(auth)
        active_company = (
            resolve_active_company_for_user(user_id) if user_id is not None else None
        )

        # Q7: reject regular users with zero accessible Companies.
        if not is_system_admin and active_company is None and user_id is not None:
            raise ValueError(
                "No company access — contact your administrator."
            )

        access_token = self.generate_auth_token(
            auth=auth,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )
        refresh_token, refresh_meta = self._issue_refresh_token(
            auth=auth,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )
        self._store_refresh_token(
            auth=auth,
            refresh_token=refresh_token,
            issued_at=refresh_meta["issued_at"],
            expires_at=refresh_meta["expires_at"],
            jti=refresh_meta["jti"],
        )

        return auth, access_token, refresh_token

    def signup(self, *, username: str, password: str, confirm_password: str, registration_code: str) -> Tuple[Auth, AuthToken, RefreshToken]:
        """
        Create a new account and return auth record with access and refresh tokens.
        Requires a valid registration code to prevent open self-registration.
        """
        settings = Settings()
        if not settings.signup_registration_code:
            raise ValueError("Registration is currently disabled.")
        if not hmac.compare_digest(registration_code, settings.signup_registration_code):
            raise ValueError("Invalid registration code.")

        if password != confirm_password:
            raise ValueError("Passwords do not match.")

        _user = self.read_by_username(username=username)
        if _user:
            raise ValueError("Username already exists.")

        auth = self.create(username=username, password=password)
        # Signup precedes any User row + UserCompany grants; tokens here
        # carry no `uid` / `cid`. The caller must subsequently bind a
        # User and grant Company access; the next refresh will pick up
        # the new claims.
        access_token = self.generate_auth_token(auth=auth)
        refresh_token, refresh_meta = self._issue_refresh_token(auth=auth)
        self._store_refresh_token(
            auth=auth,
            refresh_token=refresh_token,
            issued_at=refresh_meta["issued_at"],
            expires_at=refresh_meta["expires_at"],
            jti=refresh_meta["jti"],
        )

        return auth, access_token, refresh_token
    
    def refresh_access_token(self, *, refresh_token: str) -> Tuple[AuthToken, RefreshToken]:
        """
        Refresh access token using a valid refresh token.
        Returns new access_token and refresh_token (token rotation).
        """
        try:
            if not refresh_token:
                raise ValueError("Refresh token missing.")

            refresh_payload = verify_token(
                token=refresh_token,
                expected_token_type="refresh",
                allow_legacy=True,
            )
            
            # Get auth by public_id from token
            auth = self.read_by_public_id(public_id=refresh_payload["sub"])
            if not auth:
                raise ValueError("Invalid refresh token")

            token_hash = self._hash_refresh_token(refresh_token)
            stored = self.token_repo.read_by_hash(token_hash)
            now = datetime.now(timezone.utc)
            # Grace period for revoked token: another tab may have just rotated; allow this token once
            REVOKED_GRACE_SECONDS = 60
            revoked_in_grace = False

            if stored:
                if stored.revoked_datetime:
                    delta = (now - stored.revoked_datetime).total_seconds()
                    if delta > REVOKED_GRACE_SECONDS:
                        raise ValueError("Refresh token has been revoked.")
                    revoked_in_grace = True  # Within grace: issue new tokens, skip revoke step
                elif stored.expires_datetime and stored.expires_datetime <= now:
                    raise ValueError("Refresh token has expired.")
            else:
                logger.info("Legacy refresh token accepted for migration for %s", auth.public_id)

            # Re-resolve the access-control claims so token rotation
            # picks up any UserCompany / IsSystemAdmin / role changes
            # since the previous token was minted. Prefer the `cid`
            # carried by the refresh token (so the user stays in the
            # Company they were last in); fall back to the user's
            # default if the refresh token predates the rebuild.
            user_id, user_public_id, is_system_admin = self._resolve_user_for_auth(auth)
            active_company: Optional[ActiveCompany] = None
            prior_cid = refresh_payload.get("cid")
            if prior_cid and user_id is not None:
                active_company = resolve_company_by_public_id_for_user(
                    user_id=user_id, company_public_id=prior_cid
                )
            if active_company is None and user_id is not None:
                active_company = resolve_active_company_for_user(user_id)

            # Generate new tokens (token rotation for security)
            new_access_token = self.generate_auth_token(
                auth=auth,
                user_public_id=user_public_id,
                active_company=active_company,
                is_system_admin=is_system_admin,
            )
            new_refresh_token, refresh_meta = self._issue_refresh_token(
                auth=auth,
                user_public_id=user_public_id,
                active_company=active_company,
                is_system_admin=is_system_admin,
            )

            # Revoke old token (or record legacy token as revoked); skip if already revoked in grace
            replaced_by_jti = refresh_meta["jti"]
            if stored and not revoked_in_grace:
                revoked = self.token_repo.revoke_by_hash(
                    token_hash=token_hash,
                    revoked_datetime=now,
                    replaced_by_token_jti=replaced_by_jti,
                )
                if not revoked:
                    raise ValueError("Refresh token has been revoked.")
            elif not stored:
                legacy_issued_at = _coerce_datetime(refresh_payload.get("iat")) or now
                legacy_expires_at = _coerce_datetime(refresh_payload.get("exp")) or (
                    now + timedelta(seconds=Settings().refresh_token_expire_seconds)
                )
                legacy_jti = refresh_payload.get("jti") or str(uuid.uuid4())
                self.token_repo.create_refresh_token(
                    auth_id=auth.id,
                    token_hash=token_hash,
                    token_jti=legacy_jti,
                    issued_datetime=legacy_issued_at,
                    expires_datetime=legacy_expires_at,
                    revoked_datetime=now,
                    replaced_by_token_jti=replaced_by_jti,
                )

            # Store new refresh token
            self._store_refresh_token(
                auth=auth,
                refresh_token=new_refresh_token,
                issued_at=refresh_meta["issued_at"],
                expires_at=refresh_meta["expires_at"],
                jti=refresh_meta["jti"],
            )

            return new_access_token, new_refresh_token
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Token refresh failed: {str(e)}")

    def switch_active_company(
        self,
        *,
        user_sub: str,
        company_public_id: str,
    ) -> Tuple[Auth, AuthToken, RefreshToken, ActiveCompany]:
        """
        Re-mint access + refresh tokens for the caller with a new active
        Company. Validates membership (or system-admin bypass) and
        persists `User.LastCompanyId` so future logins remember it. Busts
        the RBAC permission cache so the next request resolves the new
        Company's permission map fresh.
        """
        auth = self.read_by_public_id(public_id=user_sub)
        if not auth:
            raise ValueError("Invalid session.")

        user_id, user_public_id, is_system_admin = self._resolve_user_for_auth(auth)
        if user_id is None:
            raise ValueError("User profile not found.")

        active_company = resolve_company_by_public_id_for_user(
            user_id=user_id, company_public_id=company_public_id
        )
        if active_company is None:
            raise ValueError(
                "Company not accessible to this user."
            )

        try:
            UserService().set_last_company_id(
                user_id=user_id, last_company_id=active_company.id
            )
        except Exception as error:
            # Persisting LastCompanyId is best-effort; the switch still
            # succeeds even if this fails so the user isn't locked out
            # by a transient DB error.
            logger.warning(
                "Failed to persist LastCompanyId for user %s: %s",
                user_id,
                error,
            )

        # Drop any cached permission map keyed under this user — the next
        # request resolves under the new Company.
        try:
            from shared.rbac import invalidate_user_cache
            invalidate_user_cache(user_sub)
        except Exception as error:
            logger.warning(
                "Failed to invalidate RBAC cache after switch for %s: %s",
                user_sub,
                error,
            )

        access_token = self.generate_auth_token(
            auth=auth,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )
        refresh_token, refresh_meta = self._issue_refresh_token(
            auth=auth,
            user_public_id=user_public_id,
            active_company=active_company,
            is_system_admin=is_system_admin,
        )
        self._store_refresh_token(
            auth=auth,
            refresh_token=refresh_token,
            issued_at=refresh_meta["issued_at"],
            expires_at=refresh_meta["expires_at"],
            jti=refresh_meta["jti"],
        )

        return auth, access_token, refresh_token, active_company

    def revoke_refresh_token(self, *, refresh_token: str) -> None:
        """
        Revoke a refresh token if it exists in storage.
        """
        if not refresh_token:
            return
        token_hash = self._hash_refresh_token(refresh_token)
        try:
            self.token_repo.revoke_by_hash(
                token_hash=token_hash,
                revoked_datetime=datetime.now(timezone.utc),
                replaced_by_token_jti=None,
            )
        except Exception as error:
            logger.warning("Failed to revoke refresh token: %s", error)
