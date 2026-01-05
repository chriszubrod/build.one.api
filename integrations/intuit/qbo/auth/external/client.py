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

    INTUIT_STATE['sent-state'] = ''.join(random.choices(string.ascii_lowercase + string.digits, k=30))

    auth_endpoint = get_intuit_discovery_document()

    endpoint = str(
        auth_endpoint['authorization_endpoint'] +
        "?" +
        "client_id=" + client_id +
        "&scope=com.intuit.quickbooks.accounting%20openid%20email%20profile%20address%20phone" +
        "&redirect_uri=https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/intuit/qbo/auth/request/callback" +
        "&response_type=code" +
        "&state=" + INTUIT_STATE['sent-state'] +
        "&claims=%7B%22id_token%22%3A%7B%22realmId%22%3Anull%7D%7D"
    )

    resp = {
        "message": endpoint,
        "status_code": 201
    }

    return resp


def connect_intuit_oauth_2_token_endpoint(request: Request):
    now = datetime.now()

    INTUIT_STATE['received-state'] = request.args.get('state', '')

    db_intuit_client_resp = qbo_client_repo.read_all()
    client = db_intuit_client_resp[0]
    client_id = client.client_id
    client_secret = client.client_secret

    code = request.args.get('code', '')

    realmId = request.args.get('realmId', '')

    token_endpoint = get_intuit_discovery_document()

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
        db_intuit_auth_resp = qbo_auth_repo.read_by_realm_id(realmId)

        auth = None
        if db_intuit_auth_resp:
            auth = db_intuit_auth_resp[0]
        
        resp_json = json.loads(resp.text)
        access_token = resp_json.get('access_token')
        expires_in = resp_json.get('expires_in')
        id_token = resp_json.get('id_token')
        refresh_token = resp_json.get('refresh_token')
        token_type = resp_json.get('token_type')
        x_refresh_token_expires_in = resp_json.get('x_refresh_token_expires_in')

        try:
            if auth:
                auth = qbo_auth_repo.update_by_realm_id(
                    code=code,
                    realm_id=realmId,
                    state=INTUIT_STATE['received-state'],
                    token_type=token_type,
                    id_token=id_token,
                    access_token=access_token,
                    expires_in=expires_in,
                    refresh_token=refresh_token,
                    x_refresh_token_expires_in=x_refresh_token_expires_in,
                )
            else:
                auth = qbo_auth_repo.create(
                    code=code,
                    realm_id=realmId,
                    state=INTUIT_STATE['received-state'],
                    token_type=token_type,
                    id_token=id_token,
                    access_token=access_token,
                    expires_in=str(expires_in),
                    refresh_token=refresh_token,
                    x_refresh_token_expires_in=str(x_refresh_token_expires_in),
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


def connect_intuit_oauth_2_token_endpoint_refresh():
    now = datetime.now()

    pers_read_qbo_client_resp = pers_intuit_client.read_db_intuit_client()
    print(f'Pers DB Message: {pers_read_qbo_client_resp.get("message")}')
    if pers_read_qbo_client_resp.get("status_code") == 201:
        client = pers_read_qbo_client_resp.get("message")
        client_id_and_secret = bytes(client.__getattribute__('ClientID') + ":" + client.__getattribute__('ClientSecret'), encoding='utf-8')

    token_endpoint = get_intuit_discovery_document()

    pers_read_auth_resp = pers_intuit_auth.read_db_intuit_auth()
    if pers_read_auth_resp.get("status_code") == 201:
        auth = pers_read_auth_resp.get("message")

    url = token_endpoint['token_endpoint']
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(client_id_and_secret).decode()
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": auth.__getattribute__('RefreshToken')
    }
    resp = requests.post(url=url, data=data, headers=headers)
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

        pers_update_auth_refresh_resp = pers_intuit_auth.update_db_intuit_auth_by_authguid(
            now=now,
            tokentype=token_type,
            idtoken=id_token,
            accesstoken=access_token,
            expiresin=str(expires_in),
            refreshtoken=refresh_token,
            xrefreshtokenexpiresin=str(x_refresh_token_expires_in),
            authguid=auth.__getattribute__('AuthGUID')
        )

        if pers_update_auth_refresh_resp.get("status_code") == 201:
            return {
                "message": resp.text,
                "status_code": 201
            }

    return {
        "message": "An error has occured during the refresh phase.",
        "status_code": 500
    }


def connect_intuit_oauth_2_token_endpoint_revoke():

    pers_read_qbo_client_resp = pers_intuit_client.read_db_intuit_client()

    if pers_read_qbo_client_resp.get("status_code") == 201:
        pers_read_qbo_client_resp = pers_read_qbo_client_resp.get("message")
        s = bytes(pers_read_qbo_client_resp.__getattribute__('ClientID') + ":" + pers_read_qbo_client_resp.__getattribute__('ClientSecret'), encoding='utf-8')

    revocation_endpoint = get_intuit_discovery_document()

    pers_read_auth_resp = pers_intuit_auth.read_db_intuit_auth()
    if pers_read_auth_resp.get("status_code") == 201:
        auth = pers_read_auth_resp.get("message")

    url = revocation_endpoint['revocation_endpoint']
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(s).decode()
    }
    data = {
        "token": auth.__getattribute__('AccessToken')
    }
    resp = requests.post(url=url, data=data, headers=headers)
    if resp.status_code == 400:
        return "An error occured: " + resp.text

    if resp.status_code == 200:

        pers_delete_auth_by_authguid_resp = pers_intuit_auth.delete_auth_by_authguid(authguid=auth.__getattribute__('AuthGUID'))
        resp = {
            "message": pers_delete_auth_by_authguid_resp.text,
            "status_code": 201
        }
        return resp

