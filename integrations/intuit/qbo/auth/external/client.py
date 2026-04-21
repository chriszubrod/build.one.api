# Python Standard Library Imports
from datetime import datetime
from urllib.parse import quote, urlencode, parse_qs, urlparse
import base64
import json
import logging
import requests

logger = logging.getLogger(__name__)

# Third-party Imports
from fastapi import Request

# Local Imports
from integrations.intuit.qbo.base.helper import get_intuit_discovery_document
from integrations.intuit.qbo.client.persistence.repo import QboClientRepository
from integrations.intuit.qbo.auth.persistence.repo import QboAuthRepository
from integrations.intuit.qbo.auth.business.state import create_state, verify_state

qbo_client_repo = QboClientRepository()
qbo_auth_repo = QboAuthRepository()


def get_intuit_discovery_document():
    try:
        url = "https://developer.api.intuit.com/.well-known/openid_configuration"
        headers = {
            "Accept": "application/json"
        }
        resp = requests.get(url=url, headers=headers)
        if resp.status_code == 400:
            return "An error occured during intuit discovery document. " + resp.text

        if resp.status_code == 200:
            return json.loads(resp.text)

    except:
        return "An error occured while trying to call openid production configuration."


def connect_intuit_oauth_2_endpoint():
    db_intuit_client_resp = qbo_client_repo.read_all()
    client = db_intuit_client_resp[0]
    client_id = client.client_id

    state = create_state()

    auth_endpoint = get_intuit_discovery_document()

    endpoint = str(
        auth_endpoint['authorization_endpoint'] +
        "?" +
        "client_id=" + client_id +
        "&scope=com.intuit.quickbooks.accounting%20openid%20email%20profile%20address%20phone" +
        "&redirect_uri=https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/intuit/qbo/auth/request/callback" +
        "&response_type=code" +
        "&state=" + state +
        "&claims=%7B%22id_token%22%3A%7B%22realmId%22%3Anull%7D%7D"
    )

    resp = {
        "message": endpoint,
        "status_code": 201
    }

    return resp


def connect_intuit_oauth_2_token_endpoint(request: Request):

    state = request.query_params.get('state', '')
    if not verify_state(state):
        logger.warning("OAuth callback rejected: invalid or expired state token")
        return {
            "message": "Invalid or expired OAuth state token",
            "status_code": 401
        }

    # Check for client configuration
    db_intuit_client_resp = qbo_client_repo.read_all()
    if not db_intuit_client_resp or len(db_intuit_client_resp) == 0:
        return {
            "message": "No Intuit client configuration found",
            "status_code": 500
        }
    client = db_intuit_client_resp[0]
    client_id = client.client_id
    client_secret = client.client_secret

    code = request.query_params.get('code', '')

    realmId = request.query_params.get('realmId', '')

    token_endpoint = get_intuit_discovery_document()
    
    # Check if discovery document returned an error (string instead of dict)
    if isinstance(token_endpoint, str):
        return {
            "message": token_endpoint,
            "status_code": 500
        }

    s = bytes(
        client_id
        + ":"
        + client_secret,
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
        resp = {
                "message": "An error occured: " + resp.text,
                "status_code": 500
            }
        return resp

    if resp.status_code == 200:
        # read_by_realm_id returns Optional[QboAuth] (single object or None), not a list
        auth = qbo_auth_repo.read_by_realm_id(realmId)
        
        resp_json = json.loads(resp.text)
        access_token = resp_json.get('access_token')
        expires_in = resp_json.get('expires_in')
        id_token = resp_json.get('id_token')
        refresh_token = resp_json.get('refresh_token')
        token_type = resp_json.get('token_type')
        x_refresh_token_expires_in = resp_json.get('x_refresh_token_expires_in')

        try:
            if auth:
                qbo_auth_repo.update_by_realm_id(
                    code=code,
                    realm_id=realmId,
                    state=state,
                    token_type=token_type,
                    id_token=id_token,
                    access_token=access_token,
                    expires_in=int(expires_in) if expires_in else 0,
                    refresh_token=refresh_token,
                    x_refresh_token_expires_in=int(x_refresh_token_expires_in) if x_refresh_token_expires_in else 0,
                )
            else:
                qbo_auth_repo.create(
                    code=code,
                    realm_id=realmId,
                    state=state,
                    token_type=token_type,
                    id_token=id_token,
                    access_token=access_token,
                    expires_in=int(expires_in) if expires_in else 0,
                    refresh_token=refresh_token,
                    x_refresh_token_expires_in=int(x_refresh_token_expires_in) if x_refresh_token_expires_in else 0,
                )
            resp = {
                "message": "Oauth 2 Token Endpoint Successful.",
                "status_code": 201
            }
        except Exception as error:
            resp = {
                "message": "An error occured: " + str(error),
                "status_code": 500
            }
        return resp
    
    # Handle any other status codes (not 200 or 400)
    return {
        "message": f"Unexpected status code from token endpoint: {resp.status_code}. Response: {resp.text}",
        "status_code": 500
    }


def connect_intuit_oauth_2_token_endpoint_refresh():
    

    db_intuit_client_resp = qbo_client_repo.read_all()
    client = db_intuit_client_resp[0]
    client_id = client.client_id
    client_secret = client.client_secret
    client_id_and_secret = bytes(client_id + ":" + client_secret, encoding='utf-8')

    token_endpoint = get_intuit_discovery_document()
    #print("token_endpoint: ", token_endpoint)

    db_intuit_auth_resp = qbo_auth_repo.read_all()
    auth = db_intuit_auth_resp[0]
    refresh_token = auth.refresh_token

    url = token_endpoint['token_endpoint']
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(client_id_and_secret).decode()
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    resp = requests.post(url=url, data=data, headers=headers)
    #print("resp status code: ", resp.status_code)
    #print("resp: ", resp.text)

    if resp.status_code == 400:
        return {
                "message": resp.text,
                "status_code": 201
            }

    if resp.status_code == 200:
        resp_json = json.loads(resp.text)
        access_token = resp_json.get('access_token')
        expires_in = resp_json.get('expires_in')
        id_token = resp_json.get('id_token')
        refresh_token = resp_json.get('refresh_token')
        token_type = resp_json.get('token_type')
        x_refresh_token_expires_in = resp_json.get('x_refresh_token_expires_in')

        try:
            #print("Trying to update by realm id")
            #print("auth: ", auth)
            #print("token_type: ", token_type)
            #print("id_token: ", id_token)
            #print("access_token: ", access_token)
            #print("expires_in: ", expires_in)
            #print("refresh_token: ", refresh_token)
            #print("x_refresh_token_expires_in: ", x_refresh_token_expires_in)
            qbo_auth_repo.update_by_realm_id(
                code=auth.code,
                realm_id=auth.realm_id,
                state=auth.state,
                token_type=token_type,
                id_token=auth.id_token,
                access_token=access_token,
                expires_in=int(expires_in) if expires_in else 0,
                refresh_token=refresh_token,
                x_refresh_token_expires_in=int(x_refresh_token_expires_in) if x_refresh_token_expires_in else 0
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


def connect_intuit_oauth_2_token_endpoint_revoke():

    db_intuit_client_resp = qbo_client_repo.read_all()
    client = db_intuit_client_resp[0]
    client_id = client.client_id
    client_secret = client.client_secret
    client_id_and_secret = bytes(client_id + ":" + client_secret, encoding='utf-8')

    revocation_endpoint = get_intuit_discovery_document()

    db_intuit_auth_resp = qbo_auth_repo.read_all()
    auth = db_intuit_auth_resp[0]
    access_token = auth.access_token

    url = revocation_endpoint['revocation_endpoint']
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(client_id_and_secret).decode()
    }
    data = {
        "token": access_token
    }
    resp = requests.post(url=url, data=data, headers=headers)
    
    if resp.status_code == 400:
        return {
            "message": "An error occured: " + resp.text,
            "status_code": 500
        }

    if resp.status_code == 200:

        try:
            qbo_auth_repo.delete_by_realm_id(auth.realm_id)
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
