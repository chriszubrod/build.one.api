"""Module to manage processes for synchronizing data from Intuit QuickBooks Online Item to
database.

This module receives the function call from the application layer, processes the requests to the
Intuit Quickbooks Online api, transforms the responses, and passes data to the persistence layer
for database interation.

Functions:

    run_item_process - this is the hub of the wheel

"""
import decimal
import json
import re
import requests

from datetime import datetime
from integrations import intuit_item_int
from modules.cost_code import pers_cost_code
from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse

from persistence import (
    pers_intuit_auth,
    pers_intuit_item,
    pers_intuit_data_sync,
    pers_intuit_email_address,
    pers_intuit_urls,
    pers_sub_cost_code
)

LAST_UPDATE = ""
REALM_ID = ""
ACCESS_TOKEN = ""


def parse_cost_code(text: str) -> tuple:
    """
    Parse cost code string into number and name.
    Returns tuple of (code, name) or (None, text) if no code found.
    """
    # Pattern matches strings that start with a digit
    pattern = r'^(\d[\d.a-z]*)\s+(.+)$'

    match = re.match(pattern, text.strip())
    if match:
        code, name = match.groups()

        if any(c.isalpha() for c in code):
            parts = code.split('.')
            if len(parts) == 2:
                main = str(int(parts[0]))
                sub = ''.join(c for c in parts[1] if c.isdigit())
                code = f"{main}.{sub}"
            else:
                code = ''.join(c for c in code if c.isdigit())

        return code, name
    return None, text


def convert_to_numeric(value: str) -> decimal.Decimal:
    """Convert string to Decimal with 4 decimal places."""
    try:
        # Remove any commas and spaces
        clean_value = value.replace(',', '').strip()

        # Convert to Decimal with 4 decimal places
        decimal_value = decimal.Decimal(clean_value).quantize(
            decimal.Decimal('0.0001'), 
            rounding=decimal.ROUND_HALF_UP
        )
        return decimal_value

    except (ValueError, decimal.InvalidOperation):
        return decimal.Decimal('0.0000')


def process_intuit_item(realm_id, intuit_item):
    # call database and read item record by item id
    pers_intuit_item_response = pers_intuit_item.\
        read_intuit_item_by_id(item_id=intuit_item.item_id)


    # if record exists, update the database record and return the message
    if isinstance(pers_intuit_item_response, SuccessResponse):
        pers_intuit_update_item_resp = pers_intuit_item.\
            update_intuit_item_by_realm_id_and_item_id(
                realm_id=realm_id,
                intuit_item=intuit_item
            )

        if isinstance(pers_intuit_update_item_resp, SuccessResponse):
            return pers_intuit_update_item_resp.to_dict()
        elif isinstance(pers_intuit_update_item_resp, BusinessResponse):
            create_intuit_item_resp = pers_intuit_item.\
                create_intuit_item(
                    realm_id=realm_id,
                    intuit_item=intuit_item
                )
            return create_intuit_item_resp.to_dict()

        else:
            return pers_intuit_update_item_resp.to_dict()

    return pers_intuit_item_response.to_dict()


def process_buildone_cost_code(buildone_cost_codes: list):

    for buildone_cost_code in buildone_cost_codes:
        # call database and read cost code record by IntuitItemId
        pers_buildone_cost_code_response = pers_cost_code.\
            read_buildone_cost_code_by_intuit_item_id(cost_code=buildone_cost_code)

        # if record exists, update the database record and return the message
        if isinstance(pers_buildone_cost_code_response, SuccessResponse):
            database_buildone_cost_code = pers_buildone_cost_code_response.data.get('row')
            buildone_cost_code.transaction_id = database_buildone_cost_code.transaction_id
            pers_buildone_update_cost_code_resp = pers_cost_code.\
                update_buildone_cost_code_intuit_sync(cost_code=buildone_cost_code)

            #return pers_buildone_update_cost_code_resp.to_dict()
            continue


        # call database and read cost code record by Name
        pers_buildone_cost_code_response = pers_cost_code.\
            read_buildone_cost_code_by_name(cost_code=buildone_cost_code)


        # if record exists, update the database record and return the message
        if isinstance(pers_buildone_cost_code_response, SuccessResponse):
            database_buildone_cost_code = pers_buildone_cost_code_response.data
            buildone_cost_code.transaction_id = database_buildone_cost_code.transaction_id

            pers_buildone_update_cost_code_resp = pers_cost_code.\
                update_buildone_cost_code_intuit_sync(cost_code=buildone_cost_code)

            #return pers_buildone_update_cost_code_resp.to_dict()
            continue

        # if record does not exist, create a new record and return the message
        elif isinstance(pers_buildone_cost_code_response, BusinessResponse):
            pers_buildone_create_cost_code_resp = pers_cost_code.\
                create_buildone_cost_code_intuit_sync(cost_code=buildone_cost_code)

            #return pers_buildone_create_cost_code_resp.to_dict()
            continue

    #return pers_buildone_cost_code_response.to_dict()


