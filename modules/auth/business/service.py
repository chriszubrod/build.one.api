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

# Local Imports
from config import Settings
from modules.auth.business.model import (
    Auth,
    AuthToken
)
from modules.auth.persistence.repo import AuthRepository
from shared.database import (
    DatabaseConcurrencyError,
    DatabaseOperationError,
)
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer
security = HTTPBearer()


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def _verify_password(password: str, password_hash: str) -> bool:
    return _hash_password(password) == password_hash

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
    Dependency for web routes that reads token from cookies or query parameters.
    """
    token = request.cookies.get("auth_token")

    if not token:
        token = request.query_params.get("token")

    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"Location": "/auth/login"}
        )

    try:
        payload = verify_token(token=token)
        return payload
    except ValueError as e:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"Location": "/auth/login"}
        )

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
        return self.repo.update_by_id(_auth)

    def delete_by_public_id(self, *, public_id: str) -> Auth:
        _auth = self.read_by_public_id(public_id=public_id)
        return self.repo.delete_by_id(_auth.id)

    def generate_token(self, *, auth: Auth) -> AuthToken:
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
        
    def login(self, *, username: str, password: str) -> Auth:
        """
        Login a auth.
        """
        auth = self.read_by_username(username=username)
        token = self.generate_token(auth=auth)
        if not auth:
            raise ValueError("Invalid username.")
        if not _verify_password(password, auth.password_hash):
            raise ValueError("Invalid password.")
        return auth, token

    def signup(self, *, username: str, password: str, confirm_password: str) -> Auth:
        """
        Signup a auth.
        """
        if password != confirm_password:
            raise ValueError("Passwords do not match.")
        if self.read_by_username(username=username):
            raise ValueError("Username already exists.")
        return self.create(username=username, password_hash=_hash_password(password))
