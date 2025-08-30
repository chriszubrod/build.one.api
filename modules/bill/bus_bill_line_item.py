"""
Module for entry line item business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from shared.response import BusinessResponse
from modules.project import pers_project
from modules.sub_cost_code import (
    pers_sub_cost_code
)
from modules.bill import (
    pers_bill_line_item
)


def get_bill_line_items() -> BusinessResponse:
    """
    Retrieves all bill line items from the database.
    """
    pers_bill_line_items_resp = pers_bill_line_item.read_bill_line_items()
    return BusinessResponse(
        data=pers_bill_line_items_resp.data,
        message=pers_bill_line_items_resp.message,
        status_code=pers_bill_line_items_resp.status_code,
        success=pers_bill_line_items_resp.success,
        timestamp=pers_bill_line_items_resp.timestamp
    )


def get_bill_line_item_by_id(bill_line_item_id: int) -> BusinessResponse:
    """
    Retrieves a bill line item by its ID.
    """
    pers_bill_line_item_resp = pers_bill_line_item.read_bill_line_item_by_id(bill_line_item_id)
    return BusinessResponse(
        data=pers_bill_line_item_resp.data,
        message=pers_bill_line_item_resp.message,
        status_code=pers_bill_line_item_resp.status_code,
        success=pers_bill_line_item_resp.success,
        timestamp=pers_bill_line_item_resp.timestamp
    )


def get_bill_line_item_by_bill_id(bill_id: int) -> BusinessResponse:
    """
    Retrieves a bill line item by its bill ID.
    """
    pers_bill_line_item_resp = pers_bill_line_item.\
        read_bill_line_item_by_bill_id(bill_id)
    return BusinessResponse(
        data=pers_bill_line_item_resp.data,
        message=pers_bill_line_item_resp.message,
        status_code=pers_bill_line_item_resp.status_code,
        success=pers_bill_line_item_resp.success,
        timestamp=pers_bill_line_item_resp.timestamp
    )


def post_bill_line_item(
        created_datetime: datetime,
        modified_datetime: datetime,
        description: str,
        units: int,
        rate: float,
        amount: float,
        is_billable: int,
        is_billed: int,
        bill_guid: str,
        sub_cost_code_guid: str,
        project_guid: str
    ) -> BusinessResponse:
    """
    Posts a bill line item to the database.
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

    # validate bill guid
    if not bill_guid or bill_guid is None or bill_guid == '':
        return BusinessResponse(
            data=None,
            message='Missing Bill Guid.',
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

    # get bill id
    bill_id = None
    read_bill_pers_response = pers_bill.read_bill_by_guid(bill_guid)
    if read_bill_pers_response.success:
        bill_id = read_bill_pers_response.data.id
    else:
        return BusinessResponse(
            data=read_bill_pers_response.data,
            message=read_bill_pers_response.message,
            status_code=read_bill_pers_response.status_code,
            success=read_bill_pers_response.success,
            timestamp=read_bill_pers_response.timestamp
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


    # create bill line item object instance
    _bill_line_item = pers_bill_line_item.BillLineItem(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        description=description,
        units=units,
        rate=rate,
        amount=amount,
        is_billable=is_billable,
        is_billed=is_billed,
        bill_id=bill_id,
        sub_cost_code_id=sub_cost_code_id,
        project_id=project_id
    )

    # create bill line item in database
    post_bill_line_item_pers_response = pers_bill_line_item.\
        create_bill_line_item(_bill_line_item)

    return BusinessResponse(
        data=post_bill_line_item_pers_response.data,
        message=post_bill_line_item_pers_response.message,
        status_code=post_bill_line_item_pers_response.status_code,
        success=post_bill_line_item_pers_response.success,
        timestamp=post_bill_line_item_pers_response.timestamp
    )
