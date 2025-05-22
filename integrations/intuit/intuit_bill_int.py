"""Module to manage processes for synchronizing data from Intuit QuickBooks Online Bill to
database.

This module receives the function call from the application layer, processes the requests to the
Intuit Quickbooks Online api, transforms the responses, and passes data to the persistence layer
for database interation.

Functions:

    run_bill_process - this is the hub of the wheel

"""
import json
import requests

from datetime import datetime
from modules.project import pers_project
from modules.sub_cost_code import pers_sub_cost_code
from modules.vendor import pers_vendor
from persistence import (
    pers_intuit_auth,
    pers_intuit_bill,
    pers_intuit_data_sync,
    pers_intuit_urls
)

from persistence.pers_response import SuccessResponse, PersistenceResponse, DatabaseError

LAST_UPDATE = ""
REALM_ID = ""
ACCESS_TOKEN = ""


def process_bill(tax_identifier, domain, bill_id, sync_token, created_datetime, last_update_datetime, company_name, display_name, print_on_check_name, active, v4id_pseudonym, primary_email_address, realm_id):

    # call database and read bill record by bill id
    pers_intuit_bill_response = pers_intuit_bill.\
        read_bill_by_id(bill_id=bill_id)


    # if record exists, update the database record and return the message
    if pers_intuit_bill_response.get('status_code') == 201:
        pers_intuit_update_bill_resp = pers_intuit_bill.\
            update_bill_by_realm_id_and_bill_id(
                tax_identifier=tax_identifier,
                bill_id=bill_id,
                sync_token=sync_token,
                created_datetime=created_datetime,
                last_update_datetime=last_update_datetime,
                company_name=company_name,
                display_name=display_name,
                print_on_check_name=print_on_check_name,
                active=active,
                v4id_pseudonym=v4id_pseudonym,
                primary_email_address=primary_email_address,
                realm_id=realm_id
            )

        return {
            "message": pers_intuit_update_bill_resp.get('message'),
            "rowcount": pers_intuit_update_bill_resp.get('rowcount'),
            "status_code": pers_intuit_update_bill_resp.get('status_code')
        }

    # if record does not exist, create the database record and return the message
    if pers_intuit_bill_response.get('status_code') == 501:

        pers_intuit_create_bill_resp = pers_intuit_bill.\
            create_bill(
                tax_identifier=tax_identifier,
                bill_id=bill_id,
                sync_token=sync_token,
                created_datetime=created_datetime,
                last_update_datetime=last_update_datetime,
                company_name=company_name,
                display_name=display_name,
                print_on_check_name=print_on_check_name,
                active=active,
                v4id_pseudonym=v4id_pseudonym,
                primary_email_address=primary_email_address,
                realm_id=realm_id
            )

        return {
            "message": pers_intuit_create_bill_resp.get('message'),
            "rowcount": pers_intuit_create_bill_resp.get('rowcount'),
            "status_code": pers_intuit_create_bill_resp.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_bill_response.get('message'),
        "rowcount": pers_intuit_bill_response.get('rowcount'),
        "status_code": pers_intuit_bill_response.get('status_code')
    }


def process_bill_message(message_decoded, realmId):

    # get query response from decoded message dict
    query_response = message_decoded.get('QueryResponse')

    # get query response time from decoded message dict
    query_response_time = message_decoded.get('time')

    # get bill list, start position, and max_results
    bill_list = query_response.get('Bill')
    start_position = query_response.get('startPosition')
    max_results = query_response.get('maxResults')
    bill_count = 0

    if max_results == 0 or max_results == None:
        return {
            "message": "The Bill process has completed.",
            "rowcount": 0,
            "status_code": 201
        }

    for bill in bill_list:

        bill_count += 1

        tax_identifier = bill.get('TaxIdentifier', '')
        domain = bill.get('domain', '')
        bill_id = bill.get('Id','')
        sync_token = bill.get('SyncToken','')
        meta_data = bill.get('MetaData', '')
        if meta_data == '':
            created_datetime = ''
            last_update_datetime = ''
        else:
            created_datetime = meta_data.get('CreateTime', '')
            last_update_datetime = meta_data.get('LastUpdatedTime', '')
        company_name = bill.get('CompanyName','')
        display_name = bill.get('DisplayName','')
        print_on_check_name = bill.get('PrintOnCheckName','')
        active = bill.get('Active','')
        v4id_pseudonym = bill.get('V4IDPseudonym', '')
        primary_email_addr = bill.get('PrimaryEmailAddr', '')
        if primary_email_addr == '':
            primary_email_address = ''
        else:
            primary_email_address = primary_email_addr.get('Address', '')

        # step through a series of bill updates, but exit if response is not successful
        # 1. process bill
        process_bill(
            tax_identifier=tax_identifier,
            domain=domain,
            bill_id=bill_id,
            sync_token=sync_token,
            created_datetime=created_datetime,
            last_update_datetime=last_update_datetime,
            company_name=company_name,
            display_name=display_name,
            print_on_check_name=print_on_check_name,
            active=active,
            v4id_pseudonym=v4id_pseudonym,
            primary_email_address=primary_email_address,
            realm_id=realmId # realmId is passed in from the function call, and must be passed to the process_bill function
        )

        pers_intuit_data_sync.update_intuit_data_sync_by_data_source_name(
            data_source_name="bill",
            last_update_datetime=query_response_time
        )

    return {
        "message": "The Bill process has completed.",
        "rowcount": bill_count,
        "status_code": 201
    }


def query_intuit_bill_count_info(uri, access_token):
    # try to request a response from the intuit bill uri endpoint
    try:
        url = uri
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "bearer " + access_token
        }
        resp = requests.get(url=url, headers=headers)
        return {
            "message": resp.text,
            "status_code": resp.status_code
        }
    except:
        return {
            "message": "An error occured while trying to call bill endpoint.",
            "status_code": 500
        }


def query_intuit_bill_info(uri, access_token):
    # try to request a response from the intuit bill uri endpoint
    try:
        url = uri
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "bearer " + access_token
        }
        resp = requests.get(url=url, headers=headers)
        return {
            "message": resp.text,
            "status_code": resp.status_code
        }
    except:
        return {
            "message": "An error occured while trying to call bill endpoint.",
            "status_code": 500
        }


def build_bill_count_uri(urls, realm_id, after_datetime):
    uri = ""
    base = ""
    version = ""
    url = ""
    query = (
        "select count(*) from Bill Where Metadata.LastUpdatedTime > '{}'".format(after_datetime)
    )
    for row in urls:
        name = row.__getattribute__('Name')
        slug = row.__getattribute__('Slug')
        if name == 'base':
            base = slug
        elif name == 'minorversion':
            version = slug
        elif name == 'querybill':
            url = slug
    uri = base + url.format(realm_id, query, version)
    return uri


def build_bill_uri(urls, realm_id, after_datetime, start_position=1, max_results=1000):
    """Build uri from urls variable passed in.

    Builds uri needed for intuit endpoint based on the name of the url provided below.

    Args:
        urls: rows of tuples
        realmId: string

    Returns:
        A uri string needed for custmer endpoint and query.

    Raises:
        None
    """
    uri = ""
    base = ""
    version = ""
    url = ""
    query = (
        "select * from Bill Where Metadata.LastUpdatedTime > '{}' STARTPOSITION {} MAXRESULTS {}".format(after_datetime, start_position, max_results)
    )
    for row in urls:
        name = row.__getattribute__('Name')
        slug = row.__getattribute__('Slug')
        if name == 'base':
            base = slug
        elif name == 'minorversion':
            version = slug
        elif name == 'querybill':
            url = slug
    uri = base + url.format(realm_id, query, version)
    return uri


