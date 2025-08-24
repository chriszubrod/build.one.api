"""
Module for intuit item business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports



# local imports
from business.bus_response import BusinessResponse
from integrations.intuit import pers_intuit_item


def get_intuit_items() -> BusinessResponse:
    """
    Retrieves all intuit items from the database.
    """
    read_intuit_items_pers_response = pers_intuit_item.read_intuit_items()

    return BusinessResponse(
        data=read_intuit_items_pers_response.data,
        message=read_intuit_items_pers_response.message,
        success=read_intuit_items_pers_response.success,
        status_code=read_intuit_items_pers_response.status_code,
        timestamp=read_intuit_items_pers_response.timestamp
    )


def get_intuit_item_by_id(intuit_item_id: int) -> BusinessResponse:
    """
    Retrieves a intuit item from the database by id.
    """
    pers_read_intuit_item_resp = pers_intuit_item.\
        read_intuit_item_by_id(item_id=intuit_item_id)

    return BusinessResponse(
        data=pers_read_intuit_item_resp.data,
        message=pers_read_intuit_item_resp.message,
        success=pers_read_intuit_item_resp.success,
        status_code=pers_read_intuit_item_resp.status_code,
        timestamp=pers_read_intuit_item_resp.timestamp
    )


