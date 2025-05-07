"""
Module for bill business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz
import uuid
import base64

# local imports
from business import bus_bill_line_item_attachment
from business.bus_response import BusinessResponse
from utils.function_help import clean_text_for_db
from persistence import (
    pers_bill,
    pers_bill_line_item,
    pers_bill_line_item_attachment,
    pers_project,
    pers_sub_cost_code,
    pers_vendor
)


def get_bills() -> BusinessResponse:
    """
    Retrieves all bills from the database.
    """
    pers_bills_resp = pers_bill.read_bills()
    return BusinessResponse(
        data=pers_bills_resp.data,
        message=pers_bills_resp.message,
        status_code=pers_bills_resp.status_code,
        success=pers_bills_resp.success,
        timestamp=pers_bills_resp.timestamp
    )


def get_bill_by_guid(bill_guid: str) -> BusinessResponse:
    """
    Retrieves a bill by its GUID.
    """
    pers_bill_resp = pers_bill.read_bill_by_guid(bill_guid)
    return BusinessResponse(
        data=pers_bill_resp.data,
        message=pers_bill_resp.message,
        status_code=pers_bill_resp.status_code,
        success=pers_bill_resp.success,
        timestamp=pers_bill_resp.timestamp
    )


def get_bills_by_vendor_id(vendor_id: int) -> BusinessResponse:
    """
    Retrieves all bills by vendor id from the database.
    """
    pers_bills_resp = pers_bill.read_bills_by_vendor_id(vendor_id)
    return BusinessResponse(
        data=pers_bills_resp.data,
        message=pers_bills_resp.message,
        status_code=pers_bills_resp.status_code,
        success=pers_bills_resp.success,
        timestamp=pers_bills_resp.timestamp
    )


def post_bill_with_line_items_and_attachments(
        created_datetime: datetime,
        modified_datetime: datetime,
        vendor_guid: str,
        number: str,
        date: datetime,
        line_items: list,
        files: list
    ) -> BusinessResponse:
    """
    Posts a bill with line items to the database.
    """
    ###############################################################################################
    # Validate the Bill
    # Validate the vendor guid
    if (not vendor_guid
        or vendor_guid == ''
        or vendor_guid is None):
        return BusinessResponse(
            data=None,
            message='Vendor guid is required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Validate the number
    if (not number
        or number == ''
        or number is None):
        return BusinessResponse(
            data=None,
            message='Number is required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Validate the date
    if (not date
        or date is None
        or date == ''):
        return BusinessResponse(
            data=None,
            message='Date is required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Get the vendor id
    vendor_pers_resp = pers_vendor.read_vendor_by_guid(vendor_guid)
    if not vendor_pers_resp.success:
        return BusinessResponse(
            data=None,
            message='Vendor not found',
            status_code=404,
            success=False,
            timestamp=datetime.now()
        )
    # Get the vendor id
    vendor_id = vendor_pers_resp.data.id


    #print(f'Vendor id: {vendor_id}')
    #print(f'Number: {number}')
    #print(f'Date: {date}')

    ###############################################################################################
    # Validate the Line Items
    if (not line_items
        or line_items == []
        or line_items is None):
        return BusinessResponse(
            data=None,
            message='Line items are required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    _tvp_line_items = []
    _tvp_attachments = []
    _row_keys = []
    _attachment_line_item_names = []
    _bill_line_items = []
    total = 0
    for index, line_item in enumerate(line_items):
        #print(f'Line item: {line_item}')
        # Validate the sub-cost code
        if (not line_item['sub-cost-code']
            or line_item['sub-cost-code'] == ''
            or line_item['sub-cost-code'] is None):
            return BusinessResponse(
                data=None,
                message='Sub-cost code is required',
                status_code=400,
                success=False,
                timestamp=datetime.now()
            )

        # Validate the amount
        if (not line_item['amount']
            or line_item['amount'] == ''
            or line_item['amount'] is None):
            return BusinessResponse(
                data=None,
                message='Amount is required',
                status_code=400,
                success=False,
                timestamp=datetime.now()
            )

        # Validate the description
        if (not line_item['description']
            or line_item['description'] == ''
            or line_item['description'] is None):
            return BusinessResponse(
                data=None,
                message='Description is required',
                status_code=400,
                success=False,
                timestamp=datetime.now()
            )

        # Validate the units
        if (not line_item['units']
            or line_item['units'] == ''
            or line_item['units'] is None):
            return BusinessResponse(
                data=None,
                message='Units are required',
                status_code=400,
                success=False,
                timestamp=datetime.now()
            )

        # Validate the rate
        if (not line_item['rate']
            or line_item['rate'] == ''
            or line_item['rate'] is None):
            return BusinessResponse(
                data=None,
                message='Rate is required',
                status_code=400,
                success=False,
                timestamp=datetime.now()
            )

        # Validate the is-billable
        if (not line_item['is-billable']
            or line_item['is-billable'] == ''
            or line_item['is-billable'] is None):
            return BusinessResponse(
                data=None,
                message='Is billable is required',
                status_code=400,
                success=False,
                timestamp=datetime.now()
            )

        # Validate the project-guid
        if (not line_item['project']
            or line_item['project'] == ''
            or line_item['project'] is None):
            return BusinessResponse(
                data=None,
                message='Project is required',
                status_code=400,
                success=False,
                timestamp=datetime.now()
            )

        # Get the sub-cost-code id
        sub_cost_code_pers_resp = pers_sub_cost_code.\
            read_sub_cost_code_by_guid(line_item['sub-cost-code'])
        if not sub_cost_code_pers_resp.success:
            return BusinessResponse(
                data=None,
                message='Sub-cost code not found',
                status_code=404,
                success=False,
                timestamp=datetime.now()
            )
        sub_cost_code_id = sub_cost_code_pers_resp.data.id


        # Get the project id
        project_pers_resp = pers_project.read_project_by_guid(line_item['project'])
        if not project_pers_resp.success:
            return BusinessResponse(
                data=None,
                message='Project not found',
                status_code=404,
                success=False,
                timestamp=datetime.now()
            )
        project_id = project_pers_resp.data.id

        row_key = uuid.uuid4()

        _tvp_line_items.append((
            str(row_key),
            created_datetime,
            modified_datetime,
            line_item['description'],
            line_item['units'],
            line_item['rate'],
            line_item['amount'],
            line_item['is-billable'],
            False, #is_billed
            sub_cost_code_id,
            project_id
        ))

        # Add attachment if it exists
        if index < len(files) and files[index]:
            attachment = files[index]
            project_abbr = project_pers_resp.data.abbreviation
            sub_cost_code_number = sub_cost_code_pers_resp.data.number
            file_extension = attachment['type'].split('/')[1]

            attachment_name = f'{project_abbr} - {vendor_pers_resp.data.name} - {number} - {line_item["description"]} - {sub_cost_code_number} - ${float(line_item["amount"]):.2f}.{file_extension}'

            _tvp_attachments.append((
                str(row_key),
                created_datetime,
                modified_datetime,
                attachment_name,
                attachment['size'],
                attachment['type'],
                base64.b64decode(attachment['content'])
            ))

        total += line_item['amount']

    #print(f'Bill line items: {_bill_line_items}')

    _bill = pers_bill.Bill(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        number=number,
        date=date,
        amount=total,
        vendor_id=vendor_id
    )

    create_bill_resp = pers_bill.create_bill_with_line_items_and_attachments(
        bill=_bill,
        line_items=_tvp_line_items,
        attachments=_tvp_attachments
    )
    return BusinessResponse(
        data=create_bill_resp.data,
        message=create_bill_resp.message,
        status_code=create_bill_resp.status_code,
        success=create_bill_resp.success,
        timestamp=create_bill_resp.timestamp
    )
