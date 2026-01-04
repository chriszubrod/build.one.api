# Python Standard Library Imports
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import base64
import hashlib
import hmac
import json
import secrets
import uuid

# Third-party Imports
import jwt
import bcrypt

# Local Imports
from config import Settings
from modules.auth.business.model import (
    Auth,
    AuthToken,
    RefreshToken
)
from modules.auth.persistence.repo import AuthRepository
from modules.organization.business.service import OrganizationService
from modules.company.business.service import CompanyService
from modules.user.business.service import UserService
from modules.module.business.service import ModuleService
from shared.database import (
    DatabaseConcurrencyError,
    DatabaseOperationError,
)
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import RedirectResponse

security = HTTPBearer()


def _hash_password(password: str) -> str:
    """
    Hash password using bcrypt with salt.
    Returns the hashed password as a string.
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def _verify_password(password: str, password_hash: str) -> bool:
    """
    Verify password against bcrypt hash.
    Handles both old SHA-256 hashes (for migration) and new bcrypt hashes.
    """
    try:
        # Try bcrypt verification first
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except (ValueError, TypeError):
        # Fallback for legacy SHA-256 hashes (for migration period)
        # TODO: Remove this fallback after all passwords are migrated
        legacy_hash = hashlib.sha256(password.encode()).hexdigest()
        return legacy_hash == password_hash

def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _base64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)

def verify_token(*, token: str) -> dict:
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
        return payload
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired.")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token.")

def get_current_user_api(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Dependency to get the current user from the token.
    """
    try:
        payload = verify_token(token=credentials.credentials)
        return payload
    except ValueError as e:
        raise HTTPException(
            status_code=401,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"}
        )

def get_current_user_web(request: Request):
    """
    Dependency for web routes that reads token from cookies or headers only.
    Security: Does not read from query parameters to prevent token leakage.
    """
    auth_token = request.cookies.get("token.access_token")

    if not auth_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]

    if not auth_token:
        return RedirectResponse(url="/auth/login", status_code=303)

    try:
        auth_payload = verify_token(token=auth_token)

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
        return RedirectResponse(url="/auth/login", status_code=303)


class AuthService:
    """
    Business logic for authentication flows and CRUD management.
    """

    def __init__(self, repo: Optional[AuthRepository] = None):
        """Initialize the AuthService."""
        self.repo = repo or AuthRepository()

    def create(self, *, username: str, password_hash: str) -> Auth:
        """
        Create a new auth record.
        """
        if self.read_by_username(username=username):
            raise ValueError("Username already exists")

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
            _auth.password_hash = auth.password_hash
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

    def generate_auth_token(self, *, auth: Auth) -> AuthToken:
        """
        Generate a token for a auth.
        """
        settings = Settings()
        now = datetime.now(timezone.utc)
        payload = {
            "sub": auth.public_id,
            "username": auth.username,
            "iat": now,
            "exp": now + timedelta(seconds=settings.access_token_expire_seconds),
            "jti": str(uuid.uuid4())
        }

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
        settings = Settings()
        now = datetime.now(timezone.utc)
        payload = {
            "sub": auth.public_id,
            "username": auth.username,
            "iat": now,
            "exp": now + timedelta(seconds=settings.refresh_token_expire_seconds),
            "jti": str(uuid.uuid4())
        }
        token = jwt.encode(
            payload,
            settings.secret_key,
            algorithm=settings.algorithm
        )
        return RefreshToken(
            refresh_token=token,
            token_type="Bearer",
            expires_in=settings.refresh_token_expire_seconds
        )

    def login(self, *, username: str, password: str) -> Auth:
        """
        Login a auth.
        Returns: (auth, access_token, refresh_token)
        """
        auth = self.read_by_username(username=username)
        
        if not auth:
            raise ValueError("Username not found.")
        
        if not _verify_password(password, auth.password_hash):
            raise ValueError("Invalid password.")

        access_token = self.generate_auth_token(auth=auth)
        refresh_token = self.generate_refresh_token(auth=auth)
        
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

        auth = self.create(username=username, password_hash=_hash_password(password))
        access_token = self.generate_auth_token(auth=auth)
        refresh_token = self.generate_refresh_token(auth=auth)

        return auth, access_token, refresh_token
    
    def refresh_access_token(self, *, refresh_token: str) -> Tuple[AuthToken, RefreshToken]:
        """
        Refresh access token using a valid refresh token.
        Returns new access_token and refresh_token (token rotation).
        """
        try:
            refresh_payload = verify_token(token=refresh_token)
            
            # Get auth by public_id from token
            auth = self.read_by_public_id(public_id=refresh_payload["sub"])
            if not auth:
                raise ValueError("Invalid refresh token")
            
            # Generate new tokens (token rotation for security)
            new_access_token = self.generate_auth_token(auth=auth)
            new_refresh_token = self.generate_refresh_token(auth=auth)
            
            return new_access_token, new_refresh_token
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Token refresh failed: {str(e)}")
