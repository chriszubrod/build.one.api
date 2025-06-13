"""
Module for Microsoft Graph API Picker business layer.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz
import json

# third party imports


# local imports
from business.bus_response import BusinessResponse
from integrations.ms.drives import api_ms_drives, pers_ms_drives


def get_ms_drives() -> BusinessResponse:
    """
    Retrieves all drives from the database.
    """
    read_drives_pers_response = pers_ms_drives.read_ms_drives()

    return BusinessResponse(
        data=read_drives_pers_response.data,
        message=read_drives_pers_response.message,
        status_code=read_drives_pers_response.status_code,
        success=read_drives_pers_response.success,
        timestamp=read_drives_pers_response.timestamp
    )


def get_ms_drives_from_graph(site_id: str) -> BusinessResponse:
    """
    Retrieves all drives from the Microsoft Graph API.
    """
    get_graph_drives_response = api_ms_drives.get_site_drives(site_id)
    drives_data = get_graph_drives_response.get_json()  # Get JSON data from Flask Response
    drives_response_json = drives_data.get('response_json')
    drives_values = drives_response_json.get('value')
    drives = []
    for drive in drives_values:
        ms_drive = pers_ms_drives.MsDrive()
        ms_drive.description = drive.get('description')
        ms_drive.drive_id = drive.get('id')
        ms_drive.drive_type = drive.get('driveType')
        ms_drive.name = drive.get('name')
        ms_drive.web_url = drive.get('webUrl')
        drives.append(ms_drive)

    return BusinessResponse(
        data=drives,
        message="Drives retrieved from Microsoft Graph API",
        status_code=200,
        success=True,
        timestamp=datetime.now()
    )


def get_ms_drives_children_from_graph(drive_id: str) -> BusinessResponse:
    """
    Retrieves all drives children from the Microsoft Graph API.
    """
    get_graph_drives_response = api_ms_drives.get_drive_by_id_root_children(drive_id)
    drives_data = get_graph_drives_response.get_json()
    drives_response_json = drives_data.get('response_json')
    drives_values = drives_response_json.get('value')
    print(drives_values)
    drives_children = []
    for drive in drives_values:
        ms_drive_item = pers_ms_drives.MsDriveItem()
        ms_drive_item.c_tag = drive.get('cTag')
        ms_drive_item.created_datetime = drive.get('createdDateTime')
        ms_drive_item.last_modified_datetime = drive.get('lastModifiedDateTime')
        ms_drive_item.e_tag = drive.get('eTag')
        ms_drive_item.drive_item_id = drive.get('id')
        ms_drive_item.drive_item_type = drive.get('driveType')
        ms_drive_item.name = drive.get('name')
        ms_drive_item.size = drive.get('size')
        ms_drive_item.web_url = drive.get('webUrl')
        drives_children.append(ms_drive_item)

    return BusinessResponse(
        data=drives_children,
        message="Drives children retrieved from Microsoft Graph API",
        status_code=200,
        success=True,
        timestamp=datetime.now()
    )



def get_ms_drives_items_children_from_graph(drive_id: str, item_id: str) -> BusinessResponse:
    """
    Retrieves all drives children from the Microsoft Graph API.
    """
    #print(drive_id)
    #print(item_id)
    get_graph_drives_response = api_ms_drives.get_drive_items_children(drive_id, item_id)
    print(get_graph_drives_response)
    #drives_data = get_graph_drives_response.get_json()
    drives_response_json = get_graph_drives_response.get('response_json')
    drives_values = drives_response_json.get('value')
    drives_children = []
    for drive in drives_values:
        ms_drive_item = pers_ms_drives.MsDriveItem()
        ms_drive_item.c_tag = drive.get('cTag')
        ms_drive_item.created_datetime = drive.get('createdDateTime')
        ms_drive_item.last_modified_datetime = drive.get('lastModifiedDateTime')
        ms_drive_item.e_tag = drive.get('eTag')
        ms_drive_item.drive_item_id = drive.get('id')
        ms_drive_item.name = drive.get('name')
        ms_drive_item.size = drive.get('size')
        ms_drive_item.web_url = drive.get('webUrl')
        drives_children.append(ms_drive_item)

    return BusinessResponse(
        data=drives_children,
        message="Drives children retrieved from Microsoft Graph API",
        status_code=200,
        success=True,
        timestamp=datetime.now()
    )
