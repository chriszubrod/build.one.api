"""
Module for Microsoft Graph API Authentication.
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


api_ms_drives_bp = Blueprint('api_ms_drives', __name__, url_prefix='/ms/app', template_folder='templates')


@api_ms_drives_bp.route('/drive', methods=['GET'])
def get_drive():
    """
    Responds to HTTP GET requests to the "/ms/app/drive" route with a JSON response containing
    the user's drive information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drive endpoint.

    Example:
    >>> get_drive()
    <Response 200 OK>
    """
    drive_secrets = current_app.config['SECRETS']

    access_token = drive_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = 'https://graph.microsoft.com/v1.0/me/drive'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_drives_bp.route('/drives', methods=['GET'])
def get_drives():
    """
    Responds to HTTP GET requests to the "/ms/app/drives" route with a JSON response containing
    the user's drives information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drives endpoint.

    Example:
    >>> get_drives()
    <Response 200 OK>
    """
    drives_secrets = current_app.config['SECRETS']

    access_token = drives_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = 'https://graph.microsoft.com/v1.0/me/drives'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_drives_bp.route('/drives/<drive_id>', methods=['GET'])
def get_drive_by_id(drive_id):
    """
    Responds to HTTP GET requests to the "/ms/app/drives/<drive_id>" route with a JSON response
    containing the information of a specific drive.

    Args:
    drive_id (str): The ID of the drive.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drives endpoint.

    Example:
    >>> get_drive_by_id('12345')
    <Response 200 OK>
    """
    drives_secrets = current_app.config['SECRETS']

    access_token = drives_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_drives_bp.route('/sites/<site_id>/drive/root/children', methods=['GET'])
def get_sites_drive_root_children(site_id):
    """
    Responds to HTTP GET requests to the "/ms/app/sites/<site_id>/drive/root/children" route with a JSON response containing
    the user's sites information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API sites endpoint.

    Example:
    >>> get_sites_drive_root_children()
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
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root/children'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_drives_bp.route('/sites/<site_id>/drives', methods=['GET'])
def get_site_drives(site_id):
    """
    Responds to HTTP GET requests to the "/ms/app/sites/<site_id>/drives" route with a JSON response containing
    the user's sites information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API sites endpoint.

    Example:
    >>> get_sites_drives()
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
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drives'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )
