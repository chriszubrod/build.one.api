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
from entities.organization.business.service import OrganizationService
from entities.company.business.service import CompanyService
from entities.user.business.service import UserService
from entities.module.business.service import ModuleService
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
logger = logging.getLogger(__name__)


class WebAuthenticationRequired(Exception):
    """
    Exception raised when web authentication is required.
    This will be caught by an exception handler that redirects to login.
    """
    pass


class RefreshRequired(Exception):
    """
    Exception raised when access token is expired but refresh token may be valid.
    Handler redirects to /auth/refresh?next=... so the session can be restored.
    """
    def __init__(self, next_path: str):
        self.next_path = next_path
        super().__init__(next_path)


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
    Verify a JWT token.
    """
    try:
        settings = Settings()
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm]
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
                return verify_token(
                    token=header_token,
                    expected_token_type="access",
                    allow_legacy=True,
                )
            except ValueError as e:
                errors.append(str(e))

    cookie_token = request.cookies.get("token.access_token")
    if cookie_token:
        _require_csrf(request)
        try:
            return verify_token(
                token=cookie_token,
                expected_token_type="access",
                allow_legacy=True,
            )
        except ValueError as e:
            errors.append(str(e))

    detail = errors[0] if errors else "Not authenticated."
    raise HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )

def get_current_user_web(request: Request):
    """
    Dependency for web routes that reads token from cookies or headers only.
    Security: Does not read from query parameters to prevent token leakage.
    Raises WebAuthenticationRequired exception if authentication fails,
    which will be caught by an exception handler that redirects to login.
    """
    auth_token = request.cookies.get("token.access_token")
    used_header = False

    if not auth_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]
            used_header = True

    if not auth_token:
        raise WebAuthenticationRequired()

    try:
        if not used_header and request.cookies.get("token.access_token"):
            _require_csrf(request)
        auth_payload = verify_token(
            token=auth_token,
            expected_token_type="access",
            allow_legacy=True,
        )

        try:
            _organizations = OrganizationService().read_all()
            auth_payload["organizations"] = [org.to_dict() for org in _organizations]
        except Exception:
            auth_payload["organizations"] = []
        
        try:
            _companies = CompanyService().read_all()
            auth_payload["companies"] = [company.to_dict() for company in _companies]
        except Exception:
            auth_payload["companies"] = []
        
        try:
            _modules = ModuleService().read_all()
            auth_payload["modules"] = [module.to_dict() for module in _modules]
        except Exception:
            auth_payload["modules"] = []
        
        # TODO: Add projects when available
        auth_payload["projects"] = []

        return auth_payload
    except ValueError as e:
        # On expired access token, try refresh via redirect so full-page nav stays logged in
        if str(e) == "Token has expired." and request.cookies.get("token.refresh_token"):
            next_path = request.url.path or "/dashboard"
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            raise RefreshRequired(next_path=next_path)
        raise WebAuthenticationRequired()


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

    def update_by_public_id(self, *, public_id: str, auth) -> Auth:
        _auth = self.read_by_public_id(public_id=public_id)
        if _auth:
            _auth.row_version = auth.row_version
            _auth.username = auth.username
            if getattr(auth, "password", None):
                _auth.password_hash = _hash_password(auth.password)
            _auth.user_id = auth.user_id

        return self.repo.update_by_id(_auth)

    def update_user_id_by_public_id(self, *, public_id: str, user_public_id: str) -> Auth:
        _auth = self.read_by_public_id(public_id=public_id)
        if _auth:
            _user = UserService().read_by_public_id(public_id=user_public_id)
            if _user:
                _auth.user_id = _user.id
            else:
                raise ValueError(f"User with public ID {user_public_id} not found.")
        return self.repo.update_by_id(_auth)


    def delete_by_public_id(self, *, public_id: str) -> Auth:
        _auth = self.read_by_public_id(public_id=public_id)
        return self.repo.delete_by_id(_auth.id)

    def _build_token_payload(
        self,
        *,
        auth: Auth,
        token_type: str,
        expires_in: int,
    ) -> Tuple[dict, datetime, datetime, str]:
        now = datetime.now(timezone.utc)
        exp = now + timedelta(seconds=expires_in)
        jti = str(uuid.uuid4())
        payload = {
            "sub": auth.public_id,
            "username": auth.username,
            "tenant_id": 1,  # Default tenant for now, will come from user/org lookup in future
            "iat": now,
            "exp": exp,
            "jti": jti,
            "token_type": token_type,
        }
        return payload, now, exp, jti

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

    def _issue_refresh_token(self, *, auth: Auth) -> Tuple[RefreshToken, dict]:
        settings = Settings()
        payload, issued_at, expires_at, jti = self._build_token_payload(
            auth=auth,
            token_type="refresh",
            expires_in=settings.refresh_token_expire_seconds,
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

    def generate_auth_token(self, *, auth: Auth) -> AuthToken:
        """
        Generate a token for a auth.
        """
        settings = Settings()
        payload, _, _, _ = self._build_token_payload(
            auth=auth,
            token_type="access",
            expires_in=settings.access_token_expire_seconds,
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

    def generate_refresh_token(self, *, auth: Auth) -> RefreshToken:
        """
        Generate a refresh token for a auth.
        """
        refresh_token, _ = self._issue_refresh_token(auth=auth)
        return refresh_token

    def login(self, *, username: str, password: str) -> Auth:
        """
        Login a auth.
        Returns: (auth, access_token, refresh_token)
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

    def signup(self, *, username: str, password: str, confirm_password: str) -> Auth:
        """
        Signup a auth.
        Returns: (auth, access_token, refresh_token)
        """
        if password != confirm_password:
            raise ValueError("Passwords do not match.")
        
        _user = self.read_by_username(username=username)
        if _user:
            raise ValueError("Username already exists.")

        auth = self.create(username=username, password=password)
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

            if stored:
                if stored.revoked_datetime:
                    raise ValueError("Refresh token has been revoked.")
                if stored.expires_datetime and stored.expires_datetime <= now:
                    raise ValueError("Refresh token has expired.")
            else:
                logger.info("Legacy refresh token accepted for migration for %s", auth.public_id)

            # Generate new tokens (token rotation for security)
            new_access_token = self.generate_auth_token(auth=auth)
            new_refresh_token, refresh_meta = self._issue_refresh_token(auth=auth)

            # Revoke old token (or record legacy token as revoked)
            replaced_by_jti = refresh_meta["jti"]
            if stored:
                revoked = self.token_repo.revoke_by_hash(
                    token_hash=token_hash,
                    revoked_datetime=now,
                    replaced_by_token_jti=replaced_by_jti,
                )
                if not revoked:
                    raise ValueError("Refresh token has been revoked.")
            else:
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
