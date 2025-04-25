"""
Module for entry line item business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from persistence import (
    pers_bill,
    pers_bill_line_item,
    pers_project,
    pers_sub_cost_code
)


def get_entry_line_items() -> BusinessResponse:
    """
    Retrieves all entry line items from the database.
    """
    pers_entry_line_items_resp = pers_bill_line_item.read_entry_line_items()
    return BusinessResponse(
        data=pers_entry_line_items_resp.data,
        message=pers_entry_line_items_resp.message,
        status_code=pers_entry_line_items_resp.status_code,
        success=pers_entry_line_items_resp.success,
        timestamp=pers_entry_line_items_resp.timestamp
    )


def get_entry_line_item_by_id(entry_line_item_id: int) -> BusinessResponse:
    """
    Retrieves an entry line item by its ID.
    """
    pers_entry_line_item_resp = pers_bill_line_item.read_entry_line_item_by_id(entry_line_item_id)
    return BusinessResponse(
        data=pers_entry_line_item_resp.data,
        message=pers_entry_line_item_resp.message,
        status_code=pers_entry_line_item_resp.status_code,
        success=pers_entry_line_item_resp.success,
        timestamp=pers_entry_line_item_resp.timestamp
    )


def get_entry_line_item_by_entry_id(entry_id: int) -> BusinessResponse:
    """
    Retrieves an entry line item by its entry ID.
    """
    pers_entry_line_item_resp = pers_bill_line_item.\
        read_entry_line_item_by_entry_id(entry_id)
    return BusinessResponse(
        data=pers_entry_line_item_resp.data,
        message=pers_entry_line_item_resp.message,
        status_code=pers_entry_line_item_resp.status_code,
        success=pers_entry_line_item_resp.success,
        timestamp=pers_entry_line_item_resp.timestamp
    )


def post_entry_line_item(
        created_datetime: datetime,
        modified_datetime: datetime,
        description: str,
        units: int,
        rate: float,
        amount: float,
        is_billable: int,
        is_billed: int,
        entry_guid: str,
        sub_cost_code_guid: str,
        project_guid: str
    ) -> BusinessResponse:
    """
    Posts an entry line item to the database.
    """

    # validate description
    if not description or description is None or description == '':
        return BusinessResponse(
            data=None,
            message='Missing Description.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate units
    if not units or units is None or units == '':
        return BusinessResponse(
            data=None,
            message='Missing Units.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate rate
    if not rate or rate is None or rate == '':
        return BusinessResponse(
            data=None,
            message='Missing Rate.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate amount
    if not amount or amount is None or amount == '':
        return BusinessResponse(
            data=None,
            message='Missing Amount.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate is_billable
    if not is_billable or is_billable is None or is_billable == '':
        return BusinessResponse(
            data=None,
            message='Missing Is Billable.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate is_billed
    if not is_billed or is_billed is None or is_billed == '':
        return BusinessResponse(
            data=None,
            message='Missing Is Billed.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate entry guid
    if not entry_guid or entry_guid is None or entry_guid == '':
        return BusinessResponse(
            data=None,
            message='Missing Entry Guid.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate sub_cost_code_guid
    if not sub_cost_code_guid or sub_cost_code_guid is None or sub_cost_code_guid == '':
        return BusinessResponse(
            data=None,
            message='Missing Sub Cost Code Guid.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate project guid
    if not project_guid or project_guid is None or project_guid == '':
        return BusinessResponse(
            data=None,
            message='Missing Project Guid.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # get entry id
    entry_id = None
    read_entry_pers_response = pers_bill.read_entry_by_guid(entry_guid)
    if read_entry_pers_response.success:
        entry_id = read_entry_pers_response.data.id
    else:
        return BusinessResponse(
            data=read_entry_pers_response.data,
            message=read_entry_pers_response.message,
            status_code=read_entry_pers_response.status_code,
            success=read_entry_pers_response.success,
            timestamp=read_entry_pers_response.timestamp
        )

    # get sub cost code id
    sub_cost_code_id = None
    read_sub_cost_code_pers_response = pers_sub_cost_code.\
        read_sub_cost_code_by_guid(sub_cost_code_guid)
    if read_sub_cost_code_pers_response.success:
        sub_cost_code_id = read_sub_cost_code_pers_response.data.id
    else:
        return BusinessResponse(
            data=read_sub_cost_code_pers_response.data,
            message=read_sub_cost_code_pers_response.message,
            status_code=read_sub_cost_code_pers_response.status_code,
            success=read_sub_cost_code_pers_response.success,
            timestamp=read_sub_cost_code_pers_response.timestamp
        )

    # get project id
    project_id = None
    read_project_pers_response = pers_project.read_project_by_guid(project_guid)
    if read_project_pers_response.success:
        project_id = read_project_pers_response.data.id
    else:
        return BusinessResponse(
            data=read_project_pers_response.data,
            message=read_project_pers_response.message,
            status_code=read_project_pers_response.status_code,
            success=read_project_pers_response.success,
            timestamp=read_project_pers_response.timestamp
        )


    # create entry line item object instance
    _entry_line_item = pers_bill_line_item.EntryLineItem(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        description=description,
        units=units,
        rate=rate,
        amount=amount,
        is_billable=is_billable,
        is_billed=is_billed,
        entry_id=entry_id,
        sub_cost_code_id=sub_cost_code_id,
        project_id=project_id
    )

    # create entry line item in database
    post_entry_line_item_pers_response = pers_bill_line_item.\
        create_entry_line_item(_entry_line_item)

    return BusinessResponse(
        data=post_entry_line_item_pers_response.data,
        message=post_entry_line_item_pers_response.message,
        status_code=post_entry_line_item_pers_response.status_code,
        success=post_entry_line_item_pers_response.success,
        timestamp=post_entry_line_item_pers_response.timestamp
    )