def run_bill_process():
    """Calls functions to synchronize data from Intuit Quickbooks Online Bill to database.

    Retrieves cusomter data from intuit endpoint, transforms and calls persistence layer functions
    to sync data.

    Args:
        None

    Returns:
        A dict mapping keys to the corresponding message, rowcount and status code for response.

    Raises:
        None
    """

    # read the datasync datetime stamp when the last update was recorded
    pers_intuit_data_sync_resp = pers_intuit_data_sync.read_intuit_data_sync_by_data_source_name(
        data_source_name='bill'
    )

    # if status code is successful, set LAST_UPDATE to be used downstream
    if pers_intuit_data_sync_resp.get('status_code') == 201:

        row = pers_intuit_data_sync_resp.get('message')

        # LAST UPDATE is stored as a datetime object. Ex. 1900-01-01 00:00:00
        last_update_datetime = row.__getattribute__('LastUpdateDatetime')

        # Convert to a string in ISO 8601 format. Ex. '1900-01-01T00:00:00'
        # We will use this in the intuit qbo bill query.
        LAST_UPDATE = last_update_datetime.strftime("%Y-%m-%dT%H:%M:%S")

    else:

        return {
            "message": pers_intuit_data_sync_resp.get('message'),
            "rowcount": 0,
            "status_code": pers_intuit_data_sync_resp.get('status_code')
        }



    # read realmId from intuit_auth in database
    pers_intuit_auth_resp = pers_intuit_auth.read_db_intuit_auth()

    # if status code is successful, set REALM_ID and ACCESS_TOKEN to be used downstream
    if pers_intuit_auth_resp.get('status_code') == 201:

        row = pers_intuit_auth_resp.get('message')
        REALM_ID = row.__getattribute__('RealmId')
        ACCESS_TOKEN = row.__getattribute__('AccessToken')

    # if status code is not sucessful, return dict and pass message and status_code.
    else:

        return {
            "message": pers_intuit_auth_resp.get('message'),
            "rowcount": 0,
            "status_code": pers_intuit_auth_resp.get('status_code')
        }




    # read intuit urls from database
    pers_read_intuit_urls_resp = pers_intuit_urls.read_intuit_urls()

    # if status code is successful, build the uris needed to call bill intuit endpoint
    if pers_read_intuit_urls_resp.get('status_code') == 201:

        # build uri for bill count, pass in url response message and pass realmid
        bill_count_uri = build_bill_count_uri(
            urls=pers_read_intuit_urls_resp.get('message'),
            realm_id=REALM_ID,
            after_datetime=LAST_UPDATE
        )

    else:

        return {
            "message": pers_read_intuit_urls_resp.get('message'),
            "status_code": pers_read_intuit_urls_resp.get('status_code')
        }




    # request bill count from intuit
    total_count = 0
    query_bill_count_resp = query_intuit_bill_count_info(
        uri=bill_count_uri,
        access_token=ACCESS_TOKEN
    )
    query_bill_count_resp_message = query_bill_count_resp.get('message')


    if query_bill_count_resp.get('status_code') == 401:

        # if this string is in query company info resposne, then authenticaion token has expired
        # possibly, in the future, we could call the connect_intuit_oauth_2_token_endpoint_refresh
        # function or the intuit_authorization_refresh endpoint
        s = "message=AuthenticationFailed; errorCode=003200; statusCode=401"
        if s in query_bill_count_resp_message:

            return {
                "message": (
                    "An error occured because the authentication token has expired." +
                    "Please refresh the token."
                ),
                "status_code": query_bill_count_resp.get('status_code')
            }

        return {
            "message": query_bill_count_resp_message,
            "status_code": query_bill_count_resp.get('status_code')
        }


    if query_bill_count_resp.get('status_code') == 200:

        query_bill_count_resp_message_dict = json.loads(query_bill_count_resp_message)

        query_response = query_bill_count_resp_message_dict.get('QueryResponse', '')

        total_count = query_response.get('totalCount', '')

        if total_count == '':
            total_count = 0


    if total_count < 1000:
        # build uri for bill, pass in url response message and pass realmid
        bill_uri = build_bill_uri(
            urls=pers_read_intuit_urls_resp.get('message'),
            realm_id=REALM_ID,
            after_datetime=LAST_UPDATE
        )


        query_bill_resp = query_intuit_bill_info(
            uri=bill_uri,
            access_token=ACCESS_TOKEN
        )


        query_bill_resp_message = query_bill_resp.get('message')


        if query_bill_resp.get('status_code') == 200:
            query_bill_resp_message_dict = json.loads(query_bill_resp_message)
            process_bill_message(
                message_decoded=query_bill_resp_message_dict,
                realmId=REALM_ID
            )

    else:

        for i in range(1, total_count, 25):

            bill_uri = build_bill_uri(
                urls=pers_read_intuit_urls_resp.get('message'),
                realm_id=REALM_ID,
                after_datetime=LAST_UPDATE,
                start_position=i,
                max_results=25
            )

            query_bill_resp = query_intuit_bill_info(
                uri=bill_uri,
                access_token=ACCESS_TOKEN
            )

            query_bill_resp_message = query_bill_resp.get('message')

            if query_bill_resp.get('status_code') == 200:
                query_bill_resp_message_dict = json.loads(query_bill_resp_message)
                process_bill_message(
                    message_decoded=query_bill_resp_message_dict,
                    realmId=REALM_ID
                )

    return {
        "message": "",
        "rowcount": total_count,
        "status_code": ""
    }




def build_create_bill_uri(urls, realm_id):
    uri = ""
    base = ""
    url = ""
    for row in urls:
        name = row.name
        slug = row.slug
        if name == 'base':
            base = slug
        elif name == 'createbill':
            url = slug
    uri = base + url.format(realm_id)
    return uri


def create_bill(uri, access_token, bill_data):
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


def create_a_bill_process(bill):

    # read realmId from intuit_auth in database
    pers_intuit_auth_resp = pers_intuit_auth.read_db_intuit_auth()

    # if status code is successful, set REALM_ID and ACCESS_TOKEN to be used downstream
    if isinstance(pers_intuit_auth_resp, SuccessResponse):
        row = pers_intuit_auth_resp.data
        REALM_ID = row.realm_id
        ACCESS_TOKEN = row.access_token

    # if status code is not sucessful, return dict and pass message and status_code.
    else:

        return {
            "message": pers_intuit_auth_resp.message,
            "status_code": pers_intuit_auth_resp.status_code
        }




    # read intuit urls from database
    pers_read_intuit_urls_resp = pers_intuit_urls.read_intuit_urls()

    # if status code is successful, build the uris needed to call bill intuit endpoint
    if isinstance(pers_read_intuit_urls_resp, SuccessResponse):

        # build uri for bill count, pass in url response message and pass realmid
        create_bill_uri = build_create_bill_uri(
            urls=pers_read_intuit_urls_resp.data,
            realm_id=REALM_ID,
        )

    else:

        return {
            "message": pers_read_intuit_urls_resp.message,
            "status_code": pers_read_intuit_urls_resp.status_code
        }

    line_item_list = []
    bill_line_items = bill.get('line_items')

    for bill_line_item in bill_line_items:

        if bill_line_item.get('is_billable'):
            billable_status = "Billable"
        else:
            billable_status = "NotBillable"

        pers_buildone_sub_cost_code_resp = pers_sub_cost_code.read_buildone_sub_cost_code_by_guid(
            sub_cost_code_guid=bill_line_item.get('item_ref_value')
        )
        if isinstance(pers_buildone_sub_cost_code_resp, SuccessResponse):
            item_ref_value = pers_buildone_sub_cost_code_resp.data.intuit_item_id
        else:
            item_ref_value = None

        pers_buildone_project_resp = pers_project.read_buildone_project_by_guid(
            guid=bill_line_item.get('customer_ref_value')
        )
        if isinstance(pers_buildone_project_resp, SuccessResponse):
            customer_ref_value = pers_buildone_project_resp.data.intuit_customer_id
        else:
            customer_ref_value = None

        line_item_list.append(
            {
                "Description": bill_line_item.get('description'),
                "Amount": bill_line_item.get('amount'),
                "DetailType": "ItemBasedExpenseLineDetail",
                "ItemBasedExpenseLineDetail": {
                    "CustomerRef": {
                        "value": customer_ref_value
                    },
                    "BillableStatus": billable_status,
                    "ItemRef": {
                        "value": item_ref_value
                    },
                    "UnitPrice": bill_line_item.get('unit_price'),
                    "MarkupInfo": {
                        "Percent": 0
                    },
                    "Qty": bill_line_item.get('qty')
                }
            }
        )

    vendor_ref_value = pers_vendor.read_buildone_vendor_by_guid(
        vendor_guid=bill.get('vendor_ref_value')
    ).data.intuit_vendor_id

    bill_data = {
        "DocNumber": bill.get('doc_number'),
        "TxnDate": bill.get('txn_date'),
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

    # create bill
    create_bill_response = create_bill(
        uri=create_bill_uri,
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

            return {
                "message": (
                    "An error occured because the authentication token has expired." +
                    "Please refresh the token."
                ),
                "status_code": create_bill_response.get('status_code')
            }

        return {
            "message": create_bill_resp_message,
            "status_code": create_bill_response.get('status_code')
        }

    return {
        "message": create_bill_response.get('message'),
        "status_code": create_bill_response.get('status_code')
    }
