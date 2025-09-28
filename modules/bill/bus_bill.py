"""
Module for bill business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz
import asyncio
import uuid
import base64
import sys
import threading
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# third party imports
from flask import session

# local imports
from integrations.ms.persistence import (
    pers_ms_sharepoint_folder,
    pers_ms_sharepoint_site,
)
from integrations.ms.auth import pers_ms_auth
from integrations.map import pers_map_project_sharepoint_folder
from integrations.ms.sites import pers_ms_sites
from modules.bill import (
    bus_bill_line_item_attachment,
    pers_bill,
    pers_bill_line_item,
    pers_bill_line_item_attachment
)
from modules.project import pers_project
from modules.sub_cost_code import pers_sub_cost_code
from shared.response import BusinessResponse
from utils.function_help import clean_text_for_db
from modules.vendor import (
    pers_vendor
)
from integrations.ms.services import bus_ms_sharepoint_sync, bus_ms_budget_tracker_push
import bus_intuit_bill_sync


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


def get_bill_by_id(bill_id: str) -> BusinessResponse:
    """
    Retrieves a bill by its ID.
    """
    pers_bill_resp = pers_bill.read_bill_by_id(bill_id)
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


def get_bill_by_last_created() -> BusinessResponse:
    """
    Retrieves all bills by created datetime from the database.
    """
    pers_bills_resp = pers_bill.read_bill_by_last_created()
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
        if (not line_item['line-item-amount']
            or line_item['line-item-amount'] == ''
            or line_item['line-item-amount'] is None):
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
            1, #line_item['units']
            line_item['line-item-amount'], #line_item['rate'],
            line_item['line-item-amount'],
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
            file_extension = attachment['file-type'].split('/')[1]

            attachment_name = f'{project_abbr} - {vendor_pers_resp.data.name} - {number} - {line_item["description"]} - {sub_cost_code_number} - ${float(line_item["line-item-amount"]):.2f} - {date.strftime("%m-%d-%Y")}.{file_extension}'

            _tvp_attachments.append((
                str(row_key),
                created_datetime,
                modified_datetime,
                attachment_name,
                attachment['file-size'],
                attachment['file-type'],
                base64.b64decode(attachment['file-content'])
            ))

        total += line_item['line-item-amount']

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

    # If create_bill_resp.success, then sync the bill to SharePoint
    if create_bill_resp.success:

        # Sync the bill to SharePoint in a separate thread
        def run_sharepoint_sync():
            bus_ms_sharepoint_sync.main_sharepoint_sync_function()

        # Sync the bill to the budget tracker in a separate thread
        def run_budget_tracker_sync():
            bus_ms_budget_tracker_push.main_budget_tracker_push_function()

        # Sync the bill to Intuit in a separate thread
        def run_intuit_sync():
            bus_bill_intuit_bill_sync.main_intuit_bill_sync_function()

        # Create the sync threads
        sync_threads = [
            threading.Thread(target=run_sharepoint_sync, daemon=True),
            threading.Thread(target=run_budget_tracker_sync, daemon=True),
            threading.Thread(target=run_intuit_sync, daemon=True)
        ]

        for thread in sync_threads:
            thread.start()


    return BusinessResponse(
        data=create_bill_resp.data,
        message=create_bill_resp.message,
        status_code=create_bill_resp.status_code,
        success=create_bill_resp.success,
        timestamp=create_bill_resp.timestamp
    )


def patch_bill_with_line_items_and_attachments(
        bill_guid: str,
        vendor_guid: str,
        number: str,
        date: datetime,
        line_items: list,
        files: list
    ) -> BusinessResponse:
    """
    Patches a bill with line items to the database.
    """
    ###############################################################################################
    # Validate the Bill
    # Validate the bill guid
    if (not bill_guid
        or bill_guid == ''
        or bill_guid is None):
        return BusinessResponse(
            data=None,
            message='Bill guid is required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

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
    vendor_id = vendor_pers_resp.data.id


    # Get the bill
    db_bill = None
    pers_bill_resp = pers_bill.read_bill_by_guid(bill_guid)
    if pers_bill_resp.success:
        db_bill = pers_bill_resp.data
    else:
        return BusinessResponse(
            data=None,
            message='Bill not found',
            status_code=404,
            success=False,
            timestamp=datetime.now()
        )

    















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
        if (not line_item['line-item-amount']
            or line_item['line-item-amount'] == ''
            or line_item['line-item-amount'] is None):
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


        # Get the bill line item by guid
        db_bill_line_item = None
        pers_bill_line_item_resp = pers_bill_line_item.read_bill_line_item_by_guid(
            bill_line_item_guid=line_item['line-item-guid']
        )
        if pers_bill_line_item_resp.success:
            db_bill_line_item = pers_bill_line_item_resp.data
            db_bill_line_item.description = line_item['description']
            db_bill_line_item.rate = line_item['line-item-amount']
            db_bill_line_item.amount = line_item['line-item-amount']
            db_bill_line_item.is_billable = line_item['is-billable']
            db_bill_line_item.sub_cost_code_id = sub_cost_code_id
            db_bill_line_item.project_id = project_id
            pers_update_bill_line_item_resp = pers_bill_line_item.update_bill_line_item(
                bill_line_item=db_bill_line_item
            )
            if pers_update_bill_line_item_resp.success:
                attachment = files[index]
                project_abbr = project_pers_resp.data.abbreviation
                sub_cost_code_number = sub_cost_code_pers_resp.data.number
                file_extension = attachment['file-type'].split('/')[1]

                attachment_name = f'{project_abbr} - {vendor_pers_resp.data.name} - {number} - {line_item["description"]} - {sub_cost_code_number} - ${float(line_item["line-item-amount"]):.2f} - {date.strftime("%m-%d-%Y")}.{file_extension}'

                db_attachment_resp = pers_bill_line_item_attachment.read_bill_line_item_attachment_by_bill_line_item_id(
                    bill_line_item_id=db_bill_line_item.id
                )
                if db_attachment_resp.success and db_attachment_resp.data:
                    db_attachment = db_attachment_resp.data[0]  # ← Get the first attachment from the list
                    db_attachment.name = attachment_name
                    db_attachment.size = attachment['file-size']
                    db_attachment.type = attachment['file-type']
                    db_attachment.content = base64.b64decode(attachment['file-content'])
                    db_attachment.bill_line_item_id = db_bill_line_item.id
        
                    pers_update_bill_line_item_attachment_resp = pers_bill_line_item_attachment.update_bill_line_item_attachment(
                        bill_line_item_attachment=db_attachment
                    )
                    if not pers_update_bill_line_item_attachment_resp.success:
                        return BusinessResponse(
                            data=None,
                            message='Bill line item attachment not updated',
                            status_code=400,
                            success=False,
                            timestamp=datetime.now()
                        )
            if not pers_update_bill_line_item_resp.success:
                return BusinessResponse(
                    data=None,
                    message='Bill line item not updated',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        else:
            new_bill_line_item = pers_bill_line_item.BillLineItem(
                description=line_item['description'],
                units=1,
                rate=line_item['line-item-amount'],
                amount=line_item['line-item-amount'],
                is_billable=line_item['is-billable'],
                is_billed=False,
                bill_id=db_bill.id,
                sub_cost_code_id=sub_cost_code_id,
                project_id=project_id
            )
            pers_create_bill_line_item_resp = pers_bill_line_item.create_bill_line_item(
                bill_line_item=new_bill_line_item
            )
            if pers_create_bill_line_item_resp.success:
                attachment = files[index]
                project_abbr = project_pers_resp.data.abbreviation
                sub_cost_code_number = sub_cost_code_pers_resp.data.number
                file_extension = attachment['file-type'].split('/')[1]

                attachment_name = f'{project_abbr} - {vendor_pers_resp.data.name} - {number} - {line_item["description"]} - {sub_cost_code_number} - ${float(line_item["line-item-amount"]):.2f} - {date.strftime("%m-%d-%Y")}.{file_extension}'

                new_attachment = pers_bill_line_item_attachment.BillLineItemAttachment(
                    name=attachment_name,
                    size=attachment['file-size'],
                    type=attachment['file-type'],
                    content=base64.b64decode(attachment['file-content']),
                    bill_line_item_id=pers_create_bill_line_item_resp.data.id
                )
                pers_create_bill_line_item_attachment_resp = pers_bill_line_item_attachment.create_bill_line_item_attachment(
                    bill_line_item_attachment=new_attachment
                )
                if not pers_create_bill_line_item_attachment_resp.success:
                    return BusinessResponse(
                        data=None,
                        message='Bill line item attachment not created',
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
            if not pers_create_bill_line_item_resp.success:
                return BusinessResponse(
                    data=None,
                    message='Bill line item not created',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )

        total += line_item['line-item-amount']





    db_bill.vendor_id = vendor_id
    db_bill.number = number
    db_bill.date = date
    db_bill.amount = total

    pers_update_bill_resp = pers_bill.update_bill(bill=db_bill)
    if not pers_update_bill_resp.success:
        return BusinessResponse(
            data=None,
            message='Bill not updated',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )


    return BusinessResponse(
        data=pers_update_bill_resp.data,
        message=pers_update_bill_resp.message,
        status_code=pers_update_bill_resp.status_code,
        success=pers_update_bill_resp.success,
        timestamp=pers_update_bill_resp.timestamp
    )
