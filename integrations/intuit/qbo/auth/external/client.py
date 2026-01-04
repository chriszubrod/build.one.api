# Python Standard Library Imports
from datetime import datetime
from urllib.parse import quote, urlencode, parse_qs, urlparse
import base64
import json
import logging
import random
import requests
import string

logger = logging.getLogger(__name__)

# Third-party Imports
from fastapi import Request

# Local Imports
from integrations.intuit.qbo.base.helper import get_intuit_discovery_document
from integrations.intuit.qbo.client.persistence.repo import QboClientRepository
from integrations.intuit.qbo.auth.persistence.repo import QboAuthRepository

qbo_client_repo = QboClientRepository()
qbo_auth_repo = QboAuthRepository()

INTUIT_STATE = {
    'sent-state': '',
    'received-state': ''
}


def connect_intuit_oauth_2_endpoint():

    db_intuit_client_resp = qbo_client_repo.read_all()

    if len(db_intuit_client_resp) == 0:
        return {
            "message": "No Intuit client found",
            "status_code": 404
        }

    db_intuit_client = db_intuit_client_resp[0]

    INTUIT_STATE['sent-state'] = ''.join(
        random.choices(string.ascii_lowercase + string.digits, k=30)
    )

    auth_endpoint = get_intuit_discovery_document()

    # Define redirect URI - must match exactly what's in Intuit Developer Portal
    # IMPORTANT: This must match EXACTLY (character-for-character) what's configured in Intuit Developer Portal
    # The endpoint is: /api/v1/intuit/qbo/auth/request/callback (see integrations/intuit/qbo/auth/api/router.py)
    redirect_uri = "https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/intuit/qbo/auth/request/callback"

    # Build query parameters using urlencode for proper encoding
    query_params = {
        "client_id": db_intuit_client.client_id,
        "scope": "com.intuit.quickbooks.accounting openid email profile address phone",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": INTUIT_STATE['sent-state'],
        "claims": '{"id_token":{"realmId":null}}'
    }
    
    # urlencode properly encodes all values
    query_string = urlencode(query_params)
    
    endpoint = f"{auth_endpoint['authorization_endpoint']}?{query_string}"

    # Comprehensive logging for debugging
    logger.info("=" * 80)
    logger.info("INTUIT OAUTH AUTHORIZATION URL GENERATION")
    logger.info("=" * 80)
    logger.info(f"Redirect URI (unencoded): {redirect_uri}")
    logger.info(f"Redirect URI length: {len(redirect_uri)} characters")
    logger.info(f"Client ID: {db_intuit_client.client_id[:10]}...{db_intuit_client.client_id[-10:]}")
    logger.info(f"Authorization endpoint: {auth_endpoint['authorization_endpoint']}")
    logger.info(f"Full authorization URL: {endpoint}")
    logger.info(f"Full URL length: {len(endpoint)} characters")
    
    # Verify the redirect URI can be parsed back from the encoded URL
    parsed = urlparse(endpoint)
    parsed_params = parse_qs(parsed.query)
    if 'redirect_uri' in parsed_params:
        decoded_redirect = parsed_params['redirect_uri'][0]
        logger.info(f"Decoded redirect_uri from URL: {decoded_redirect}")
        logger.info(f"Decoded redirect_uri length: {len(decoded_redirect)} characters")
        if decoded_redirect != redirect_uri:
            logger.warning(f"⚠️  Redirect URI mismatch! Original: {redirect_uri}, Decoded: {decoded_redirect}")
        else:
            logger.info("✓ Redirect URI encoding/decoding verified correctly")
    logger.info("=" * 80)

    return {
        "message": endpoint,
        "status_code": 201
    }


