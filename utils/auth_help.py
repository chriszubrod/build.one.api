"""
Module for auth helper.
"""

# python standard library imports
import re
from datetime import datetime
from functools import wraps
from flask import session, redirect, url_for

# third party imports
import bcrypt

# local imports
from helper.token_help import verify_token, verify_refresh_token, refresh_token


def requires_auth(permission=None):
    """
    Decorator for web routes that require authentication.
    Checks both session and token validity.
    
    Args:
        permission: Optional permission string to check
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check if user is logged in via session
            if not session.get('logged_in'):
                return redirect(url_for('auth_web.login_route'))

            # Check if we have a token
            token = session.get('token')
            if not token:
                session.clear()  # Clear invalid session
                return redirect(url_for('auth_web.login_route'))

            # Verify token
            verify_result = verify_token(token)
            #print(f'Verify Token Result: {verify_result}')

            if verify_result['success']:
                # If no specific permission required, allow access
                if not permission:
                    return f(*args, **kwargs)

                # Check if user has required permission
                user_permissions = session.get('permissions', [])
                if permission not in user_permissions:
                    return redirect(url_for('auth_web.unauthorized_route'))

                return f(*args, **kwargs)

            # Token is not valid, check if it can be refreshed
            verify_refresh_result = verify_refresh_token(token)
            #print(f'Verify Refresh Token Result: {verify_refresh_result}')

            if verify_refresh_result['success']:
                refresh_result = refresh_token(token)
                if refresh_result['success']:
                    session['token'] = refresh_result['data']
                    return f(*args, **kwargs)

            # If we get here, token is invalid and couldn't be refreshed
            session.clear()
            return redirect(url_for('auth_web.login_route'))

        return decorated_function
    return decorator


def validate_password(password: str) -> dict:
    """
    Validates a password.
    """
    if len(password) < 8:
        # Must be at least 8 characters long
        return {
            'data': None,
            'message': 'Password must be at least 8 characters long.',
            'status_code': 400,
            'success': False,
            'timestamp': datetime.now(),
        }

    # Check for uppercase, lowercase, number, and special character
    if not re.search(r'[A-Z]', password):
        # Must contain at least one uppercase letter
        return {
            'data': None,
            'message': 'Password must contain at least one uppercase letter.',
            'status_code': 400,
            'success': False,
            'timestamp': datetime.now(),
        }

    if not re.search(r'[a-z]', password):
        # Must contain at least one lowercase letter
        return {
            'data': None,
            'message': 'Password must contain at least one lowercase letter.',
            'status_code': 400,
            'success': False,
            'timestamp': datetime.now(),
        }

    if not re.search(r'[0-9]', password):
        # Must contain at least one number
        return {
            'data': None,
            'message': 'Password must contain at least one number.',
            'status_code': 400,
            'success': False,
            'timestamp': datetime.now(),
        }

    if not re.search(r'[!@#$%^&*()]', password):
        # Must contain at least one special character
        return {
            'data': None,
            'message': 'Password must contain at least one special character.',
            'status_code': 400,
            'success': False,
            'timestamp': datetime.now(),
        }

    # Return success response
    return {
        'data': None,
        'message': 'Password has been validated.',
        'status_code': 200,
        'success': True,
        'timestamp': datetime.now(),
    }


def hash_password(password: str) -> dict:
    """
    Hashes a password.
    """
    try:
        salt = bcrypt.gensalt()
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)

        return {
            'data': {
                'hashed_password': hashed_password.decode('utf-8'),
                'salt_password': salt.decode('utf-8')
            },
            'message': 'Password has been hashed.',
            'status_code': 200,
            'success': True,
            'timestamp': datetime.now(),
        }
    except Exception as e:
        return {
            'data': None,
            'message': str(e),
            'status_code': 500,
            'success': False,
            'timestamp': datetime.now(),
        }


def verify_password(password: str, hashed_password: str) -> dict:
    """
    Verifies a password.
    """
    try:
        if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
            return {
                'data': None,
                'message': 'Password is correct.',
                'status_code': 200,
                'success': True,
                'timestamp': datetime.now(),
            }
        else:
            return {
                'data': None,
                'message': 'Password is incorrect.',
                'status_code': 400,
                'success': False,
                'timestamp': datetime.now(),
            }
    except Exception as e:
        return {
            'data': None,
            'message': str(e),
            'status_code': 500,
            'success': False,
            'timestamp': datetime.now(),
        }
