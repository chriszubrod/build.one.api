"""
Module for Microsoft Graph API Picker business layer.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports


# local imports
from shared.response import BusinessResponse
from integrations.ms.sites import pers_ms_sites


def get_ms_sites() -> BusinessResponse:
    """
    Retrieves all sites from the database.
    """
    read_sites_pers_response = pers_ms_sites.read_ms_sites()

    return BusinessResponse(
        data=read_sites_pers_response.data,
        message=read_sites_pers_response.message,
        status_code=read_sites_pers_response.status_code,
        success=read_sites_pers_response.success,
        timestamp=read_sites_pers_response.timestamp
    )
