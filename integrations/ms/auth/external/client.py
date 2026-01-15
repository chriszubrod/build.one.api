# Python Standard Library Imports
from datetime import datetime
from urllib.parse import quote, urlencode
import base64
import hashlib
import json
import logging
import os
import random
import requests
import string

logger = logging.getLogger(__name__)

# Third-party Imports
from fastapi import Request

# Local Imports
from integrations.ms.client.persistence.repo import MsClientRepository
from integrations.ms.auth.persistence.repo import MsAuthRepository

ms_client_repo = MsClientRepository()
ms_auth_repo = MsAuthRepository()

# PKCE and state storage
MS_OAUTH = {
    'state': '',
    'code_verifier': ''
}


def generate_pkce_pair():
    """
    Generate PKCE code_verifier and code_challenge.
    Returns tuple of (code_verifier, code_challenge).
    """
    # Generate random code_verifier (43-128 characters, URL-safe)
    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode('utf-8')
    
    # Generate code_challenge = base64url(SHA256(code_verifier))
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).rstrip(b'=').decode('utf-8')
    
    return code_verifier, code_challenge


def connect_ms_oauth_2_endpoint():
    """
    Generate Microsoft 365 OAuth 2.0 authorization URL with PKCE.
    """
    try:
        db_ms_client_resp = ms_client_repo.read_all()
        if not db_ms_client_resp or len(db_ms_client_resp) == 0:
            return {
                "message": "No MS client configuration found",
                "status_code": 500
            }
        client = db_ms_client_resp[0]
        client_id = client.client_id
        tenant_id = client.tenant_id
        redirect_uri = client.redirect_uri

        # Generate state for CSRF protection
        MS_OAUTH['state'] = ''.join(random.choices(string.ascii_lowercase + string.digits, k=30))
        
        # Generate PKCE code_verifier and code_challenge
        code_verifier, code_challenge = generate_pkce_pair()
        MS_OAUTH['code_verifier'] = code_verifier

        # Microsoft Graph API OAuth 2.0 authorization endpoint
        authorization_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
        
        # Scopes - full access: user profile, email, SharePoint, and files/Excel
        scopes = "openid profile User.Read Mail.Read Mail.Send Sites.ReadWrite.All Files.ReadWrite.All offline_access"
        
        # Build authorization URL with PKCE
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": scopes,
            "state": MS_OAUTH['state'],
            "code_challenge": code_challenge,
            "code_challenge_method": "S256"
        }
        
        endpoint = f"{authorization_endpoint}?{urlencode(params)}"
        
        print("=" * 80)
        print("PKCE AUTHORIZATION REQUEST")
        print("=" * 80)
        print(f"Client ID: {client_id}")
        print(f"Tenant ID: {tenant_id}")
        print(f"Redirect URI: {redirect_uri}")
        print(f"State: {MS_OAUTH['state']}")
        print(f"Code Verifier: {code_verifier[:20]}... (stored for token exchange)")
        print(f"Code Challenge: {code_challenge}")
        print("=" * 80)

        resp = {
            "message": endpoint,
            "status_code": 201
        }

        return resp
    except Exception as e:
        logger.exception("Error generating MS authorization URL")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def connect_ms_oauth_2_token_endpoint(request: Request):
    """
    Exchange authorization code for access token and refresh token using PKCE.
    """
    try:
        received_state = request.query_params.get('state', '')

        # Check for client configuration
        db_ms_client_resp = ms_client_repo.read_all()
        if not db_ms_client_resp or len(db_ms_client_resp) == 0:
            return {
                "message": "No MS client configuration found",
                "status_code": 500
            }
        client = db_ms_client_resp[0]
        client_id = client.client_id
        client_secret = client.client_secret
        tenant_id = client.tenant_id
        redirect_uri = client.redirect_uri
        
        # Get stored code_verifier from PKCE flow
        code_verifier = MS_OAUTH.get('code_verifier', '')

        # Print configuration being used
        print("=" * 80)
        print("PKCE TOKEN EXCHANGE - CONFIGURATION")
        print("=" * 80)
        print(f"Client ID: {client_id}")
        print(f"Client Secret: {client_secret[:10]}... (truncated)" if client_secret else "Client Secret: NOT FOUND")
        print(f"Tenant ID: {tenant_id}")
        print(f"Redirect URI: {redirect_uri}")
        print(f"Code Verifier: {code_verifier[:20]}... (truncated)" if code_verifier else "Code Verifier: NOT FOUND")
        print(f"State Sent: {MS_OAUTH.get('state', '')}")
        print(f"State Received: {received_state}")
        print(f"Authorization Code: {request.query_params.get('code', '')[:20]}... (truncated)")
        print("=" * 80)

        code = request.query_params.get('code', '')
        error = request.query_params.get('error', '')
        error_description = request.query_params.get('error_description', '')
        error_uri = request.query_params.get('error_uri', '')
        
        if error:
            # Print all OAuth error details from Microsoft
            print("=" * 80)
            print("OAUTH AUTHORIZATION ERROR - FROM MICROSOFT")
            print("=" * 80)
            print(f"Error: {error}")
            print(f"Error Description: {error_description}")
            print(f"Error URI: {error_uri}")
            print(f"All Query Params: {dict(request.query_params)}")
            print(f"Client ID being used: {client_id}")
            print(f"Tenant ID being used: {tenant_id}")
            print(f"Redirect URI being used: {redirect_uri}")
            print("=" * 80)
            return {
                "message": f"OAuth error: {error} - {error_description}",
                "status_code": 500
            }

        if not code:
            return {
                "message": "No authorization code received",
                "status_code": 500
            }

        # Verify state matches (CSRF protection)
        if received_state != MS_OAUTH.get('state', ''):
            print(f"State mismatch! Sent: {MS_OAUTH.get('state', '')}, Received: {received_state}")
            return {
                "message": "State mismatch - possible CSRF attack",
                "status_code": 400
            }
        
        # Verify we have a code_verifier
        if not code_verifier:
            return {
                "message": "No PKCE code_verifier found - authorization flow may have been interrupted",
                "status_code": 500
            }

        # Microsoft Graph API token endpoint
        token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

        # Prepare token request with PKCE + client_secret (confidential client with PKCE)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier
        }
        
        print(f"\nSending token request to: {token_endpoint}")
        resp = requests.post(url=token_endpoint, data=data, headers=headers)
        
        print(f"Response Status Code: {resp.status_code}")
        print(f"Response Headers: {dict(resp.headers)}")
        print(f"Response Text: {resp.text}\n")

        if resp.status_code != 200:
            error_details = resp.text
            # Print detailed error information
            print("=" * 80)
            print("TOKEN EXCHANGE ERROR - DETAILED INFORMATION")
            print("=" * 80)
            print(f"HTTP Status Code: {resp.status_code}")
            print(f"Error Response: {error_details}")
            print(f"Client ID: {client_id}")
            print(f"Tenant ID: {tenant_id}")
            print(f"Redirect URI: {redirect_uri}")
            print(f"Token Endpoint: {token_endpoint}")
            print("=" * 80)
            
            logger.error(f"Token exchange failed. Status: {resp.status_code}, Response: {error_details}")
            logger.error(f"Client ID: {client_id[:8]}..., Tenant: {tenant_id}, Redirect URI: {redirect_uri}")
            return {
                "message": f"Token exchange failed: {error_details}",
                "status_code": 500
            }

        resp_json = json.loads(resp.text)
        access_token = resp_json.get('access_token')
        expires_in = resp_json.get('expires_in', 3600)
        refresh_token = resp_json.get('refresh_token')
        token_type = resp_json.get('token_type', 'Bearer')
        scope = resp_json.get('scope', '')
        
        # Extract user ID from ID token if available
        user_id = None
        if 'id_token' in resp_json:
            try:
                # Decode JWT to get user info (simplified - just get sub claim)
                id_token = resp_json.get('id_token')
                if id_token:
                    # Simple base64 decode of payload (not full JWT validation)
                    parts = id_token.split('.')
                    if len(parts) >= 2:
                        payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
                        user_id = payload.get('sub') or payload.get('oid')
            except Exception as e:
                logger.warning(f"Could not extract user ID from ID token: {e}")

        # Check if auth record exists for this tenant
        auth = ms_auth_repo.read_by_tenant_id(tenant_id)
        
        try:
            if auth:
                ms_auth_repo.update_by_tenant_id(
                    code=code,
                    state=received_state,
                    token_type=token_type,
                    access_token=access_token,
                    expires_in=int(expires_in) if expires_in else 3600,
                    refresh_token=refresh_token,
                    scope=scope,
                    tenant_id=tenant_id,
                    user_id=user_id
                )
            else:
                ms_auth_repo.create(
                    code=code,
                    state=received_state,
                    token_type=token_type,
                    access_token=access_token,
                    expires_in=int(expires_in) if expires_in else 3600,
                    refresh_token=refresh_token,
                    scope=scope,
                    tenant_id=tenant_id,
                    user_id=user_id
                )
            resp = {
                "message": "OAuth 2 Token Endpoint Successful.",
                "status_code": 201
            }
        except Exception as error:
            resp = {
                "message": "An error occurred: " + str(error),
                "status_code": 500
            }
        return resp
    
    except Exception as e:
        print("=" * 80)
        print("EXCEPTION IN TOKEN ENDPOINT")
        print("=" * 80)
        print(f"Exception Type: {type(e).__name__}")
        print(f"Exception Message: {str(e)}")
        import traceback
        print("Traceback:")
        print(traceback.format_exc())
        print("=" * 80)
        logger.exception("Error in MS token endpoint")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def connect_ms_oauth_2_token_endpoint_refresh():
    """
    Refresh the access token using the refresh token (PKCE - no client_secret needed).
    """
    try:
        # Get client configuration
        db_ms_client_resp = ms_client_repo.read_all()
        if not db_ms_client_resp or len(db_ms_client_resp) == 0:
            return {
                "message": "No MS client configuration found",
                "status_code": 500
            }
        client = db_ms_client_resp[0]
        client_id = client.client_id
        client_secret = client.client_secret
        tenant_id = client.tenant_id

        # Get auth record
        db_ms_auth_resp = ms_auth_repo.read_all()
        if not db_ms_auth_resp or len(db_ms_auth_resp) == 0:
            return {
                "message": "No MS auth record found",
                "status_code": 500
            }
        auth = db_ms_auth_resp[0]
        refresh_token = auth.refresh_token

        # Microsoft Graph API token endpoint
        token_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        # Confidential client refresh - requires client_secret
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }
        
        resp = requests.post(url=token_endpoint, data=data, headers=headers)

        if resp.status_code != 200:
            return {
                "message": f"Token refresh failed: {resp.text}",
                "status_code": 500
            }

        resp_json = json.loads(resp.text)
        access_token = resp_json.get('access_token')
        expires_in = resp_json.get('expires_in', 3600)
        refresh_token_new = resp_json.get('refresh_token', refresh_token)  # Use new refresh token if provided, otherwise keep old one
        token_type = resp_json.get('token_type', 'Bearer')
        scope = resp_json.get('scope', auth.scope or '')

        try:
            ms_auth_repo.update_by_tenant_id(
                code=auth.code,
                state=auth.state,
                token_type=token_type,
                access_token=access_token,
                expires_in=int(expires_in) if expires_in else 3600,
                refresh_token=refresh_token_new,
                scope=scope,
                tenant_id=auth.tenant_id,
                user_id=auth.user_id
            )

            resp = {
                "message": "Oauth 2 Token Endpoint Refresh Successful.",
                "status_code": 201
            }

        except Exception as error:
            resp = {
                "message": "An error has occured during the refresh phase: " + str(error),
                "status_code": 500
            }

        return resp
    except Exception as e:
        logger.exception("Error in MS token refresh")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def connect_ms_oauth_2_token_endpoint_revoke():
    """
    Revoke the access token and delete the auth record.
    """
    try:
        # Get client configuration
        db_ms_client_resp = ms_client_repo.read_all()
        if not db_ms_client_resp or len(db_ms_client_resp) == 0:
            return {
                "message": "No MS client configuration found",
                "status_code": 500
            }
        client = db_ms_client_resp[0]
        client_id = client.client_id
        tenant_id = client.tenant_id

        # Get auth record
        db_ms_auth_resp = ms_auth_repo.read_all()
        if not db_ms_auth_resp or len(db_ms_auth_resp) == 0:
            return {
                "message": "No MS auth record found",
                "status_code": 500
            }
        auth = db_ms_auth_resp[0]
        access_token = auth.access_token

        # Microsoft Graph API revocation endpoint
        revocation_endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/logout"

        # Note: Microsoft doesn't have a standard token revocation endpoint like OAuth 2.0 spec
        # We'll just delete the auth record from our database
        # If needed, we could call the logout endpoint, but it's not required for revocation

        try:
            ms_auth_repo.delete_by_tenant_id(auth.tenant_id)
            resp = {
                "message": "Oauth 2 Token Endpoint Revoke Successful.",
                "status_code": 201
            }
        except Exception as error:
            resp = {
                "message": "An error has occured during the revoke phase: " + str(error),
                "status_code": 500
            }
        return resp
    except Exception as e:
        logger.exception("Error in MS token revocation")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def test_ms_graph_connection():
    """
    Test the Microsoft Graph API connection by calling the /me endpoint.
    Returns user profile information if successful.
    """
    try:
        # Get auth record
        db_ms_auth_resp = ms_auth_repo.read_all()
        if not db_ms_auth_resp or len(db_ms_auth_resp) == 0:
            return {
                "message": "No MS auth record found. Please authenticate first.",
                "status_code": 404
            }
        auth = db_ms_auth_resp[0]
        access_token = auth.access_token

        if not access_token:
            return {
                "message": "No access token found. Please authenticate first.",
                "status_code": 404
            }

        # Call Microsoft Graph /me endpoint
        graph_endpoint = "https://graph.microsoft.com/v1.0/me"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        print("=" * 80)
        print("TESTING MICROSOFT GRAPH API CONNECTION")
        print("=" * 80)
        print(f"Endpoint: {graph_endpoint}")
        print(f"Access Token: {access_token[:30]}... (truncated)")
        print("=" * 80)
        
        resp = requests.get(url=graph_endpoint, headers=headers)
        
        print(f"Response Status Code: {resp.status_code}")
        print(f"Response: {resp.text}")
        print("=" * 80)

        if resp.status_code == 200:
            user_data = json.loads(resp.text)
            return {
                "message": "Microsoft Graph API connection successful!",
                "status_code": 200,
                "user": {
                    "display_name": user_data.get("displayName"),
                    "email": user_data.get("mail") or user_data.get("userPrincipalName"),
                    "id": user_data.get("id"),
                    "job_title": user_data.get("jobTitle"),
                    "office_location": user_data.get("officeLocation")
                }
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "error": resp.text
            }
        else:
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error testing MS Graph connection")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def get_ms_graph_messages(top: int = 10, folder: str = "inbox"):
    """
    Fetch recent emails from the user's mailbox.
    
    Args:
        top: Number of messages to retrieve (default 10)
        folder: Mail folder to read from (default "inbox")
    
    Returns:
        List of messages with subject, from, received date, and preview
    """
    try:
        # Get auth record
        db_ms_auth_resp = ms_auth_repo.read_all()
        if not db_ms_auth_resp or len(db_ms_auth_resp) == 0:
            return {
                "message": "No MS auth record found. Please authenticate first.",
                "status_code": 404
            }
        auth = db_ms_auth_resp[0]
        access_token = auth.access_token

        if not access_token:
            return {
                "message": "No access token found. Please authenticate first.",
                "status_code": 404
            }

        # Call Microsoft Graph messages endpoint
        graph_endpoint = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
        
        params = {
            "$top": top,
            "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments",
            "$orderby": "receivedDateTime desc"
        }
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        print("=" * 80)
        print(f"FETCHING EMAILS FROM {folder.upper()}")
        print("=" * 80)
        print(f"Endpoint: {graph_endpoint}")
        print(f"Top: {top}")
        print("=" * 80)
        
        resp = requests.get(url=graph_endpoint, headers=headers, params=params)
        
        print(f"Response Status Code: {resp.status_code}")

        if resp.status_code == 200:
            data = json.loads(resp.text)
            messages = data.get("value", [])
            
            formatted_messages = []
            for msg in messages:
                from_email = msg.get("from", {}).get("emailAddress", {})
                formatted_messages.append({
                    "id": msg.get("id"),
                    "subject": msg.get("subject"),
                    "from_name": from_email.get("name"),
                    "from_email": from_email.get("address"),
                    "received": msg.get("receivedDateTime"),
                    "preview": msg.get("bodyPreview", "")[:100] + "..." if msg.get("bodyPreview") else "",
                    "is_read": msg.get("isRead"),
                    "has_attachments": msg.get("hasAttachments")
                })
            
            return {
                "message": f"Successfully retrieved {len(formatted_messages)} messages",
                "status_code": 200,
                "count": len(formatted_messages),
                "messages": formatted_messages
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "error": resp.text
            }
        else:
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error fetching MS Graph messages")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }
