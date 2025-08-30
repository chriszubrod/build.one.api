"""
Module for auth business.
"""

# python standard library imports
from datetime import datetime, timezone

# local imports
from utils import auth_help, token_help
from modules.user import pers_user
from shared.response import BusinessResponse


def post_auth_registration(
        username: str,
        password: str,
        confirm_password: str
    ) -> BusinessResponse:
    """
    Posts a user registration.
    """

    # Validate password and confirm password match
    if password != confirm_password:
        return BusinessResponse(
            data=None,
            message="Passwords do not match.",
            timestamp=datetime.now(),
            status_code=400,
            success=False
        )

    # Validate username
    validate_username_resp = pers_user.read_user_by_username(username)
    if validate_username_resp.success:
        return BusinessResponse(
            data=validate_username_resp.data,
            message=validate_username_resp.message,
            status_code=validate_username_resp.status_code,
            success=validate_username_resp.success,
            timestamp=validate_username_resp.timestamp
        )

    # Validate password format
    validate_password_resp = auth_help.validate_password(password)
    if not validate_password_resp['success']:
        return BusinessResponse(
            data=validate_password_resp['data'],
            message=validate_password_resp['message'],
            status_code=validate_password_resp['status_code'],
            success=validate_password_resp['success'],
            timestamp=validate_password_resp['timestamp']
        )

    # Hash password
    hash_password_resp = auth_help.hash_password(password)
    if not hash_password_resp['success']:
        return BusinessResponse(
            data=hash_password_resp['data'],
            message=hash_password_resp['message'],
            status_code=hash_password_resp['status_code'],
            success=hash_password_resp['success'],
            timestamp=hash_password_resp['timestamp']
        )

    # Create user
    _user = pers_user.User(
        username=username,
        password_hash=hash_password_resp['data']['hashed_password'],
        password_salt=hash_password_resp['data']['salt_password']
    )

    create_user_resp = pers_user.create_user(_user)
    if not create_user_resp.success:
        return BusinessResponse(
            data=create_user_resp.data,
            message=create_user_resp.message,
            status_code=create_user_resp.status_code,
            success=create_user_resp.success,
            timestamp=create_user_resp.timestamp
        )

    return BusinessResponse(
        data=None,
        message="User registered successfully.",
        status_code=201,
        success=True,
        timestamp=datetime.now()
    )


def post_auth_login(
        username: str,
        password: str
    ) -> BusinessResponse:
    """
    Posts a user login.
    """

    # Validate username
    validate_username_resp = pers_user.read_user_by_username(username)
    if not validate_username_resp.success:
        return BusinessResponse(
            data=validate_username_resp.data,
            message=validate_username_resp.message,
            status_code=validate_username_resp.status_code,
            success=validate_username_resp.success,
            timestamp=validate_username_resp.timestamp
        )

    # Get user data from response
    _user_data = validate_username_resp.data
    #print(f"User data: {_user_data}")

    # Authenticate user
    verify_password_resp = auth_help.verify_password(
        password=password,
        hashed_password=_user_data.password_hash
    )
    if not verify_password_resp['success']:
        print(f"Verify password response: {verify_password_resp}")
        return BusinessResponse(
            data=verify_password_resp['data'],
            message=verify_password_resp['message'],
            status_code=verify_password_resp['status_code'],
            success=verify_password_resp['success'],
            timestamp=verify_password_resp['timestamp']
        )

    # Generate token
    generate_token_resp = token_help.generate_token()
    if not generate_token_resp['success']:
        print(f"Generate token response: {generate_token_resp}")
        return BusinessResponse(
            data=generate_token_resp.get('data'),
            message=generate_token_resp.get('message'),
            status_code=generate_token_resp.get('status_code'),
            success=generate_token_resp.get('success'),
            timestamp=generate_token_resp.get('timestamp')
        )

    return BusinessResponse(
        data={
            'token': generate_token_resp.get('data'),
            'username': username,
            'user_id': _user_data.id
        },
        message="User logged in successfully.",
        status_code=200,
        success=True,
        timestamp=datetime.now(timezone.utc)
    )
