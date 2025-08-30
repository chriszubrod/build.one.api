"""Module to manage processes for synchronizing data from Intuit QuickBooks Online Customer to
database.

This module receives the function call from the application layer, processes the requests to the
Intuit Quickbooks Online api, transforms the responses, and passes data to the persistence layer
for database interation.

Functions:

    run_customer_process - this is the hub of the wheel

"""
from datetime import datetime

import json
import requests

from integrations.intuit.intuit_customer_int import (
    query_intuit_customer_count_info,
    query_intuit_customer_info
)
from shared.response import BusinessResponse
from modules.project import pers_project
from shared.response import SuccessResponse
from persistence import (
    pers_customer,
    pers_intuit_auth,
    pers_intuit_customer,
    pers_intuit_data_sync,

    pers_intuit_email_address,
    pers_intuit_urls
)

LAST_UPDATE = ""
REALM_ID = ""
ACCESS_TOKEN = ""


def process_intuit_customer(intuit_customer: pers_intuit_customer.IntuitCustomer):

    # call database and read customer record by customer id
    pers_intuit_customer_response = pers_intuit_customer.\
        read_intuit_customer_by_id(customer_id=intuit_customer.id)


    # if record exists, update the database record and return the message
    if pers_intuit_customer_response.get('status_code') == 201:
        pers_intuit_update_customer_resp = pers_intuit_customer.\
            update_intuit_customer_by_realm_id_and_customer_id(
                customer=intuit_customer
            )

        return {
            "message": pers_intuit_update_customer_resp.get('message'),
            "rowcount": pers_intuit_update_customer_resp.get('rowcount'),
            "status_code": pers_intuit_update_customer_resp.get('status_code')
        }

    # if record does not exist, create the database record and return the message
    if pers_intuit_customer_response.get('status_code') == 501:

        pers_intuit_create_customer_resp = pers_intuit_customer.\
            create_intuit_customer(
                customer=intuit_customer
            )

        return {
            "message": pers_intuit_create_customer_resp.get('message'),
            "rowcount": pers_intuit_create_customer_resp.get('rowcount'),
            "status_code": pers_intuit_create_customer_resp.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_customer_response.get('message'),
        "rowcount": pers_intuit_customer_response.get('rowcount'),
        "status_code": pers_intuit_customer_response.get('status_code')
    }


def process_buildone_customer(display_name, created_datetime, last_update_datetime, active, intuit_customer_id):

    # call database and read customer record by customer name
    pers_customer_response = pers_customer.read_customer_by_name(name=display_name)

    # if record exists, update the database record and return the message
    if pers_customer_response.get('status_code') == 201:
        _id = pers_customer_response.get('message').__getattribute__('Id')
        transaction_id = pers_customer_response.get('message').__getattribute__('TransactionId')
        pers_update_customer_resp = pers_customer.update_customer_by_id(
            customer_id=_id,
            modified_datetime=last_update_datetime,
            customer_name=display_name,
            is_active=active,
            transaction_id=transaction_id,
            intuit_customer_id=intuit_customer_id
        )

        return {
            "message": pers_update_customer_resp.get('message'),
            "rowcount": pers_update_customer_resp.get('rowcount'),
            "status_code": pers_update_customer_resp.get('status_code')
        }

    # if record does not exist, create the database record and return the message
    if pers_customer_response.get('status_code') == 501:
        pers_create_customer_resp = pers_customer.create_customer(
            created_datetime=created_datetime,
            modified_datetime=last_update_datetime,
            customer_name=display_name,
            is_active=active,
            first_name="",
            last_name="",
            email="",
            phone="",
            street_one="",
            street_two="",
            city="",
            state="",
            zip_code="",
            intuit_customer_id=intuit_customer_id
        )

        return {
            "message": pers_create_customer_resp.get('message'),
            "rowcount": pers_create_customer_resp.get('rowcount'),
            "status_code": pers_create_customer_resp.get('status_code')
        }

    return {
        "message": pers_customer_response.get('message'),
        "rowcount": pers_customer_response.get('rowcount'),
        "status_code": pers_customer_response.get('status_code')
    }


def process_buildone_project():
    pass

