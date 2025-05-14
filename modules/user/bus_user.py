"""
Module for user business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz
import re
import bcrypt

# third party imports


# local imports
from business.bus_response import BusinessResponse
from utils import function_help as fhp
from modules.user import pers_user
from persistence import pers_role


def validate_email(email: str) -> BusinessResponse:
    """
    Validates an email.
    """
    try:
        # Validate email
        if not fhp.is_valid_email(email):
            return BusinessResponse(
                success=False,
                message="Invalid email.",
                status_code=400
            )

        # Check if email is already registered
        _email = pers_user.read_user_by_email(email)

        if isinstance(_email, SuccessResponse):
            return BusinessResponse(
                success=False,
                message="Email already exists.",
                status_code=400
            )

        # Return success response
        return BusinessResponse(
            success=True,
            message="Email has been validated.",
            data=_email,
            status_code=200
        )

    # Catch all exceptions
    except Exception as e:
        return BusinessResponse(
            success=False,
            message=str(e),
            status_code=500
        )



def post_user(submission_datetime, username, password, role_guid, is_active) -> BusinessResponse:
    """
    Posts a user.
    """
    # verify role_guid is not empty
    if not role_guid:
        return BusinessResponse(
            data=None,
            message="Missing Role.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # get the role id
    role_id = 0
    read_role_pers_response = pers_role.read_role_by_guid(role_guid)
    if read_role_pers_response.success:
        role_data = read_role_pers_response.data
        role_id = role_data.id
    else:
        return BusinessResponse(
            data=read_role_pers_response.data,
            message=read_role_pers_response.message,
            success=read_role_pers_response.success,
            status_code=read_role_pers_response.status_code,
            timestamp=datetime.now(tz.tzlocal())
        )

    # Hash the password and return with salt.
    password_response = hash_password(password)
    _password_hash = ""
    _salt_password = ""
    if password_response.success:
        _password_hash = password_response.data['hashed_password']
        _salt_password = password_response.data['salt_password']
    else:
        return BusinessResponse(
            data=password_response.data,
            message=password_response.message,
            success=password_response.success,
            status_code=password_response.status_code,
            timestamp=datetime.now(tz.tzlocal())
        )

    # User instance.
    _user = pers_user.User(
        created_datetime=submission_datetime,
        modified_datetime=submission_datetime,
        username=username,
        password_hash=_password_hash,
        password_salt=_salt_password,
        is_active=is_active,
        role_id=role_id
    )

    # Create the user.
    create_user_pers_response = pers_user.create_user(_user)
    return BusinessResponse(
        data=create_user_pers_response.data,
        message=create_user_pers_response.message,
        success=create_user_pers_response.success,
        status_code=create_user_pers_response.status_code,
        timestamp=datetime.now(tz.tzlocal())
    )


def authorize_user(email: str) -> BusinessResponse:
    """
    Authorizes a user.
    """
    # Get user from database.
    _user = pers_user.read_user_by_email(email)
    if not isinstance(_user, SuccessResponse):
        return BusinessResponse(
            success=False,
            message=_user.message,
            status_code=500
        )

    # Check if the user is active.
    _user_data = _user.data
    if not _user_data.is_active:
        return BusinessResponse(
            success=False,
            message="User is not active.",
            status_code=400
        )

    # Get the user role.
    _user_role = pers_role.read_role_by_id(_user_data.role_id)
    if not isinstance(_user_role, SuccessResponse):
        _user_role_id = ""
        _user_role_name = ""
    else:
        _user_role_id = _user_role.data.role_id
        _user_role_name = _user_role.data.name

    # Get the user permissions.
    #   Modules
    #   Projects
    #   Add Over Time

    return BusinessResponse(
        success=True,
        message="User authorized successfully",
        data={
            'user': {
                'id': _user_data.id,
                'username': _user_data.username,
                'is_active': _user_data.is_active,
                'role': {
                    'id': _user_role_id,
                    'name': _user_role_name
                },
                'modules': [],
                'projects': []
            }
        },
        status_code=200
    )


def get_users():
    """
    Retrieves all users from the database.
    """
    read_users_pers_response = pers_user.read_users()
    return BusinessResponse(
        data=read_users_pers_response.data,
        success=read_users_pers_response.success,
        message=read_users_pers_response.message,
        status_code=read_users_pers_response.status_code,
        timestamp=datetime.now(tz.tzlocal())
    )


def get_user_by_guid(user_guid: str) -> BusinessResponse:
    """
    Retrieves a user by guid from the database.
    """
    read_user_pers_response = pers_user.read_user_by_guid(user_guid)
    return BusinessResponse(
        data=read_user_pers_response.data,
        success=read_user_pers_response.success,
        message=read_user_pers_response.message,
        status_code=read_user_pers_response.status_code,
        timestamp=datetime.now(tz.tzlocal())
    )


def post_user_by_guid(
        id = None, 
        guid = None, 
        created_datetime = None, 
        modified_datetime = None, 
        username = None, 
        password_hash = None, 
        password_salt = None, 
        is_active = None,
        role_id = None,
        transaction_id = None) -> BusinessResponse:
    """
    Updates a user by guid.
    """
    _user = pers_user.User(
        id=id,
        guid=guid,
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        username=username,
        password_hash=password_hash,
        password_salt=password_salt,
        is_active=is_active,
        role_id=role_id,
        transaction_id=transaction_id
    )
    pers_read_user_response = pers_user.read_user_by_guid(_user.guid)
    if not isinstance(pers_read_user_response, SuccessResponse):
        return BusinessResponse(
            success=False,
            message=pers_read_user_response.message,
            status_code=pers_read_user_response.status_code
        )
    else:
        _user_data = pers_read_user_response.data
        if _user.id is None:
            _user.id = _user_data.id
        if _user.guid is None:
            _user.guid = _user_data.guid
        if _user.created_datetime is None:
            _user.created_datetime = _user_data.created_datetime
        if _user.modified_datetime is None:
            _user.modified_datetime = _user_data.modified_datetime
        if _user.username is None:
            _user.username = _user_data.username
        if _user.password_hash is None:
            _user.password_hash = _user_data.password_hash
        if _user.password_salt is None:
            _user.password_salt = _user_data.password_salt
        if _user.is_active is None:
            _user.is_active = _user_data.is_active

    pers_update_user_response = pers_user.update_user_by_id(_user)
    if isinstance(pers_update_user_response, SuccessResponse):
        return BusinessResponse(
            success=True,
            message="User updated successfully",
            status_code=200
        )
    else:
        return BusinessResponse(
            success=False,
            message=pers_update_user_response.message,
            status_code=pers_update_user_response.status_code
        )