def process_buildone_sub_cost_code(buildone_sub_cost_codes: list):

    for buildone_sub_cost_code in buildone_sub_cost_codes:
        # call database and read sub cost code record by IntuitItemId
        pers_buildone_sub_cost_code_response = pers_sub_cost_code.\
            read_buildone_sub_cost_code_by_intuit_item_id(sub_cost_code=buildone_sub_cost_code)

        # if record exists, update the database record and return the message
        if isinstance(pers_buildone_sub_cost_code_response, SuccessResponse):
            database_buildone_sub_cost_code = pers_buildone_sub_cost_code_response.data
            buildone_sub_cost_code.transaction_id = database_buildone_sub_cost_code.transaction_id
            buildone_sub_cost_code.sub_cost_code_id = database_buildone_sub_cost_code.\
                sub_cost_code_id

            pers_buildone_update_sub_cost_code_resp = pers_sub_cost_code.\
                update_buildone_sub_cost_code_intuit_sync(sub_cost_code=buildone_sub_cost_code)

            #return pers_buildone_update_sub_cost_code_resp.to_dict()
            continue


        # call database and read sub cost code record by Name
        pers_buildone_sub_cost_code_response = pers_sub_cost_code.\
            read_buildone_sub_cost_code_by_name(sub_cost_code=buildone_sub_cost_code)


        # if record exists, update the database record and return the message
        if isinstance(pers_buildone_sub_cost_code_response, SuccessResponse):
            database_buildone_sub_cost_code = pers_buildone_sub_cost_code_response.data
            buildone_sub_cost_code.transaction_id = database_buildone_sub_cost_code.transaction_id
            buildone_sub_cost_code.sub_cost_code_id = database_buildone_sub_cost_code.\
                sub_cost_code_id

            pers_buildone_update_sub_cost_code_resp = pers_sub_cost_code.\
                update_buildone_sub_cost_code_intuit_sync(sub_cost_code=buildone_sub_cost_code)

            #return pers_buildone_update_sub_cost_code_resp.to_dict()
            continue


        # if record does not exist, create a new record and return the message
        elif isinstance(pers_buildone_sub_cost_code_response, BusinessResponse):
            pers_buildone_create_sub_cost_code_resp = pers_sub_cost_code.\
                create_buildone_sub_cost_code_intuit_sync(sub_cost_code=buildone_sub_cost_code)

            #pers_buildone_create_sub_cost_code_resp.to_dict()
            continue

    #return pers_buildone_sub_cost_code_response.to_dict()