def process_customer_message(message_decoded, realm_id):

    # get query response from decoded message dict
    query_response = message_decoded.get('QueryResponse')

    # get query response time from decoded message dict
    query_response_time = message_decoded.get('time')

    # get customer list, start position, and max_results
    customer_list = query_response.get('Customer')
    start_position = query_response.get('startPosition')
    max_results = query_response.get('maxResults')
    customer_count = 0

    if max_results == 0 or max_results == None:
        return {
            "message": "The Customer process has completed.",
            "rowcount": 0,
            "status_code": 201
        }

    for customer in customer_list:

        customer_count += 1

        meta_data = customer.get('MetaData', '')
        if meta_data == '':
            created_datetime = ''
            last_update_datetime = ''
        else:
            created_datetime = meta_data.get('CreateTime', '')
            last_update_datetime = meta_data.get('LastUpdatedTime', '')

        parent_ref = customer.get('ParentRef', '')
        if parent_ref == '':
            parent_ref_value = ''
        else:
            parent_ref_value = parent_ref.get('value', '')

        intuit_customer = pers_intuit_customer.IntuitCustomer(
            realm_id=realm_id,
            id=customer.get('Id', ''),
            display_name=customer.get('DisplayName', ''),
            sync_token=customer.get('SyncToken', ''),
            created_datetime=created_datetime,
            last_updated_datetime=last_update_datetime,
            fully_qualified_name=customer.get('FullyQualifiedName', ''),
            is_job=customer.get('Job', ''),
            parent_ref_value=parent_ref_value,
            level=customer.get('Level', ''),
            is_project=customer.get('IsProject', ''),
            client_entity_id=customer.get('ClientEntityId', ''),
            is_active=customer.get('Active', ''),
            v4id_pseudonym=customer.get('V4IDPseudonym', ''),
        )

        # step through a series of customer updates, but exit if response is not successful
        # 1. process customer
        process_intuit_customer(intuit_customer=intuit_customer)

        # if is_project, then process_project
        process_buildone_project()

        # else process_customer
        process_buildone_customer()

        pers_intuit_data_sync.update_intuit_data_sync_by_data_source_name(
            data_source_name="customer",
            last_update_datetime=query_response_time
        )

    return {
        "message": "The Customer process has completed.",
        "rowcount": customer_count,
        "status_code": 201
    }



def build_customer_count_uri(urls, realm_id, after_datetime):
    uri = ""
    base = ""
    version = ""
    url = ""
    query = (
        "select count(*) from Customer Where Metadata.LastUpdatedTime > '{}'".format(after_datetime)
    )
    for row in urls:
        name = row.__getattribute__('Name')
        slug = row.__getattribute__('Slug')
        if name == 'base':
            base = slug
        elif name == 'minorversion':
            version = slug
        elif name == 'querycustomer':
            url = slug
    uri = base + url.format(realm_id, query, version)
    return uri


def build_customer_uri(urls, realm_id, after_datetime, start_position=1, max_results=1000):
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
        "select * from Customer Where Metadata.LastUpdatedTime > '{}' STARTPOSITION {} MAXRESULTS {}".format(after_datetime, start_position, max_results)
    )
    for row in urls:
        name = row.__getattribute__('Name')
        slug = row.__getattribute__('Slug')
        if name == 'base':
            base = slug
        elif name == 'minorversion':
            version = slug
        elif name == 'querycustomer':
            url = slug
    uri = base + url.format(realm_id, query, version)
    return uri


