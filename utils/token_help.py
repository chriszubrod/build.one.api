"""
Module for token helper.
"""
# python standard library imports
from datetime import datetime, timezone
from functools import wraps
import secrets

# third party imports
from flask import request, jsonify
import jwt

# local imports


def requires_token(f):
    """
    Requires token
    """
    @wraps(f)
    def decorated(*args, **kwargs):

        # Get token from Authorization header
        token = request.headers.get('Authorization')

        # Check if token is provided and starts with 'Bearer '
        if not token or not token.startswith('Bearer '):
            return jsonify({
                'data': None,
                'message': 'Token is not provided',
                'status_code': 401,
                'success': False,
                'timestamp': datetime.now(timezone.utc)  # UTC timestamp in error response
            })

        # Remove 'Bearer ' prefix
        token = token.split(' ')[1]

        # Verify token
        verify_token_response = verify_token(token)

        # If token is invalid, try to refresh it
        if not verify_token_response['success']:
            return jsonify({
                'data': verify_token_response['data'],
                'message': verify_token_response['message'],
                'status_code': verify_token_response['status_code'],
                'success': verify_token_response['success'],
                'timestamp': verify_token_response['timestamp']
            })

        return f(*args, **kwargs)
    return decorated


def generate_token() -> dict:
    """
    Generate token with both expiry times using UTC
    """
    try:
        current_timestamp = int(datetime.now(timezone.utc).timestamp())

        #print('Token Generation:')
        #print(f'Current UTC timestamp: {current_timestamp}')

        # Ensure we're using the same timestamp for all fields
        payload = {
            #'exp': current_timestamp + int(900),  # 15 minutes from now (UTC)
            'exp': current_timestamp + int(5),  # 5 seconds from now (UTC)
            #'refresh_exp': current_timestamp + int(604800),  # 7 days from now (UTC)
            'refresh_exp': current_timestamp + int(300),  # 5 minutes from now (UTC)
            'iat': current_timestamp,  # issued at time (UTC)
            'jti': secrets.token_urlsafe(32)
        }

        #print(f'Generated payload: {payload}')

        token = jwt.encode(payload, 'secret', algorithm='HS256')

        return {
            'data': token,
            'message': 'Token generated successfully.',
            'status_code': 200,
            'success': True,
            'timestamp': datetime.now(timezone.utc)  # UTC timestamp in response
        }

    except Exception as e:

        return {
            'data': None,
            'message': str(e),
            'status_code': 500,
            'success': False,
            'timestamp': datetime.now(timezone.utc)  # UTC timestamp in error response
        }


def refresh_token(token: str) -> dict:
    """
    Refresh token
    """
    try:
        # Decode the token WITHOUT verifying 'exp'
        payload = jwt.decode(token, 'secret', algorithms=['HS256'], options={'verify_exp': False})

        current_timestamp = int(datetime.now(timezone.utc).timestamp())

        # Ensure we're using the same timestamp for all fields
        new_payload = {
            'exp': current_timestamp + int(900),  # 15 minutes from now (UTC)
            'refresh_exp': payload['refresh_exp'], # keep the original refresh_exp
            'iat': current_timestamp,  # issued at time (UTC)
            'jti': payload['jti'] # keep the original jti
        }

        token = jwt.encode(new_payload, 'secret', algorithm='HS256')

        return {
            'data': token,
            'message': 'Token refreshed successfully.',
            'status_code': 200,
            'success': True,
            'timestamp': datetime.now(timezone.utc)  # UTC timestamp in response
        }

    except Exception as e:

        return {
            'data': None,
            'message': str(e),
            'status_code': 500,
            'success': False,
            'timestamp': datetime.now(timezone.utc)  # UTC timestamp in error response
        }


def verify_token(token: str, current_timestamp: int) -> dict:
    """
    Verify token
    """
    try:
        # Decode and verify the token
        payload = jwt.decode(token, 'secret', algorithms=['HS256'])

        # Check if token has expired        
        if current_timestamp > payload['exp']:
            return {
                'data': None,
                'message': 'Token has expired.',
                'status_code': 401,
                'success': False,
                'timestamp': datetime.now(timezone.utc)
            }

        # Token is valid
        return {
            'data': payload,
            'message': 'Token is valid',
            'success': True,
            'status_code': 200,
            'timestamp': datetime.now(timezone.utc)
        }

    except jwt.ExpiredSignatureError:
        return {
            'data': None,
            'message': 'Token has expired',
            'success': False,
            'status_code': 401,
            'timestamp': datetime.now(timezone.utc)
        }
    except jwt.InvalidTokenError:
        return {
            'data': None,
            'message': 'Invalid token',
            'success': False,
            'status_code': 401,
            'timestamp': datetime.now(timezone.utc)
        }
    except Exception as e:
        return {
            'data': None,
            'message': f'Error verifying token: {str(e)}',
            'success': False,
            'status_code': 500,
            'timestamp': datetime.now(timezone.utc)
        }


def verify_refresh_token(token: str, current_timestamp: int) -> dict:
    """
    Verify refresh token
    """
    #print(f"VERIFY_REFRESH_TOKEN CALLED")
    try:
        # Decode and verify the token
        payload = jwt.decode(token, 'secret', algorithms=['HS256'], options={'verify_exp': False})
        #print(f"verify_refresh_token payload: {payload}")

        # Check if refresh token has expired
        if current_timestamp > payload['refresh_exp']:
            return {
                'data': None,
                'message': 'Refresh token has expired',
                'success': False,
                'status_code': 401,
                'timestamp': datetime.now(timezone.utc)
            }

        # Token is valid
        return {
            'data': payload,
            'message': 'Refresh token is valid',
            'success': True,
            'status_code': 200,
            'timestamp': datetime.now(timezone.utc)
        }

    except jwt.ExpiredSignatureError:
        return {
            'data': None,
            'message': 'Refresh token has expired',
            'success': False,
            'status_code': 401,
            'timestamp': datetime.now(timezone.utc)
        }
    except jwt.InvalidTokenError:
        return {
            'data': None,
            'message': 'Invalid refresh token',
            'success': False,
            'status_code': 401,
            'timestamp': datetime.now(timezone.utc)
        }
    except Exception as e:
        return {
            'data': None,
            'message': f'Error verifying refresh token: {str(e)}',
            'success': False,
            'status_code': 500,
            'timestamp': datetime.now(timezone.utc)
        }
