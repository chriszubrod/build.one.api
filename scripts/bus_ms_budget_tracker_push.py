"""
Service module for syncing with Microsoft SharePoint.
"""

# python standard library imports
import asyncio
import base64
import hashlib
import json
import re
import requests
from datetime import datetime, timedelta
from dateutil import tz
from difflib import SequenceMatcher
from typing import List, Dict, Any
from flask import session


# third party imports
import jwt
import time

# local imports
from integrations.adapters import (
    map_project_to_sharepoint_workbook as pers_map_project_sharepoint_workbook,
    map_project_to_sharepoint_worksheet as pers_map_project_sharepoint_worksheet,
)
from integrations.ms.persistence import (
    pers_ms_sharepoint_site,
    pers_ms_sharepoint_workbook,
    pers_ms_sharepoint_worksheet,
)
from integrations.ms.auth import bus_ms_auth, api_ms_auth
from modules.bill import bus_bill, bus_bill_line_item, pers_bill_line_item
from modules.cost_code import bus_cost_code
from modules.sub_cost_code import bus_sub_cost_code
from modules.vendor import bus_vendor



def _get_sharepoint_site():
    sharepoint_site = pers_ms_sharepoint_site.\
        read_sharepoint_sites()
    if sharepoint_site.success:
        return sharepoint_site.data[0]
    else:
        return None


def _get_sharepoint_access_token():
    ms_auth = bus_ms_auth.get_ms_auth_by_user_id(
        user_id=2
    )
    if ms_auth.success:
        return ms_auth.data.access_token
    else:
        return None


def _get_project_integration_sharepoint_workbook_mapping(project_id):
    project_sharepoint_workbook_mapping = pers_map_project_sharepoint_workbook.\
        read_map_project_to_sharepoint_workbook_by_project_id(
            project_id=project_id
        )
    if project_sharepoint_workbook_mapping.success:
        return project_sharepoint_workbook_mapping.data
    else:
        return None


def _get_sharepoint_workbook_by_workbook_id(workbook_id):
    sharepoint_workbook = pers_ms_sharepoint_workbook.\
        read_sharepoint_workbook_by_workbook_id(workbook_id)
    if sharepoint_workbook.success:
        return sharepoint_workbook.data
    else:
        return None


def _get_project_integration_sharepoint_worksheet_mapping(project_id):
    project_sharepoint_worksheet_mapping = pers_map_project_sharepoint_worksheet.\
        read_map_project_to_sharepoint_worksheet_by_project_id(
            project_id=project_id
        )
    if project_sharepoint_worksheet_mapping.success:
        return project_sharepoint_worksheet_mapping.data
    else:
        return None


def _get_sharepoint_worksheet_by_worksheet_id(worksheet_id):
    sharepoint_worksheet = pers_ms_sharepoint_worksheet.\
        read_sharepoint_worksheet_by_worksheet_id(worksheet_id)
    if sharepoint_worksheet.success:
        return sharepoint_worksheet.data
    else:
        return None


def _get_sharepoint_worksheet(site_id, item_id, worksheet_id, access_token):

    headers = {
        'Authorization': f'Bearer {access_token}'
        # 'Workbook-Session-Id': 'session_id'
    }
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets/{worksheet_id}/usedRange'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return resp.json()


def _find_insert_point_range_for_bill_line_item(
        worksheet_file: dict,
        bill_line_item_sub_cost_code_number: str
    ):
    try:
        print(f'\nBill Line Item Sub Cost Code: {bill_line_item_sub_cost_code_number} (type: {type(bill_line_item_sub_cost_code_number)})')

        # Check range information
        range_address = worksheet_file.get('address', '')
        row_index = worksheet_file.get('rowIndex', 0)
        column_count = worksheet_file.get('columnCount', 0)
        column_hidden = worksheet_file.get('columnHidden', False)
        column_index = worksheet_file.get('columnIndex', 0)
        row_count = worksheet_file.get('rowCount', 0)
        row_hidden = worksheet_file.get('rowHidden', False)
        row_index = worksheet_file.get('rowIndex', 0)
        values = worksheet_file.get('values', [])

        print(f'\nRange Address: {range_address}')
        print(f'\nRow Index: {row_index}')
        print(f'\nColumn Count: {column_count}')
        print(f'\nColumn Hidden: {column_hidden}')
        print(f'\nColumn Index: {column_index}')
        print(f'\nRow Count: {row_count}')
        print(f'\nRow Hidden: {row_hidden}')
        print(f'\nLength of Values: {len(values)}')

        first_occurrence_row = None
        last_occurrence_row = None
        first_occurrence_row_with_data = None
        last_occurrence_row_with_data = None

        for i, row in enumerate(values):
            cell_str = f"{float(row[1]):.2f}" if row[1] and str(row[1]).replace('.', '').replace('-', '').isdigit() else ""
            target_str = f"{float(bill_line_item_sub_cost_code_number):.2f}"
            
            if cell_str == target_str:
                print(f'\nRow {i}: {row[0]} - {cell_str} - {target_str}')

                has_bill_line_item_data = False
                if len(row) > 3:  # Need at least 4 columns
                    for col_idx in range(7, len(row)):  # Check columns 8 onwards
                        if row[col_idx] and str(row[col_idx]).strip() != "":
                            has_bill_line_item_data = True
                            break

                if first_occurrence_row is None:
                    first_occurrence_row = row_index + i + 1
                
                if has_bill_line_item_data:
                    if first_occurrence_row_with_data is None:
                        first_occurrence_row_with_data = row_index + i + 1
                    last_occurrence_row_with_data = row_index + i + 1
                    print(f'\nRow {i} has bill line item data: {row}')
                else:
                    print(f'\nRow {i} has sub-cost code but no bill line item data: {row}')
                
                last_occurrence_row = row_index + i + 1

        print(f'\nFirst Occurrence Row: {first_occurrence_row}')
        print(f'\nLast Occurrence Row: {last_occurrence_row}')
        print(f'\nFirst Occurrence Row with Data: {first_occurrence_row_with_data}')
        print(f'\nLast Occurrence Row with Data: {last_occurrence_row_with_data}')

        # Determine insertion point
        if first_occurrence_row_with_data is None and first_occurrence_row is not None:
            # Insert 2 rows after the first occurrence (no data found)
            insertion_row = first_occurrence_row + 2
            print(f'\nInserting 2 rows after first occurrence: row {insertion_row}')
        elif first_occurrence_row_with_data is not None:
            # Insert after the first occurrence (no data found)
            insertion_row = first_occurrence_row_with_data + 1
            print(f'\nInserting after first occurrence: row {insertion_row}')
        else:
            print(f'\nNo matching sub-cost code found')
            return None

        # Extract sheet name from the range address
        sheet_name = range_address.split('!')[0] if '!' in range_address else 'Sheet1'
        
        # Always use A:Z range (26 columns)
        insertion_range = f"{sheet_name}!A{insertion_row}:Z{insertion_row}"
        
        print(f'\nInsertion range: {insertion_range}')

        return insertion_range

    except Exception as e:
        print(f'\nError reading JSON file: {str(e)}')
        return None


def _insert_row_with_data_to_worksheet(
        site_id: str,
        item_id: str,
        worksheet_id: str,
        access_token: str,
        insert_range: str,
        row_data: list
    ):
    try:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
            # 'Workbook-Session-Id': 'session_id'
        }
        insert_row_url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets/{worksheet_id}/range(address=\'{insert_range}\')/insert'

        insert_row_payload = {
            'shift': "down"
        }

        print(f'\nInserting row at range: {insert_range}')
        print(f'\nInsert URL: {insert_row_url}')
        
        insert_response = requests.post(
            url=insert_row_url,
            headers=headers,
            json=insert_row_payload,
            timeout=30
        )

        if insert_response.status_code not in [200, 201, 202]:
            print(f'\nFailed to insert row. Status: {insert_response.status_code}')
            print(f'\nResponse: {insert_response.text}')
            return False
    
        print(f'\nRow inserted successfully. Status: {insert_response.status_code}')

        # Update the row with the data
        update_url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets/{worksheet_id}/range(address=\'{insert_range}\')'
        
        update_payload = {
            "values": [row_data]  # Wrap row_data in a list to make it a 2D array
        }
        
        print(f'\nUpdating row with data: {row_data}')
        print(f'\nUpdate URL: {update_url}')
        
        update_response = requests.patch(
            url=update_url,
            headers=headers,
            json=update_payload,
            timeout=30
        )
        
        if update_response.status_code not in [200, 201, 202]:
            print(f'\nFailed to update row with data. Status: {update_response.status_code}')
            print(f'\nResponse: {update_response.text}')
            return False
        
        print(f'\nRow updated with data successfully. Status: {update_response.status_code}')

        return True

    except Exception as e:
        print(f'\nError inserting row with data to worksheet: {str(e)}')
        return None







def main_budget_tracker_push_function():

    # Get bills that have been created since last sync
    bills = None
    bills = bus_bill.get_bill_by_last_created()
    if bills.success:
        bills = bills.data
    else:
        print(f'\nNo bills found since last sync')
        return 'No bills found since last sync'


    if bills:
        for bill in bills:
            print(f'\nBill: {bill}')

            # Get bill line items for this bill
            bill_line_items = bus_bill_line_item.get_bill_line_item_by_bill_id(
                bill_id=bill.id
            )
            if bill_line_items.success:
                bill_line_items = bill_line_items.data
            else:
                print(f'\nNo bill line items found for bill {bill}')
                continue




    
            # Get SharePoint site
            site_id = ''
            sharepoint_site = _get_sharepoint_site()
            #print(f'\nSharepoint Site: {sharepoint_site}')
            if not sharepoint_site:
                return 'No SharePoint site found'
            else:
                site_id = sharepoint_site.site_sharepoint_id
            
            # Refresh SharePoint access token
            api_ms_auth.refresh_token()

            # Get SharePoint access token
            access_token = _get_sharepoint_access_token()
            if not access_token:
                return 'No SharePoint access token found'


            for bill_line_item in bill_line_items:
                print(f'\nBill Line Items: {bill_line_item}')



                # Get sub cost code for bill line item
                sub_cost_code = None
                sub_cost_code_mapping = bus_sub_cost_code.get_sub_cost_code_by_id(
                    sub_cost_code_id=bill_line_item.sub_cost_code_id
                )
                if sub_cost_code_mapping.success:
                    sub_cost_code = sub_cost_code_mapping.data
                else:
                    print(f'\nNo Sub Cost Code found for bill line item {bill_line_item}')
                    continue


                vendor = None
                if bill.vendor_id:
                    vendor_mapping = bus_vendor.get_vendor_by_id(
                        vendor_id=bill.vendor_id
                    )
                    if vendor_mapping.success:
                        vendor = vendor_mapping.data
                    else:
                        print(f'\nNo Vendor found for bill {bill}')



                # Get the SharePoint Workbook for that project.
                workbook_mapping_id = None
                project_sharepoint_workbook_mapping = _get_project_integration_sharepoint_workbook_mapping(
                    project_id=bill_line_item.project_id
                )
                #print(f'\nProject Sharepoint Workbook Mapping: {project_sharepoint_workbook_mapping}')
                if project_sharepoint_workbook_mapping:
                    workbook_mapping_id = project_sharepoint_workbook_mapping[0].ms_sharepoint_workbook_id
                else:
                    print(f'\nNo Project Sharepoint Workbook Mapping found for project {bill_line_item.project_id}')
                    continue


                workbook_id = None
                sharepoint_workbook = _get_sharepoint_workbook_by_workbook_id(
                    workbook_id=workbook_mapping_id
                )
                #print(f'\nSharepoint Workbook: {sharepoint_workbook}')
                if sharepoint_workbook:
                    workbook_id = sharepoint_workbook.workbook_ms_id
                else:
                    print(f'\nNo Sharepoint Workbook found for project {bill_line_item.project_id}')
                    continue


                # Get the SharePoint Worksheet for that project.
                worksheet_mapping_id = None
                project_sharepoint_worksheet_mapping = _get_project_integration_sharepoint_worksheet_mapping(
                    project_id=bill_line_item.project_id
                )
                print(f'\nProject Sharepoint Worksheet Mapping: {project_sharepoint_worksheet_mapping}')
                if project_sharepoint_worksheet_mapping:
                    worksheet_mapping_id = project_sharepoint_worksheet_mapping[0].ms_sharepoint_worksheet_id
                else:
                    print(f'\nNo Project Sharepoint Worksheet Mapping found for project {bill_line_item.project_id}')
                    continue


                worksheet_id = None
                sharepoint_worksheet = _get_sharepoint_worksheet_by_worksheet_id(
                    worksheet_id=worksheet_mapping_id
                )
                #print(f'\nSharepoint Worksheet: {sharepoint_worksheet}')
                if sharepoint_worksheet:
                    worksheet_id = sharepoint_worksheet.worksheet_ms_id
                else:
                    print(f'\nNo Sharepoint Worksheet found for project {bill_line_item.project_id}')
                    continue





                
                # TODO: For each bill line item attachment, get the SharePoint files for that project.
                # TODO: Update this to get the budget tracker file
                sharepoint_worksheet = _get_sharepoint_worksheet(
                    site_id=site_id,
                    item_id=workbook_id,
                    worksheet_id=worksheet_id,
                    access_token=access_token
                )
                #print(f'\nSharepoint Worksheet: {sharepoint_worksheet}')
                #print(f'\nSharepoint Worksheet Type: {type(sharepoint_worksheet)}')

                insert_range = _find_insert_point_range_for_bill_line_item(
                    worksheet_file=sharepoint_worksheet,
                    bill_line_item_sub_cost_code_number=sub_cost_code.number
                )

                if insert_range:
                    print(f'\nInsert Range Returned: {insert_range}')

                    # Create row_data with 26 columns (A:Z) to match the range
                    row_data = [""] * 26  # Initialize with 26 empty strings
                    
                    # Populate the specific columns we need
                    row_data[0] = ""  # Column A
                    row_data[1] = str(int(float(sub_cost_code.number)))  # Column B
                    row_data[2] = str(sub_cost_code.number)  # Column C
                    row_data[8] = str(bill.date)  # Column I
                    row_data[9] = str(vendor.name) if vendor else ""  # Column J
                    row_data[10] = str(bill.number)  # Column K
                    row_data[11] = str(bill_line_item.description)  # Column L
                    row_data[12] = "Ck"  # Column M
                    row_data[13] = str(bill_line_item.amount)  # Column N

                    print(f'\nRow data length: {len(row_data)}')
                    print(f'\nRow data: {row_data}')
                    
                    # Insert the row with data
                    success = _insert_row_with_data_to_worksheet(
                        site_id=site_id,
                        item_id=workbook_id,
                        worksheet_id=worksheet_id,
                        access_token=access_token,
                        insert_range=insert_range,
                        row_data=row_data
                    )
                    
                    if success:
                        print(f'\nSuccessfully inserted bill line item data into worksheet')
                    else:
                        print(f'\nFailed to insert bill line item data into worksheet')
                else:
                    print(f'\nNo insert range found - cannot insert data')






                try:
                    with open('sharepoint_worksheet_dump.json', 'w', encoding='utf-8') as f:
                        json.dump(sharepoint_worksheet, f, indent=4, ensure_ascii=False)
                    print(f'\nSharePoint worksheet data dumped to sharepoint_worksheet_dump.json')
                except Exception as e:
                    print(f'\nError dumping to JSON: {str(e)}')

    return 'Main Function Complete'


if __name__ == '__main__':
    print('KICKING OFF')
    main_result = main_budget_tracker_push_function()
    print(main_result)