def run_customer_process():
    """
    """

    # read the datasync datetime stamp when the last update was recorded
    pers_intuit_data_sync_resp = pers_intuit_data_sync.read_intuit_data_sync_by_data_source_name(
        data_source_name='customer'
    )
    # if status code is successful, set LAST_UPDATE to be used downstream
    if pers_intuit_data_sync_resp.get('status_code') == 201:

        row = pers_intuit_data_sync_resp.get('message')

        # LAST UPDATE is stored as a datetime object. Ex. 1900-01-01 00:00:00
        last_update_datetime = row.getattr('LastUpdateDatetime')

        # Convert to a string in ISO 8601 format. Ex. '1900-01-01T00:00:00'
        # We will use this in the intuit qbo customer query.
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
        REALM_ID = row.getattr('RealmId')
        ACCESS_TOKEN = row.getattr('AccessToken')
    # if status code is not sucessful, return dict and pass message and status_code.
    else:
        return {
            "message": pers_intuit_auth_resp.get('message'),
            "rowcount": 0,
            "status_code": pers_intuit_auth_resp.get('status_code')
        }




    # read intuit urls from database
    pers_read_intuit_urls_resp = pers_intuit_urls.read_intuit_urls()
    # if status code is successful, build the uris needed to call customer intuit endpoint
    if pers_read_intuit_urls_resp.get('status_code') == 201:
        # build uri for customer count, pass in url response message and pass realmid
        customer_count_uri = build_customer_count_uri(
            urls=pers_read_intuit_urls_resp.get('message'),
            realm_id=REALM_ID,
            after_datetime=LAST_UPDATE
        )
    else:
        return {
            "message": pers_read_intuit_urls_resp.get('message'),
            "status_code": pers_read_intuit_urls_resp.get('status_code')
        }




    # request customer count from intuit
    total_count = 0
    query_customer_count_resp = query_intuit_customer_count_info(
        uri=customer_count_uri,
        access_token=ACCESS_TOKEN
    )
    query_customer_count_resp_message = query_customer_count_resp.get('message')


    if query_customer_count_resp.get('status_code') == 401:

        # if this string is in query company info resposne, then authenticaion token has expired
        # possibly, in the future, we could call the connect_intuit_oauth_2_token_endpoint_refresh
        # function or the intuit_authorization_refresh endpoint
        s = "message=AuthenticationFailed; errorCode=003200; statusCode=401"
        if s in query_customer_count_resp_message:

            return {
                "message": (
                    "An error occured because the authentication token has expired." +
                    "Please refresh the token."
                ),
                "status_code": query_customer_count_resp.get('status_code')
            }

        return {
            "message": query_customer_count_resp_message,
            "status_code": query_customer_count_resp.get('status_code')
        }


    if query_customer_count_resp.get('status_code') == 200:

        query_customer_count_resp_message_dict = json.loads(query_customer_count_resp_message)

        query_response = query_customer_count_resp_message_dict.get('QueryResponse', '')

        total_count = query_response.get('totalCount', '')

        if total_count == '':
            total_count = 0


    if total_count < 1000:
        # build uri for customer, pass in url response message and pass realmid
        customer_uri = build_customer_uri(
            urls=pers_read_intuit_urls_resp.get('message'),
            realm_id=REALM_ID,
            after_datetime=LAST_UPDATE
        )


        query_customer_resp = query_intuit_customer_info(
            uri=customer_uri,
            access_token=ACCESS_TOKEN
        )


        query_customer_resp_message = query_customer_resp.get('message')


        if query_customer_resp.get('status_code') == 200:
            query_customer_resp_message_dict = json.loads(query_customer_resp_message)
            process_customer_message(
                message_decoded=query_customer_resp_message_dict,
                realmId=REALM_ID
            )

    else:

        for i in range(1, total_count, 25):

            customer_uri = build_customer_uri(
                urls=pers_read_intuit_urls_resp.get('message'),
                realm_id=REALM_ID,
                after_datetime=LAST_UPDATE,
                start_position=i,
                max_results=25
            )

            query_customer_resp = query_intuit_customer_info(
                uri=customer_uri,
                access_token=ACCESS_TOKEN
            )

            query_customer_resp_message = query_customer_resp.get('message')

            if query_customer_resp.get('status_code') == 200:
                query_customer_resp_message_dict = json.loads(query_customer_resp_message)
                process_customer_message(
                    message_decoded=query_customer_resp_message_dict,
                    realmId=REALM_ID
                )

    return {
        "message": "",
        "rowcount": total_count,
        "status_code": ""
    }


def get_intuit_projects():
    """
    Retrieves all projects from the database.
    """
    try:
        pers_intuit_projects_resp = pers_intuit_customer.\
            read_intuit_projects()


        if isinstance(pers_intuit_projects_resp, SuccessResponse):
            projects = pers_intuit_projects_resp.data

            return BusinessResponse(
                success=True,
                message='Projects found',
                status_code=200,
                data=projects
            )

        return BusinessResponse(
            success=False,
            message=pers_intuit_projects_resp.message,
            status_code=500,
            data=None
        )

    except Exception as e:
        return BusinessResponse(
            success=False,
            message=str(e),
            status_code=500,
            data=None
        )