def process_item_message(message_decoded, realm_id):

    # get query response from decoded message dict
    query_response = message_decoded.get('QueryResponse')

    # get query response time from decoded message dict
    query_response_time = message_decoded.get('time')

    # get item list, start position, and max_results
    item_list = query_response.get('Item')
    start_position = query_response.get('startPosition')
    max_results = query_response.get('maxResults')
    item_count = 0

    if max_results == 0 or max_results is None:
        return {
            "message": "The Vendor process has completed.",
            "rowcount": 0,
            "status_code": 201
        }

    buildone_cost_code_list = []
    buildone_sub_cost_code_list = []

    for _item in item_list:

        item_count += 1

        domain = _item.get('domain', '')

        meta_data = _item.get('MetaData', '')
        if meta_data == '':
            created_datetime = ''
            last_update_datetime = ''
        else:
            created_datetime = meta_data.get('CreateTime', '')
            last_update_datetime = meta_data.get('LastUpdatedTime', '')

        parent_ref = _item.get('ParentRef', '')
        if parent_ref == '':
            parent_ref_value = ''
        else:
            parent_ref_value = parent_ref.get('value', '')

        intuit_item = pers_intuit_item.IntuitItem(
            realm_id=realm_id,
            name=_item.get('Name', ''),
            is_active=_item.get('Active', ''),
            is_sub_item=_item.get('SubItem', ''),
            parent_ref_value=parent_ref_value,
            level=_item.get('Level', ''),
            fully_qualified_name=_item.get('FullyQualifiedName', ''),
            item_id=_item.get('Id', ''),
            sync_token=_item.get('SyncToken', ''),
            created_datetime=created_datetime,
            last_update_datetime=last_update_datetime
        )

        # 1. process intuit vendor
        process_intuit_item(
            realm_id=realm_id,
            intuit_item=intuit_item
        )


        # split name into cost code/sub cost code and name
        _code, _name = parse_cost_code(intuit_item.name)

        # if code is None, then skip this item and continue to next item
        if _code is None:
            continue

        # 2. process buildone item
        # if is_sub_item is false, then process buildone cost code
        if intuit_item.is_sub_item == '':
            buildone_cost_code = pers_cost_code.CostCode(
                created_datetime=intuit_item.created_datetime,
                modified_datetime=intuit_item.last_update_datetime,
                number=convert_to_numeric(_code),
                name=_name,
                intuit_item_id=intuit_item.item_id
            )
            buildone_cost_code_list.append(buildone_cost_code)

        if intuit_item.is_sub_item == 1:
            buildone_sub_cost_code = pers_sub_cost_code.SubCostCode(
                created_datetime=intuit_item.created_datetime,
                modified_datetime=intuit_item.last_update_datetime,
                number=convert_to_numeric(_code),
                name=_name,
                cost_code_id=buildone_cost_code.cost_code_id,
                intuit_item_id=intuit_item.item_id,
                parent_ref_value=parent_ref_value
            )
            buildone_sub_cost_code_list.append(buildone_sub_cost_code)

    # process buildone cost code
    process_buildone_cost_code(
        buildone_cost_codes=buildone_cost_code_list
    )

    # process buildone sub cost code
    process_buildone_sub_cost_code(
        buildone_sub_cost_codes=buildone_sub_cost_code_list
    )

    pers_intuit_data_sync.update_intuit_data_sync_by_data_source_name(
            data_source_name="vendor",
            last_update_datetime=query_response_time
        )

    return {
        "message": "The Vendor process has completed.",
        "rowcount": item_count,
        "status_code": 201
    }

def run_item_process():
    """
    """

    # read the datasync datetime stamp when the last update was recorded
    pers_intuit_data_sync_resp = pers_intuit_data_sync.read_intuit_data_sync_by_data_source_name(
        data_source_name='item'
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







    # request item count from intuit
    total_count = 0
    query_item_count_resp = intuit_item_int.query_intuit_item_count_info(
        urls=urls,
        realm_id=REALM_ID,
        last_update=LAST_UPDATE,
        access_token=ACCESS_TOKEN
    )
    query_item_count_resp_message = query_item_count_resp.get('message')


    if query_item_count_resp.get('status_code') == 401:

        # if this string is in query resposne, then authenticaion token has expired
        # possibly, in the future, we could call the connect_intuit_oauth_2_token_endpoint_refresh
        # function or the intuit_authorization_refresh endpoint
        s = "message=AuthenticationFailed; errorCode=003200; statusCode=401"
        if s in query_item_count_resp_message:

            return {
                "message": (
                    "An error occured because the authentication token has expired. " +
                    "Please refresh the token."
                ),
                "status_code": query_item_count_resp.get('status_code')
            }

        return {
            "message": query_item_count_resp_message,
            "status_code": query_item_count_resp.get('status_code')
        }



    if query_item_count_resp.get('status_code') == 200:

        query_item_count_resp_message_dict = json.loads(query_item_count_resp_message)

        query_response = query_item_count_resp_message_dict.get('QueryResponse', '')

        total_count = query_response.get('totalCount', '')

        if total_count == '':
            total_count = 0


    if total_count < 1000:

        query_item_resp = intuit_item_int.query_intuit_item_info(
            urls=urls,
            realm_id=REALM_ID,
            last_update=LAST_UPDATE,
            access_token=ACCESS_TOKEN
        )

        query_item_resp_message = query_item_resp.get('message')

        if query_item_resp.get('status_code') == 200:
            query_item_resp_message_dict = json.loads(query_item_resp_message)
            process_item_message(
                message_decoded=query_item_resp_message_dict,
                realm_id=REALM_ID
            )
    else:

        for i in range(1, total_count, 25):

            query_item_resp = intuit_item_int.query_intuit_item_info(
                urls=urls,
                realm_id=REALM_ID,
                last_update=LAST_UPDATE,
                access_token=ACCESS_TOKEN,
                start_position=i,
                max_results=25
            )

            query_item_resp_message = query_item_resp.get('message')

            if query_item_resp.get('status_code') == 200:
                query_item_resp_message_dict = json.loads(query_item_resp_message)
                process_item_message(
                    message_decoded=query_item_resp_message_dict,
                    realm_id=REALM_ID
                )

    return {
        "message": "",
        "rowcount": total_count,
        "status_code": ""
    }
