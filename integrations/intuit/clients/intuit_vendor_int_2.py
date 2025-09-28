"""Module to manage processes for synchronizing data from Intuit QuickBooks Online Vendor to
database.

This module receives the function call from the application layer, processes the requests to the
Intuit Quickbooks Online api, transforms the responses, and passes data to the persistence layer
for database interation.

Functions:

    run_vendor_process - this is the hub of the wheel

"""
import json
import requests

from datetime import datetime
from integrations.intuit.clients import intuit_vendor_int
from modules.vendor import pers_vendor
from shared.response import PersistenceResponse
from integrations.intuit.persistence import (
    pers_intuit_auth,
    pers_intuit_data_sync,
    pers_intuit_email_address,
    pers_intuit_urls,
    pers_intuit_vendor,
)

LAST_UPDATE = ""
REALM_ID = ""
ACCESS_TOKEN = ""


def process_intuit_vendor(realm_id, intuit_vendor):

    # call database and read vendor record by vendor id
    pers_intuit_vendor_response = pers_intuit_vendor.\
        read_intuit_vendor_by_id(vendor_id=intuit_vendor.vendor_id)

    # if record exists, update the database record and return the message
    if isinstance(pers_intuit_vendor_response, SuccessResponse):
        pers_intuit_update_vendor_resp = pers_intuit_vendor.\
            update_intuit_vendor_by_realm_id_and_vendor_id(
                realm_id=realm_id,
                intuit_vendor=intuit_vendor
            )

        if isinstance(pers_intuit_update_vendor_resp, SuccessResponse):
            return pers_intuit_update_vendor_resp.to_dict()
        elif isinstance(pers_intuit_update_vendor_resp, BusinessResponse):
            create_intuit_vendor_resp = pers_intuit_vendor.\
                create_intuit_vendor(
                    realm_id=realm_id,
                    intuit_vendor=intuit_vendor
                )
            return create_intuit_vendor_resp.to_dict()

        else:
            return pers_intuit_update_vendor_resp.to_dict()

    return pers_intuit_vendor_response.to_dict()


def process_buildone_vendor(buildone_vendor):

    # call database and read vendor record by vendor id
    pers_buildone_vendor_response = pers_vendor.\
        read_buildone_vendor_by_id(vendor_id=buildone_vendor.vendor_id)

    if isinstance(pers_buildone_vendor_response, SuccessResponse):
        database_buildone_vendor = pers_buildone_vendor_response.data
        buildone_vendor.transaction_id = database_buildone_vendor.transaction_id
        pers_buildone_update_vendor_resp = pers_vendor.\
            update_buildone_vendor_intuit_sync(buildone_vendor=buildone_vendor)

    elif isinstance(pers_buildone_vendor_response, BusinessResponse):

        # call database and read vendor record by vendor name
        pers_buildone_vendor_response = pers_vendor.\
            read_buildone_vendor_by_name(vendor_name=buildone_vendor.vendor_name)


        # if record exists, update the database record and return the message
        if isinstance(pers_buildone_vendor_response, SuccessResponse):
            database_buildone_vendor = pers_buildone_vendor_response.data
            buildone_vendor.transaction_id = database_buildone_vendor.transaction_id
            pers_buildone_update_vendor_resp = pers_vendor.\
                update_buildone_vendor_intuit_sync(buildone_vendor=buildone_vendor)

            return pers_buildone_update_vendor_resp.to_dict()

        elif isinstance(pers_buildone_vendor_response, BusinessResponse):
            pers_buildone_create_vendor_resp = pers_vendor.\
                create_buildone_vendor_intuit_sync(buildone_vendor=buildone_vendor)

            return pers_buildone_create_vendor_resp.to_dict()

    return pers_buildone_vendor_response.to_dict()


def process_vendor_message(message_decoded, realm_id):

    # get query response from decoded message dict
    query_response = message_decoded.get('QueryResponse')

    # get query response time from decoded message dict
    query_response_time = message_decoded.get('time')

    # get vendor list, start position, and max_results
    vendor_list = query_response.get('Vendor')
    start_position = query_response.get('startPosition')
    max_results = query_response.get('maxResults')
    vendor_count = 0

    if max_results == 0 or max_results is None:
        return {
            "message": "The Vendor process has completed.",
            "rowcount": 0,
            "status_code": 201
        }

    for _vendor in vendor_list:

        vendor_count += 1

        domain = _vendor.get('domain', '')

        meta_data = _vendor.get('MetaData', '')
        if meta_data == '':
            created_datetime = ''
            last_update_datetime = ''
        else:
            created_datetime = meta_data.get('CreateTime', '')
            last_update_datetime = meta_data.get('LastUpdatedTime', '')

        primary_email_addr = _vendor.get('PrimaryEmailAddress', '')
        if primary_email_addr == '':
            primary_email_address = ''
        else:
            primary_email_address = primary_email_addr.get('Address', '')

        intuit_vendor = pers_intuit_vendor.IntuitVendor(
            realm_id=realm_id,
            tax_identifier=_vendor.get('TaxIdentifier', ''),
            vendor_id=_vendor.get('Id', ''),
            sync_token=_vendor.get('SyncToken', ''),
            created_datetime=created_datetime,
            last_update_datetime=last_update_datetime,
            company_name=_vendor.get('CompanyName', ''),
            display_name=_vendor.get('DisplayName', ''),
            print_on_check_name=_vendor.get('PrintOnCheckName', ''),
            active=_vendor.get('Active', ''),
            v4id_pseudonym=_vendor.get('V4IDPseudonym', ''),
            primary_email_address=primary_email_address
        )

        # 1. process intuit vendor
        process_intuit_vendor(
            realm_id=realm_id,
            intuit_vendor=intuit_vendor
        )

        # 2. process buildone vendor  
        buildone_vendor = pers_vendor.Vendor(
            vendor_created_datetime=created_datetime,
            vendor_modified_datetime=last_update_datetime,
            vendor_name=_vendor.get('DisplayName', ''),
            intuit_vendor_id=_vendor.get('Id', '')
        )

        process_buildone_vendor(
            buildone_vendor=buildone_vendor
        )

        pers_intuit_data_sync.update_intuit_data_sync_by_data_source_name(
            data_source_name="vendor",
            last_update_datetime=query_response_time
        )

    return {
        "message": "The Vendor process has completed.",
        "rowcount": vendor_count,
        "status_code": 201
    }


def run_vendor_process():
    """
    """

    # read the datasync datetime stamp when the last update was recorded
    pers_intuit_data_sync_resp = pers_intuit_data_sync.read_intuit_data_sync_by_data_source_name(
        data_source_name='vendor'
    )

    if isinstance(pers_intuit_data_sync_resp, SuccessResponse):
        sync_record = pers_intuit_data_sync_resp.data['sync_record']
        last_update_datetime = getattr(sync_record, 'LastUpdateDatetime')
        LAST_UPDATE = last_update_datetime.strftime("%Y-%m-%dT%H:%M:%S")

    else:
        return pers_intuit_data_sync_resp.to_dict()


    # read realmId from intuit_auth in database
    pers_intuit_auth_resp = pers_intuit_auth.read_db_intuit_auth()

    if isinstance(pers_intuit_auth_resp, SuccessResponse):
        REALM_ID = pers_intuit_auth_resp.data['auth_record'].realm_id
        ACCESS_TOKEN = pers_intuit_auth_resp.data['auth_record'].access_token
    else:
        return pers_intuit_auth_resp.to_dict()


    # read intuit urls from database
    pers_read_intuit_urls_resp = pers_intuit_urls.read_intuit_urls()

    if isinstance(pers_read_intuit_urls_resp, SuccessResponse):
        urls = pers_read_intuit_urls_resp.data['urls_records']
    else:
        return pers_read_intuit_urls_resp.to_dict()


    # request vendor count from intuit
    total_count = 0
    query_vendor_count_resp = intuit_vendor_int.query_intuit_vendor_count_info(
        urls=urls,
        realm_id=REALM_ID,
        last_update=LAST_UPDATE,
        access_token=ACCESS_TOKEN
    )
    query_vendor_count_resp_message = query_vendor_count_resp.get('message')


    if query_vendor_count_resp.get('status_code') == 401:

        # if this string is in query company info resposne, then authenticaion token has expired
        # possibly, in the future, we could call the connect_intuit_oauth_2_token_endpoint_refresh
        # function or the intuit_authorization_refresh endpoint
        s = "message=AuthenticationFailed; errorCode=003200; statusCode=401"
        if s in query_vendor_count_resp_message:

            return {
                "message": (
                    "An error occured because the authentication token has expired. " +
                    "Please refresh the token."
                ),
                "status_code": query_vendor_count_resp.get('status_code')
            }

        return {
            "message": query_vendor_count_resp_message,
            "status_code": query_vendor_count_resp.get('status_code')
        }


    if query_vendor_count_resp.get('status_code') == 200:

        query_vendor_count_resp_message_dict = json.loads(query_vendor_count_resp_message)

        query_response = query_vendor_count_resp_message_dict.get('QueryResponse', '')

        total_count = query_response.get('totalCount', '')

        if total_count == '':
            total_count = 0


    if total_count < 1000:

        query_vendor_resp = intuit_vendor_int.query_intuit_vendor_info(
            urls=urls,
            realm_id=REALM_ID,
            last_update=LAST_UPDATE,
            access_token=ACCESS_TOKEN
        )

        query_vendor_resp_message = query_vendor_resp.get('message')

        if query_vendor_resp.get('status_code') == 200:
            query_vendor_resp_message_dict = json.loads(query_vendor_resp_message)
            process_vendor_message(
                message_decoded=query_vendor_resp_message_dict,
                realm_id=REALM_ID
            )

    else:

        for i in range(1, total_count, 25):

            query_vendor_resp = intuit_vendor_int.query_intuit_vendor_info(
                urls=urls,
                realm_id=REALM_ID,
                last_update=LAST_UPDATE,
                access_token=ACCESS_TOKEN,
                start_position=i,
                max_results=25
            )

            query_vendor_resp_message = query_vendor_resp.get('message')

            if query_vendor_resp.get('status_code') == 200:
                query_vendor_resp_message_dict = json.loads(query_vendor_resp_message)
                process_vendor_message(
                    message_decoded=query_vendor_resp_message_dict,
                    realm_id=REALM_ID
                )

    return {
        "message": "",
        "rowcount": total_count,
        "status_code": ""
    }
