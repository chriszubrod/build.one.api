"""
Module for intuit vendor business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports



# local imports
from shared.response import BusinessResponse
from integrations.intuit.persistence import pers_intuit_vendor


def get_intuit_vendors() -> BusinessResponse:
    """
    Retrieves all intuit vendors from the database.
    """
    read_intuit_vendors_pers_response = pers_intuit_vendor.read_intuit_vendors()

    return BusinessResponse(
        data=read_intuit_vendors_pers_response.data,
        message=read_intuit_vendors_pers_response.message,
        success=read_intuit_vendors_pers_response.success,
        status_code=read_intuit_vendors_pers_response.status_code,
        timestamp=read_intuit_vendors_pers_response.timestamp
    )


def get_intuit_vendor_by_id(intuit_vendor_id: int) -> BusinessResponse:
    """
    Retrieves a intuit vendor from the database by id.
    """
    pers_read_intuit_vendor_resp = pers_intuit_vendor.\
        read_intuit_vendor_by_id(vendor_id=intuit_vendor_id)

    return BusinessResponse(
        data=pers_read_intuit_vendor_resp.data,
        message=pers_read_intuit_vendor_resp.message,
        success=pers_read_intuit_vendor_resp.success,
        status_code=pers_read_intuit_vendor_resp.status_code,
        timestamp=pers_read_intuit_vendor_resp.timestamp
    )


