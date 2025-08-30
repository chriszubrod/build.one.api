"""
Module for Microsoft 365 auth business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz
import re
import bcrypt

# third party imports


# local imports
from shared.response import BusinessResponse
from utils import function_help as fhp
from integrations.ms.auth import pers_ms_auth




def get_ms_auth_by_user_id(user_id) -> BusinessResponse:
    """
    Get the Microsoft 365 auth by user id.
    """
    read_ms_auth_pers_response = pers_ms_auth.read_ms_auth_by_user_id(user_id)
    return BusinessResponse(
        data=read_ms_auth_pers_response.data,
        success=read_ms_auth_pers_response.success,
        message=read_ms_auth_pers_response.message,
        status_code=read_ms_auth_pers_response.status_code,
        timestamp=read_ms_auth_pers_response.timestamp
    )


def post_ms_auth(
        submission_datetime,
        client_id,
        tenant,
        client_secret,
        access_token,
        expires_in,
        ext_expires_in,
        refresh_token,
        scope,
        token_type,
        user_id
    ):
    """
    Post the Microsoft 365 auth.
    """
    required_fields = {
        'client_id': client_id,
        'tenant': tenant,
        'client_secret': client_secret,
        'access_token': access_token,
        'expires_in': expires_in,
        'ext_expires_in': ext_expires_in,
        'refresh_token': refresh_token,
        'scope': scope,
        'token_type': token_type,
        'user_id': user_id
    }
    missing_fields = [field for field, value in required_fields.items() if not value]
    if missing_fields:
        return BusinessResponse(
            data=None,
            success=False,
            message=f"Missing required fields: {missing_fields}",
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )
    
    ms_auth = pers_ms_auth.MsAuth(
        created_datetime=submission_datetime,
        modified_datetime=submission_datetime,
        **required_fields
    )

    create_ms_auth_pers_response = pers_ms_auth.create_ms_auth(ms_auth)
    return BusinessResponse(
        data=create_ms_auth_pers_response.data,
        success=create_ms_auth_pers_response.success,
        message=create_ms_auth_pers_response.message,
        status_code=create_ms_auth_pers_response.status_code,
        timestamp=create_ms_auth_pers_response.timestamp
    )


def patch_ms_auth(
        ms_auth: pers_ms_auth.MsAuth
    ) -> BusinessResponse:
    """
    Patch the Microsoft 365 auth.
    """
    update_ms_auth_pers_response = pers_ms_auth.update_ms_auth_by_id(ms_auth)
    return BusinessResponse(
        data=update_ms_auth_pers_response.data,
        success=update_ms_auth_pers_response.success,
        message=update_ms_auth_pers_response.message,
        status_code=update_ms_auth_pers_response.status_code,
        timestamp=update_ms_auth_pers_response.timestamp
    )
