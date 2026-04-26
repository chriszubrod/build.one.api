# Python Standard Library Imports

# Third-party Imports
from fastapi import HTTPException, status

# Local Imports
from entities.auth.business.service import AuthService


def resolve_user_id(current_user: dict) -> int:
    """
    Translate the JWT's `sub` claim (Auth.PublicId) into Auth.UserId.

    The JWTs this app issues carry `sub` (Auth public_id) but no `user_id`
    claim. Routers that need to write to a column with a NOT NULL FK to
    dbo.[User] must resolve sub → Auth → user_id; otherwise they'd write
    NULL and trip the FK constraint.

    Raises 401 if the token has no subject or doesn't map to a known user.
    """
    sub = current_user.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token subject.",
        )
    auth = AuthService().read_by_public_id(public_id=sub)
    if not auth or not auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not map to a known user.",
        )
    return auth.user_id
