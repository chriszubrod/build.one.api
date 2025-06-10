"""
Module for Microsoft Graph API Sites.
"""

# python standard library imports
import json
import os
import requests
import sys
from datetime import datetime
from dateutil import tz

# third party imports
from flask import Blueprint, redirect, request, jsonify, current_app, url_for, session, flash

# local imports
from integrations.ms.auth import bus_ms_auth



# At the top of the file
MS_GRAPH_BASE_URL = 'https://graph.microsoft.com/v1.0'
MS_AUTH_BASE_URL = 'https://login.microsoftonline.com'

api_ms_sites_bp = Blueprint('api_ms_sites', __name__, url_prefix='/ms/app', template_folder='templates')


@api_ms_sites_bp.route('/sites', methods=['GET'])
def get_sites():
    """
    Responds to HTTP GET requests to the "/ms/app/sites" route with a JSON response containing
    the user's sites information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API sites endpoint.

    Example:
    >>> get_sites()
    <Response 200 OK>
    """
    secrets = bus_ms_auth.get_ms_auth_by_user_id(session['user']['id'])
    if secrets.success:
        secrets = secrets.data
    else:
        return jsonify({
            "error": "Failed to get Microsoft Graph API integration",
            "status_code": 500
        }), 500
    print(secrets)

    access_token = secrets.access_token
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = 'https://graph.microsoft.com/v1.0/sites?search=*'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_sites_bp.route('/sites/rogersbuildllc', methods=['GET'])
def get_site_by_id():
    """
    Responds to HTTP GET requests to the "/ms/app/sites/" route with a JSON response containing
    the user's site information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API sites endpoint.

    Example:
    >>> get_site_by_id()
    <Response 200 OK>
    """
    secrets = bus_ms_auth.get_ms_auth_by_user_id(session['user']['id'])
    if secrets.success:
        secrets = secrets.data
    else:
        return jsonify({
            "error": "Failed to get Microsoft Graph API integration",
            "status_code": 500
        }), 500
    print(secrets)

    access_token = secrets.access_token
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = f'https://graph.microsoft.com/v1.0/sites/imviokguifqdnyjvkb9idegwrhi.sharepoint.com:/sites/RogersBuildLLC'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )
