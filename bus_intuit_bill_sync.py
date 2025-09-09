"""
Service module for syncing with Intuit Bill.
"""

# python standard library imports
import asyncio
import base64
import hashlib
import json
import re
import requests
from datetime import datetime
from dateutil import tz
from difflib import SequenceMatcher
from typing import List, Dict, Any
from flask import session


# third party imports
import jwt
import time

# local imports
from shared.response import BusinessResponse
from shared.response import SuccessResponse
from integrations.intuit import pers_intuit_urls, pers_intuit_auth
from integrations.map import (
    pers_map_attachment_sharepoint_file,
    pers_map_project_sharepoint_folder,
    pers_map_bill_intuit_bill
)
from integrations.ms import (
    ms_drive_int,
    ms_upload_new_file,
    pers_ms_sharepoint_file,
    pers_ms_sharepoint_folder,
    pers_ms_sharepoint_site
)
from integrations.ms.auth import bus_ms_auth, api_ms_auth
from modules.bill import pers_bill_line_item, pers_bill
from modules.project import pers_project
from modules.sub_cost_code import pers_sub_cost_code, bus_sub_cost_code
from modules.vendor import pers_vendor, bus_vendor
from integrations.intuit import bus_intuit_item, bus_intuit_vendor


def _get_bills():
    bills = pers_bill.\
        read_bills()
    if bills.success:
        return bills.data
    else:
        return None


def _get_bills_mapping():
    bills_mapping = pers_map_bill_intuit_bill.\
        read_map_intuit_bills()
    if bills_mapping.success:
        return bills_mapping.data
    else:
        return None


def _get_bills_not_mapped(bills, mapping):
    return [bill for bill in bills if bill.id not in [mapping.bill_id for mapping in mapping]]


def _get_bill_line_items_by_bill_id(bill_id):
    bill_line_items = pers_bill_line_item.\
        read_bill_line_item_by_bill_id(bill_id)
    if bill_line_items.success:
        return bill_line_items.data
    else:
        return None


def _build_uri(url_name, urls, realm_id, query=None, minor_version=None):
    uri = ""
    base = ""
    url = ""
    #print(f'\nUrls: {urls}')
    for row in urls:
        name = row.name
        slug = row.slug
        if name == 'base':
            base = slug
        elif name == url_name:
            url = slug
    
    # Handle different URL types based on number of placeholders
    if url.count('{}') == 1:
        # Simple case like createbill: /v3/company/{}/bill
        uri = base + url.format(realm_id)
    elif url.count('{}') == 3:
        # Query case like querybill: /v3/company/{}/query?query={}&minorversion={}
        if query and minor_version:
            uri = base + url.format(realm_id, query, minor_version)
        else:
            return "No query or minor version provided"
    
    return uri


def _get_intuit_site(url_name, realm_id, query=None, minor_version=None):

    uri = None
    # read intuit urls from database
    pers_read_intuit_urls_resp = pers_intuit_urls.read_intuit_urls()

    # if status code is successful, build the uris needed to call bill intuit endpoint
    if pers_read_intuit_urls_resp.success:
        # build uri for bill count, pass in url response message and pass realmid
        uri = _build_uri(
            url_name=url_name,
            urls=pers_read_intuit_urls_resp.data,
            realm_id=realm_id,
            query=query,
            minor_version=minor_version
        )
    return uri


def _request_intuit_authorization():
    try:
        intuit_auth_resp = requests.get(
            url=f'https://python312.azurewebsites.net/intuit/authorization/request?token=kjapr215asd613ar5sa961a3r5asd3132a1sdf8s'
        )
        #print(f'\nIntuit Auth Request Response Status Code: {intuit_auth_resp.status_code}')
        #print(f'\nIntuit Auth Request Response Text: {intuit_auth_resp.text}')

        if intuit_auth_resp.status_code == 201:
            return True
        else:
            return False
    except Exception as e:
        print(f'\nError requesting Intuit Authorization: {str(e)}')
        return False


def _refresh_intuit_authorization():
    #
    try:
        intuit_auth_refresh_resp = requests.get(
            url=f'https://python312.azurewebsites.net/intuit/authorization/refresh/request?token=kjapr215asd613ar5sa961a3r5asd3132a1sdf8s'
        )
        print(f'\nIntuit Auth Refresh Response Status Code: {intuit_auth_refresh_resp.status_code}')
        print(f'\nIntuit Auth Refresh Response Text: {intuit_auth_refresh_resp.text}')

        if intuit_auth_refresh_resp.status_code == 200:
            if 'Token invalid' in intuit_auth_refresh_resp.text:
                return _request_intuit_authorization()
            else:
                return True

        return False

    except Exception as e:
        print(f'\nError refreshing Intuit Authorization: {str(e)}')
        return False


def _get_intuit_authorization():
    intuit_auth = pers_intuit_auth.read_db_intuit_auth()
    if intuit_auth:
        return intuit_auth.data
    else:
        return None


def _get_item_ref_value(bill_line_item):
    item_ref_value = None
    pers_sub_cost_code_resp = pers_sub_cost_code.\
        read_sub_cost_code_by_id(
            id=bill_line_item.sub_cost_code_id
        )
    #print(f'\nSub Cost Code Response: {pers_sub_cost_code_resp.data}')
    if pers_sub_cost_code_resp.success:

        get_mapped_intuit_item_bus_response = bus_sub_cost_code.\
            get_mapped_intuit_item_by_sub_cost_code_id(
                sub_cost_code_id=pers_sub_cost_code_resp.data.id
            )

        if get_mapped_intuit_item_bus_response.success:
            get_intuit_item_bus_response = bus_intuit_item.get_intuit_item_by_id(
                intuit_item_id=get_mapped_intuit_item_bus_response.data.intuit_item_id
            )
            if get_intuit_item_bus_response.success:
                item_ref_value = get_intuit_item_bus_response.data.item_id
            else:
                item_ref_value = None
        else:
            item_ref_value = None
    else:
        item_ref_value = None
    return item_ref_value


def _get_customer_ref_value(bill_line_item):
    customer_ref_value = None
    pers_project_resp = pers_project.read_project_intuit_customer_by_project_id(
        project_id=bill_line_item.project_id
    )
    #print(f'\nProject Response: {pers_project_resp.data}')
    if pers_project_resp.success:
        customer_ref_value = pers_project_resp.data.intuit_customer_id
    else:
        customer_ref_value = None
    return customer_ref_value


def _get_vendor_ref_value(bill):
    vendor_ref_value = None
    pers_vendor_resp = pers_vendor.\
        read_vendor_by_id(
            vendor_id=bill.vendor_id
        )
    #print(f'\nVendor Response: {pers_vendor_resp.data}')
    if pers_vendor_resp.success:

        get_mapped_intuit_vendor_bus_response = bus_vendor.\
            get_mapped_intuit_vendor_by_vendor_id(
                vendor_id=pers_vendor_resp.data.id
            )
        #print(f'\nGet Mapped Intuit Vendor Bus Response: {get_mapped_intuit_vendor_bus_response.data}')

        if get_mapped_intuit_vendor_bus_response.success:
            get_intuit_vendor_bus_response = bus_intuit_vendor.get_intuit_vendor_by_id(
                intuit_vendor_id=get_mapped_intuit_vendor_bus_response.data.intuit_vendor_id
            )
            #print(f'\nGet Intuit Vendor Bus Response: {get_intuit_vendor_bus_response.data}')
            if get_intuit_vendor_bus_response.success:
                vendor_ref_value = get_intuit_vendor_bus_response.data.vendor_id
            else:
                vendor_ref_value = None
        else:
            vendor_ref_value = None
    else:
        vendor_ref_value = None
    return vendor_ref_value


def _create_bill(uri, access_token, bill_data):
    # try to request a response from the intuit bill uri endpoint
    try:
        url = uri
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "bearer " + access_token
        }
        resp = requests.post(
            url=url,
            headers=headers,
            data=json.dumps(bill_data),  # data=body
            timeout=10
        )
        return {
            "message": resp.text,
            "status_code": resp.status_code
        }
    except Exception as e:
        return {
            "message": f"An error occured while trying to call bill endpoint: {e}",
            "status_code": 500
        }


def _query_bill(uri, access_token, bill_data):
    # try to request a response from the intuit bill uri endpoint
    try:
        url = uri
        print(f'\nQuery Bill URI: {url}')
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "bearer " + access_token
        }
        resp = requests.get(
            url=url,
            headers=headers,
            data=json.dumps(bill_data),  # data=body
            timeout=10
        )
        return {
            "message": resp.text,
            "status_code": resp.status_code
        }
    except Exception as e:
        return {
            "message": f"An error occured while trying to call bill endpoint: {e}",
            "status_code": 500
        }


def main_intuit_bill_sync_function():

    # Get bills that are not mapped to an Intuit Bill
    bills_not_mapped = None

    bills = _get_bills()
    #print(f'\nBill Line Item Attachments Count: {len(bill_line_item_attachments)}')
    if not bills:
        return 'No bills found'

    bills_mapping = _get_bills_mapping()
    #print(f'\nBill Line Item Attachments Mapping Count: {len(bill_line_item_attachments_mapping)}')
    if not bills_mapping:
        bills_not_mapped = bills
    else:
        bills_not_mapped = _get_bills_not_mapped(
            bills=bills,
            mapping=bills_mapping
        )
    print(f'\nBills Not Mapped: {len(bills_not_mapped)}')
    #print(f'\nBills Not Mapped: {bills_not_mapped}')





    # Get Realm ID and Access Token
    REALM_ID = None
    ACCESS_TOKEN = None
    # read realmId from intuit_auth in database
    pers_intuit_auth_resp = pers_intuit_auth.read_db_intuit_auth()
    # if status code is successful, set REALM_ID and ACCESS_TOKEN to be used downstream
    if pers_intuit_auth_resp.success:
        row = pers_intuit_auth_resp.data
        REALM_ID = row.realm_id
        ACCESS_TOKEN = row.access_token
        #print(f'\nRealm ID: {REALM_ID}')
        #print(f'\nAccess Token: {ACCESS_TOKEN}')
    # if status code is not sucessful, return dict and pass message and status_code.
    else:
        return f'Error getting Realm ID and Access Token: {pers_intuit_auth_resp.message}'




    # Get create bill intuit site
    create_bill_intuit_site = _get_intuit_site(url_name='createbill', realm_id=REALM_ID)
    if create_bill_intuit_site is None:
        return 'No Intuit site found'
    #print(f'\nCreate Bill Intuit Site: {create_bill_intuit_site}')







    # Refresh Intuit access token
    refresh_intuit_authorization_resp = _refresh_intuit_authorization()
    if refresh_intuit_authorization_resp:
        #print(f'\nIntuit access token refreshed')
        pass
    else:
        return 'Error refreshing Intuit access token'







    # TODO: Get Intuit access token
    # Get SharePoint access token
    intuit_auth = _get_intuit_authorization()
    if intuit_auth is None:
        return 'No Intuit access token found'
    else:
        #print(f'\nIntuit Auth: {intuit_auth}')
        pass




    # For each bill, get bill line items
    bill_line_items = []

    for bill in bills_not_mapped:
        print(f'\nBill : {bill}')

        line_item_list = []

        # TODO: For each bill, get bill line items.
        bill_line_items = _get_bill_line_items_by_bill_id(
            bill_id=bill.id
        )
        #print(f'\nBill Line Items: {bill_line_items}')
            
        for bill_line_item in bill_line_items:

            # Set billable status
            billable_status = None
            if bill_line_item.is_billable:
                billable_status = "Billable"
            else:
                billable_status = "NotBillable"
            #print(f'\nBillable Status: {billable_status}')
        
            # Set item ref value
            # This is the build one sub cost code
            item_ref_value = None
            item_ref_value = _get_item_ref_value(
                bill_line_item=bill_line_item
            )
            #print(f'\nItem Ref Value: {item_ref_value}')

            # Set customer ref value
            # This is the build one project
            customer_ref_value = None
            customer_ref_value = _get_customer_ref_value(
                bill_line_item=bill_line_item
            )
            #print(f'\nCustomer Ref Value: {customer_ref_value}')

            line_item_list.append(
                {
                    "Description": bill_line_item.description,
                    "Amount": float(bill_line_item.amount),
                    "DetailType": "ItemBasedExpenseLineDetail",
                    "ItemBasedExpenseLineDetail": {
                        "CustomerRef": {
                            "value": customer_ref_value
                        },
                        "BillableStatus": billable_status,
                        "ItemRef": {
                            "value": item_ref_value
                        },
                        "UnitPrice": float(bill_line_item.rate),
                        "MarkupInfo": {
                            "Percent": 0
                        },
                        "Qty": bill_line_item.units
                    }
                }
            )

        vendor_ref_value = _get_vendor_ref_value(
            bill=bill
        )
        #print(f'\nVendor Ref Value: {vendor_ref_value}')


        bill_data = {
            "DocNumber": bill.number,
            "TxnDate": bill.date.strftime('%Y-%m-%d'),
            "SalesTermRef": {
                "value": "2"
            },
            "Line": line_item_list,
            "VendorRef": {
                "value": vendor_ref_value
            },
            "APAccountRef": {
                "value": "7"
            }
        }
        #print(f'\nBill Data: {bill_data}')

        
        # create bill
        create_bill_response = _create_bill(
            uri=create_bill_intuit_site,
            access_token=ACCESS_TOKEN,
            bill_data=bill_data
        )
        create_bill_resp_message = create_bill_response.get('message')
        if create_bill_response.get('status_code') == 401:

            # if this string is in query company info resposne, then authenticaion token has expired
            # possibly, in the future, we could call the connect_intuit_oauth_2_token_endpoint_refresh
            # function or the intuit_authorization_refresh endpoint
            s = "message=AuthenticationFailed; errorCode=003200; statusCode=401"
            if s in create_bill_resp_message:

                print(
                    {
                        "message": (
                            "An error occured because the authentication token has expired." +
                            "Please refresh the token."
                        ),
                        "status_code": create_bill_response.get('status_code')
                    }
                )

            print(
                {
                    "message": create_bill_resp_message,
                    "status_code": create_bill_response.get('status_code')
                }
            )

        print(
            {
                "message": create_bill_response.get('message'),
                "status_code": create_bill_response.get('status_code')
            }
        )


    return 'Main Function Complete'


if __name__ == '__main__':
    print('KICKING OFF')
    main_result = main_intuit_bill_sync_function()
    print(main_result)