def connect_intuit_oauth_2_token_endpoint(request: Request):

    qbo_client = qbo_client_repo.read_all()

    if len(qbo_client) > 0:
        client = qbo_client[0]
    else:
        return {
            "message": "No Intuit client found",
            "status_code": 404
        }

    now = datetime.now()

    INTUIT_STATE['received-state'] = request.query_params.get('state') or ''

    code = request.query_params.get('code') or ''

    realm_id = request.query_params.get('realmId') or ''

    qbo_auth = qbo_auth_repo.read_by_realm_id(realm_id)

    if len(qbo_auth) > 0:
        auth = qbo_auth[0]
        auth.code = code
        auth.realm_id = realm_id
        qbo_auth_repo.update_by_realm_id(auth)
    else:
        qbo_auth_repo.create(code=code, realm_id=realm_id)

    token_endpoint = get_intuit_discovery_document()

    s = bytes(
        client.client_id
        + ":"
        + client.client_secret,
        encoding='utf-8'
    )

    url = token_endpoint['token_endpoint']
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(s).decode()
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/intuit/qbo/auth/request/callback"
    }
    resp = requests.post(url=url, data=data, headers=headers)

    if resp.status_code == 400:
        return {
            "message": "An error occured: " + resp.text,
            "status_code": 500
        }

    if resp.status_code == 200:
        resp_json = json.loads(resp.text)
        access_token = resp_json.get('access_token')
        expires_in = resp_json.get('expires_in')
        id_token = resp_json.get('id_token')
        refresh_token = resp_json.get('refresh_token')
        token_type = resp_json.get('token_type')
        x_refresh_token_expires_in = resp_json.get('x_refresh_token_expires_in')

        qbo_auth_repo.update_by_realm_id(
            code=code,
            realm_id=realm_id,
            state=INTUIT_STATE['received-state'],
            token_type=token_type,
            id_token=id_token,
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=refresh_token,
            x_refresh_token_expires_in=x_refresh_token_expires_in
        )
        return {
            "message": "Oauth 2 Token Endpoint Successful.",
            "status_code": 201
        }


def connect_intuit_oauth_2_token_endpoint_refresh(auth):

    now = datetime.now()

    db_intuit_client_resp = qbo_client_repo.read_all()
    
    if len(db_intuit_client_resp) > 0:
        db_intuit_client = db_intuit_client_resp[0]
        client_id_and_secret = bytes(
            db_intuit_client.client_id
            + ":"
            + db_intuit_client.client_secret, encoding='utf-8'
        )

    token_endpoint = get_intuit_discovery_document()

    db_intuit_auth_resp = qbo_auth_repo.read_all()
    if len(db_intuit_auth_resp) > 0:
        db_intuit_auth = db_intuit_auth_resp[0]

    url = token_endpoint['token_endpoint']
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(client_id_and_secret).decode()
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": db_intuit_auth.refresh_token
    }
    resp = requests.post(url=url, data=data, headers=headers)
    if resp.status_code == 400:
        return {
            "message": "An error occured: " + resp.text,
            "status_code": 500
        }

    if resp.status_code == 200:
        resp_json = json.loads(resp.text)
        access_token = resp_json.get('access_token')
        expires_in = resp_json.get('expires_in')
        id_token = resp_json.get('id_token')
        refresh_token = resp_json.get('refresh_token')
        token_type = resp_json.get('token_type')
        x_refresh_token_expires_in = resp_json.get('x_refresh_token_expires_in')

        update_db_auth_refresh_resp = qbo_auth_repo.update_by_auth_guid(
            now=now,
            tokentype=token_type,
            idtoken=id_token,
            accesstoken=access_token,
            expiresin=str(expires_in),
            refreshtoken=refresh_token,
            xrefreshtokenexpiresin=str(x_refresh_token_expires_in),
            authguid=auth.__getattribute__('AuthGUID')
        )

        if update_db_auth_refresh_resp.get("status_code") == 201:
            return {
                "message": "Oauth 2 Token Endpoint Refresh Successful.",
                "status_code": 201
            }
        else:
            return {
                "message": update_db_auth_refresh_resp.get("message"),
                "status_code": 500
            }

    return {
        "message": "An error has occured during the refresh phase.",
        "status_code": 500
    }


def connect_intuit_oauth_2_token_endpoint_revoke():

    db_intuit_client_resp = qbo_client_repo.read_all()

    if len(db_intuit_client_resp) > 0:
        db_intuit_client = db_intuit_client_resp[0]
        s = bytes(
            db_intuit_client.client_id
            + ":"
            + db_intuit_client.client_secret, encoding='utf-8'
        )

    revocation_endpoint = get_intuit_discovery_document()

    db_intuit_auth_resp = qbo_auth_repo.read_all()
    if len(db_intuit_auth_resp) > 0:
        db_intuit_auth = db_intuit_auth_resp[0]

    url = revocation_endpoint['revocation_endpoint']
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(s).decode()
    }
    data = {
        "token": db_intuit_auth.access_token
    }
    resp = requests.post(url=url, data=data, headers=headers)
    if resp.status_code == 400:
        return "An error occured: " + resp.text

    if resp.status_code == 200:

        delete_db_auth_by_authguid_resp = qbo_auth_repo.delete_by_auth_guid(
            authguid=db_intuit_auth.auth_guid
        )
        resp = {
            "message": delete_db_auth_by_authguid_resp.get("message"),
            "status_code": 500
        }
        return resp
