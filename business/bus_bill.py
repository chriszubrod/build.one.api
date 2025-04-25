"""
Module for entry business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business import bus_attachment
from business.bus_response import BusinessResponse
from utils.function_help import clean_text_for_db
from persistence import (
    pers_attachment,
    pers_bill,
    pers_bill_line_item,
    pers_entry_type,
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


def get_entry_by_guid(entry_guid: str) -> BusinessResponse:
    """
    Retrieves an entry by its GUID.
    """
    pers_entry_resp = pers_bill.read_entry_by_guid(entry_guid)
    return BusinessResponse(
        data=pers_entry_resp.data,
        message=pers_entry_resp.message,
        status_code=pers_entry_resp.status_code,
        success=pers_entry_resp.success,
        timestamp=pers_entry_resp.timestamp
    )


def get_entries_by_entry_type_id(entry_type_id: int) -> BusinessResponse:
    """
    Retrieves all entries by entry type id from the database.
    """
    pers_entries_resp = pers_bill.read_entries_by_entry_type_id(entry_type_id)
    return BusinessResponse(
        data=pers_entries_resp.data,
        message=pers_entries_resp.message,
        status_code=pers_entries_resp.status_code,
        success=pers_entries_resp.success,
        timestamp=pers_entries_resp.timestamp
    )


def post_entry_with_line_items_and_attachment(
        created_datetime: datetime,
        modified_datetime: datetime,
        vendor_guid: str,
        number: str,
        date: datetime,
        entry_type: str,
        line_items: list,
        files: list
    ) -> BusinessResponse:
    """
    Posts an entry with line items to the database.
    """
    ###############################################################################################
    # Validate the Entry
    # Validate the vendor guid
    if not vendor_guid or vendor_guid == '' or vendor_guid is None:
        return BusinessResponse(
            data=None,
            message='Vendor guid is required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Validate the number
    if not number or number == '' or number is None:
        return BusinessResponse(
            data=None,
            message='Number is required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Validate the date
    if not date or date is None or date == '':
        return BusinessResponse(
            data=None,
            message='Date is required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Validate the entry type
    if not entry_type or entry_type == '' or entry_type is None:
        return BusinessResponse(
            data=None,
            message='Entry type is required',
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

    # Get the entry type id
    entry_type_pers_resp = pers_entry_type.read_entry_type_by_name(entry_type)
    if not entry_type_pers_resp.success:
        return BusinessResponse(
            data=None,
            message='Entry type not found',
            status_code=404,
            success=False,
            timestamp=datetime.now()
        )
    # Get the entry type id
    entry_type_id = entry_type_pers_resp.data.id

    #print(f'Vendor id: {vendor_id}')
    #print(f'Number: {number}')
    #print(f'Date: {date}')
    #print(f'Entry type id: {entry_type_id}')

    ###############################################################################################
    # Validate the Entry Line Items
    if not line_items or line_items == [] or line_items is None:
        return BusinessResponse(
            data=None,
            message='Line items are required',
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    attachment_line_item_names = []
    _entry_line_items = []
    total = 0
    for line_item in line_items:
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
        if not line_item['amount'] or line_item['amount'] == '' or line_item['amount'] is None:
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
        if not line_item['units'] or line_item['units'] == '' or line_item['units'] is None:
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

        # Create the attachment name
        attachment_line_item_names.append(
            {
                'project': project_pers_resp.data.abbreviation,
                'description': line_item['description'],
                'sub-cost-code': sub_cost_code_pers_resp.data.number,
                'amount': line_item['amount'],
            }
        )

        total += line_item['amount']

        # Create the entry line item object and append to the list
        _entry_line_items.append(
            pers_bill_line_item.EntryLineItem(
                created_datetime=created_datetime,
                modified_datetime=modified_datetime,
                description=line_item['description'],
                units=line_item['units'],
                rate=line_item['rate'],
                amount=line_item['amount'],
                is_billable=line_item['is-billable'],
                is_billed=False,
                entry_id=None,
                sub_cost_code_id=sub_cost_code_id,
                project_id=project_id
            )
        )

    #print(f'Entry line items: {_entry_line_items}')

    _entry = pers_bill.Entry(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        number=number,
        date=date,
        amount=total,
        entry_type_id=entry_type_id,
        vendor_id=vendor_id,
        attachment_id=None
    )


    ###############################################################################################
    # Validate the Attachments
    _attachments = []
    for index, value in enumerate(files):

        _attachments.append(
            pers_attachment.Attachment(
                created_datetime=created_datetime,
                modified_datetime=modified_datetime,
                name=value['name'],
                text=clean_text_for_db(value['text']),
                number_of_pages=value['pages'],
                file_size=value['size'],
                file_type=value['type'],
                file_path=f'project\\{_entry_line_items[index].project_id}\\bill'
            )
        )

    #print(f'Attachments: {_attachments}')

    create_entry_resp = pers_bill.create_entry_with_line_items_and_attachment(
        entry=_entry,
        line_items=_entry_line_items,
        attachments=_attachments
    )
    if create_entry_resp.success:
        for attachment in _attachments:
            write_file_resp = bus_attachment.write_file_to_project_id_bill_folder(
                file_content=attachment.text,
                file_path=attachment.file_path,
                file_name=attachment.name
            )
            if write_file_resp.success:
                return BusinessResponse(
                    data=None,
                    message='Entry created successfully',
                    status_code=200,
                    success=True,
                    timestamp=datetime.now()
                )
            else:
                return BusinessResponse(
                    data=None,
                    message='File write failed',
                    status_code=500,
                    success=False,
                    timestamp=datetime.now()
                )
    else:
        return BusinessResponse(
            data=None,
            message='Entry creation failed',
            status_code=500,
            success=False,
            timestamp=datetime.now()
        )
