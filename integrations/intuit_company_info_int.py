"""Module providing functions for managing Company Info from Intuit QuickBooks Online.

This module handles the business layer of extracting data from the CompanyInfo Intuit QuickBooks
online endpoint, transforming that data based on data objects, and passing that data to the
persistence layer for loading into the database.

"""
import json
import requests

from persistence import (
    pers_intuit_auth,
    pers_intuit_company_info,
    pers_intuit_data_sync,
    pers_intuit_email_address,
    pers_intuit_name_value_pair,
    pers_intuit_physical_address,
    pers_intuit_telephone_number,
    pers_intuit_urls,
    pers_intuit_web_site_address
)

REALM_ID = ""
ACCESS_TOKEN = ""


def process_name_value(company_info):
    """Processes the Name Value pairs of CompanyInfo

    Retrieves the NameValue list and Id from CompanyInfo, and then iterates through each to either
    create a new database record or update the existing.

    Args:
        company_info: A dict of CompanyInfo
    
    Returns:
        A dict returning the message, and status_code as a result of the persistence layer.
    """

    # define name value list variable and company info id, or return '' if not found
    name_value_list = company_info.get('NameValue')
    company_info_id = company_info.get('Id','')

    # loop through each item in the name value list
    for name_value in name_value_list:

        # define name and value variables, or return '' if not found
        name = name_value.get('Name', '')
        value = name_value.get('Value', '')

        # call database and read name value pair record by name and company id
        pers_intuit_read_name_value_pair_response = pers_intuit_name_value_pair.\
            read_name_value_pair_by_name_and_company_id(
                name=name,
                company_info_id=company_info_id
            )

        # if record exists, update the database record and return the message
        if pers_intuit_read_name_value_pair_response.get('status_code') == 201:

            pers_intuit_update_value_by_name_and_company_id_response = pers_intuit_name_value_pair.\
                update_value_by_name_and_company_id(
                    name=name,
                    value=value,
                    company_info_id=company_info_id
                )

            if pers_intuit_update_value_by_name_and_company_id_response.get('status_code') != 201:

                return {
                    "message": pers_intuit_update_value_by_name_and_company_id_response.\
                        get('message'),
                    "status_code": pers_intuit_update_value_by_name_and_company_id_response.\
                        get('status_code')
                }

        # if record does not exist, create the database record and return the message
        elif pers_intuit_read_name_value_pair_response.get('status_code') == 501:

            pers_intuit_create_name_value_pair_response = pers_intuit_name_value_pair.\
                create_name_value_pair(
                    name=name,
                    value=value,
                    company_info_id=company_info_id
                )

            if pers_intuit_create_name_value_pair_response.get('status_code') != 201:

                return {
                    "message": pers_intuit_create_name_value_pair_response.get('message'),
                    "status_code": pers_intuit_create_name_value_pair_response.get('status_code')
                }

    return {
        "message": pers_intuit_read_name_value_pair_response.get('message'),
        "status_code": pers_intuit_read_name_value_pair_response.get('status_code')
    }


def process_web_address(company_info):
    """Processes the Web Address of CompanyInfo

    Retrieves the WebAddr, URI and Id from CompanyInfo, and then either creates a new database
    record or update the existing.

    Args:
        company_info: A dict of CompanyInfo
    
    Returns:
        A dict returning the message, and status_code as a result of the persistence layer.
    """

    # define web address variables, or return '' if not found
    web_address = company_info.get('WebAddr')
    web_address_uri = web_address.get('URI')
    company_info_id = company_info.get('Id','')

    # call database and read web address record by company id
    pers_intuit_read_web_site_address_by_company_id_response = pers_intuit_web_site_address.\
        read_web_site_address_by_company_id(
            company_id=company_info_id
        )

    # if record exists, update the database record and return the message
    if pers_intuit_read_web_site_address_by_company_id_response.get('status_code') == 201:

        pers_intuit_update_web_site_address_by_company_id_response = pers_intuit_web_site_address.\
            update_web_site_address_by_company_id(
                uri=web_address_uri,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_update_web_site_address_by_company_id_response.get('message'),
            "status_code": pers_intuit_update_web_site_address_by_company_id_response.\
                get('status_code')
        }

    # if record does not exist, create the database record and return the message
    if pers_intuit_read_web_site_address_by_company_id_response.get('status_code') == 501:

        pers_intuit_create_web_site_address_response = pers_intuit_web_site_address.\
            create_web_site_address(
                uri=web_address_uri,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_create_web_site_address_response.get('message'),
            "status_code": pers_intuit_create_web_site_address_response.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_read_web_site_address_by_company_id_response.get('message'),
        "status_code": pers_intuit_read_web_site_address_by_company_id_response.get('status_code')
    }


def process_email(company_info):

    # define email variables, or return '' if not found
    email_address = company_info.get('Email')
    email = email_address.get('Address')
    company_info_id = company_info.get('Id','')

    # call database and read primary phone record by company id
    pers_intuit_read_email_address_by_company_id_response = pers_intuit_email_address.\
        read_email_address_by_company_id(
            company_id=company_info_id
        )

    # if record exists, update the database record and return the message
    if pers_intuit_read_email_address_by_company_id_response.get('status_code') == 201:

        pers_intuit_update_email_address_by_company_id_response = pers_intuit_email_address.\
            update_email_address_by_company_id(
                email_address=email,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_update_email_address_by_company_id_response.get('message'),
            "status_code": pers_intuit_update_email_address_by_company_id_response.get('status_code')
        }

    # if record does not exist, create the database record and return the message
    elif pers_intuit_read_email_address_by_company_id_response.get('status_code') == 501:

        pers_intuit_create_email_address_response = pers_intuit_email_address.\
            create_email_address(
                email_address=email,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_create_email_address_response.get('message'),
            "status_code": pers_intuit_create_email_address_response.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_read_email_address_by_company_id_response.get('message'),
        "status_code": pers_intuit_read_email_address_by_company_id_response.get('status_code')
    }


def process_primary_phone(company_info):

    # define primary phone variables, or return '' if not found
    primary_phone = company_info.get('PrimaryPhone')
    free_form_number = primary_phone.get('FreeFormNumber')
    company_info_id = company_info.get('Id','')

    # call database and read primary phone record by company id
    pers_intuit_read_telephone_number_by_company_id_response = pers_intuit_telephone_number.\
        read_telephone_number_by_company_id(
            company_id=company_info_id
        )

    # if record exists, update the database record and return the message
    if pers_intuit_read_telephone_number_by_company_id_response.get('status_code') == 201:

        pers_intuit_update_telephone_number_by_company_id_response = pers_intuit_telephone_number.\
            update_telephone_number_by_company_id(
                telephone_number=free_form_number,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_update_telephone_number_by_company_id_response.get('message'),
            "status_code": pers_intuit_update_telephone_number_by_company_id_response.get('status_code')
        }

    # if record does not exist, create the database record and return the message
    elif pers_intuit_read_telephone_number_by_company_id_response.get('status_code') == 501:

        pers_intuit_create_telephone_number_response = pers_intuit_telephone_number.\
            create_telephone_number(
                telephone_number=free_form_number,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_create_telephone_number_response.get('message'),
            "status_code": pers_intuit_create_telephone_number_response.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_read_telephone_number_by_company_id_response.get('message'),
        "status_code": pers_intuit_read_telephone_number_by_company_id_response.get('status_code')
    }


def process_customer_communication_email_address(company_info):

    # define customer communication email address variables, or return '' if not found
    customer_communication_email_address = company_info.get('CustomerCommunicationEmailAddr')
    email_address = customer_communication_email_address.get('Address')
    company_info_id = company_info.get('Id','')

    # call database and read customer communication email address record by id and company id
    pers_intuit_read_email_address_by_company_id_response = pers_intuit_email_address.\
        read_email_address_by_company_id(
            company_id=company_info_id
        )

    # if record exists, update the database record and return the message
    if pers_intuit_read_email_address_by_company_id_response.get('status_code') == 201:

        pers_intuit_update_email_address_by_company_id_response = pers_intuit_email_address.\
            update_email_address_by_company_id(
                email_address=email_address,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_update_email_address_by_company_id_response.get('message'),
            "status_code": pers_intuit_update_email_address_by_company_id_response.\
                get('status_code')
        }

    # if record does not exist, create the database record and return the message
    elif pers_intuit_read_email_address_by_company_id_response.get('status_code') == 501:

        pers_intuit_create_email_address_response = pers_intuit_email_address.create_email_address(
            email_address=email_address,
            company_info_id=company_info_id
        )

        return {
            "message": pers_intuit_create_email_address_response.get('message'),
            "status_code": pers_intuit_create_email_address_response.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_read_email_address_by_company_id_response.get('message'),
        "status_code": pers_intuit_read_email_address_by_company_id_response.get('status_code')
    }


def process_legal_address(company_info):

    # define company legal address variables, or return '' if not found
    legal_address = company_info.get('LegalAddr','')
    legal_address_id = legal_address.get('Id','')
    legal_address_line_one = legal_address.get('Line1','')
    legal_address_city = legal_address.get('City','')
    legal_address_country = legal_address.get('Country','')
    legal_address_country_sub_division_code = legal_address.get('CountrySubDivisionCode','')
    legal_addresss_postal_code = legal_address.get('PostalCode','')
    company_info_id = company_info.get('Id','')

    # call database and read company address record by id and company id
    pers_intuit_read_physical_address_by_id_and_company_id_response = pers_intuit_physical_address.\
        read_physical_address_by_id_and_company_id(
            id=legal_address_id,
            company_id=company_info_id
        )

    # if record exists, update the database record and return the message
    if pers_intuit_read_physical_address_by_id_and_company_id_response.get('status_code') == 201:

        pers_intuit_update_physical_address_by_id_and_company_id_response = pers_intuit_physical_address.\
            update_physical_address_by_id_and_company_id(
                id=legal_address_id,
                postal_code=legal_addresss_postal_code,
                city=legal_address_city,
                country=legal_address_country,
                line_one=legal_address_line_one,
                country_sub_division_code=legal_address_country_sub_division_code,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_update_physical_address_by_id_and_company_id_response.\
                get('message'),
            "status_code": pers_intuit_update_physical_address_by_id_and_company_id_response.\
                get('status_code')
        }

    # if record does not exist, create the database record and return the message
    elif pers_intuit_read_physical_address_by_id_and_company_id_response.get('status_code') == 501:

        pers_intuit_create_physical_address_response = pers_intuit_physical_address.\
            create_physical_address(
                id=legal_address_id,
                postal_code=legal_addresss_postal_code,
                city=legal_address_city,
                country=legal_address_country,
                line_one=legal_address_line_one,
                country_sub_division_code=legal_address_country_sub_division_code,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_create_physical_address_response.get('message'),
            "status_code": pers_intuit_create_physical_address_response.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_read_physical_address_by_id_and_company_id_response.get('message'),
        "status_code": pers_intuit_read_physical_address_by_id_and_company_id_response.\
            get('status_code')
    }


def process_customer_communication_address(company_info):

    # define company communication address variables, or return '' if not found
    customer_communication_address = company_info.get('CustomerCommunicationAddr','')
    customer_communication_address_id = customer_communication_address.get('Id','')
    customer_communication_address_line_one = customer_communication_address.get('Line1','')
    customer_communication_address_city = customer_communication_address.get('City','')
    customer_communication_address_country = customer_communication_address.get('Country','')
    customer_communication_address_country_sub_division_code = customer_communication_address.\
        get('CountrySubDivisionCode','')
    customer_communication_address_postal_code = customer_communication_address.get('PostalCode','')
    company_info_id = company_info.get('Id','')

    # call database and read company address record by id and company id
    pers_intuit_read_physical_address_by_id_and_company_id_response = pers_intuit_physical_address.\
        read_physical_address_by_id_and_company_id(
            id=customer_communication_address_id,
            company_id=company_info_id
        )

    # if record exists, update the database record and return the message
    if pers_intuit_read_physical_address_by_id_and_company_id_response.get('status_code') == 201:

        pers_intuit_update_physical_address_by_id_and_company_id_response = pers_intuit_physical_address.\
            update_physical_address_by_id_and_company_id(
                id=customer_communication_address_id,
                postal_code=customer_communication_address_postal_code,
                city=customer_communication_address_city,
                country=customer_communication_address_country,
                line_one=customer_communication_address_line_one,
                country_sub_division_code=customer_communication_address_country_sub_division_code,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_update_physical_address_by_id_and_company_id_response.\
                get('message'),
            "status_code": pers_intuit_update_physical_address_by_id_and_company_id_response.\
                get('status_code')
        }

    # if record does not exist, create the database record and return the message
    elif pers_intuit_read_physical_address_by_id_and_company_id_response.get('status_code') == 501:
        
        pers_intuit_create_physical_address_response = pers_intuit_physical_address.\
            create_physical_address(
                id=customer_communication_address_id,
                postal_code=customer_communication_address_postal_code,
                city=customer_communication_address_city,
                country=customer_communication_address_country,
                line_one=customer_communication_address_line_one,
                country_sub_division_code=customer_communication_address_country_sub_division_code,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_create_physical_address_response.get('message'),
            "status_code": pers_intuit_create_physical_address_response.get('status_code')
        }


    # if there is an error, the return the message
    return {
        "message": pers_intuit_read_physical_address_by_id_and_company_id_response.get('message'),
        "status_code": pers_intuit_read_physical_address_by_id_and_company_id_response.\
            get('status_code')
    }


def process_company_address(company_info):

    # define company address variables, or return '' if not found
    company_address = company_info.get('CompanyAddr','')
    company_address_id = company_address.get('Id','')
    company_address_line_one = company_address.get('Line1','')
    company_address_city = company_address.get('City','')
    company_address_country = company_address.get('Country','')
    company_address_country_sub_division_code = company_address.get('CountrySubDivisionCode','')
    company_address_postal_code = company_address.get('PostalCode','')
    company_info_id = company_info.get('Id','')

    # call database and read company address record by id and company id
    pers_intuit_read_physical_address_by_id_and_company_id_response = pers_intuit_physical_address.\
        read_physical_address_by_id_and_company_id(
            id=company_address_id,
            company_id=company_info_id
        )

    # if record exists, update the database record and return the message
    if pers_intuit_read_physical_address_by_id_and_company_id_response.get('status_code') == 201:

        pers_intuit_update_physical_address_by_id_and_company_id_response = pers_intuit_physical_address.\
            update_physical_address_by_id_and_company_id(
                id=company_address_id,
                postal_code=company_address_postal_code,
                city=company_address_city,
                country=company_address_country,
                line_one=company_address_line_one,
                country_sub_division_code=company_address_country_sub_division_code,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_update_physical_address_by_id_and_company_id_response.\
                get('message'),
            "status_code": pers_intuit_update_physical_address_by_id_and_company_id_response.\
                get('status_code')
        }

    # if record does not exist, create the database record and return the message
    elif pers_intuit_read_physical_address_by_id_and_company_id_response.get('status_code') == 501:

        pers_intuit_create_physical_address_response = pers_intuit_physical_address.\
            create_physical_address(
                id=company_address_id,
                postal_code=company_address_postal_code,
                city=company_address_city,
                country=company_address_country,
                line_one=company_address_line_one,
                country_sub_division_code=company_address_country_sub_division_code,
                company_info_id=company_info_id
            )

        return {
            "message": pers_intuit_create_physical_address_response.get('message'),
            "status_code": pers_intuit_create_physical_address_response.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_read_physical_address_by_id_and_company_id_response.get('message'),
        "status_code": pers_intuit_read_physical_address_by_id_and_company_id_response.\
            get('status_code')
    }


def process_company_info(company_info, meta_data, realm_id):

    # define company info variables, or return '' if not found
    company_info_id = company_info.get('Id','')
    sync_token = company_info.get('SyncToken','')
    company_name = company_info.get('CompanyName','')
    supported_languages = company_info.get('SupportedLanguages','')
    country = company_info.get('Country','')
    fiscal_year_start_month = company_info.get('FiscalYearStartMonth','')
    legal_name = company_info.get('LegalName','')
    company_start_date = company_info.get('CompanyStartDate','')
    employer_id = company_info.get('EmployerId','')
    domain = company_info.get('domain','')
    sparse = company_info.get('sparse','')
    created_datetime = meta_data.get('CreateTime','')
    last_update_datetime = meta_data.get('LastUpdatedTime','')

    # call database and read company info record by company id
    pers_intuit_company_info_response = pers_intuit_company_info.\
        read_company_info_by_id(id=company_info_id)

    # if record exists, update the database record and return the message
    if pers_intuit_company_info_response.get('status_code') == 201:

        pers_intuit_update_company_info_resp = pers_intuit_company_info.update_company_info(
            realm_id=realm_id,
            id=company_info_id,
            sync_token=sync_token,
            company_name=company_name,
            supported_languages=supported_languages,
            country=country,
            fiscal_year_start_month=fiscal_year_start_month,
            legal_name=legal_name,
            company_start_date=company_start_date,
            employer_id=employer_id,
            domain=domain,
            sparse=sparse,
            created_datetime=created_datetime,
            last_update_datetime=last_update_datetime
        )

        return {
            "message": pers_intuit_update_company_info_resp.get('message'),
            "rowcount": pers_intuit_update_company_info_resp.get('rowcount'),
            "status_code": pers_intuit_update_company_info_resp.get('status_code')
        }

    # if record does not exist, create the database record and return the message
    elif pers_intuit_company_info_response.get('status_code') == 501:

        pers_intuit_create_company_info_resp = pers_intuit_company_info.create_company_info(
            realm_id=realm_id,
            id=company_info_id,
            sync_token=sync_token,
            company_name=company_name,
            supported_languages=supported_languages,
            country=country,
            fiscal_year_start_month=fiscal_year_start_month,
            legal_name=legal_name,
            company_start_date=company_start_date,
            employer_id=employer_id,
            domain=domain,
            sparse=sparse,
            created_datetime=created_datetime,
            last_update_datetime=last_update_datetime
        )

        return {
            "message": pers_intuit_create_company_info_resp.get('message'),
            "rowcount": pers_intuit_create_company_info_resp.get('rowcount'),
            "status_code": pers_intuit_create_company_info_resp.get('status_code')
        }

    # if there is an error, the return the message
    return {
        "message": pers_intuit_company_info_response.get('message'),
        "rowcount": pers_intuit_company_info_response.get('rowcount'),
        "status_code": pers_intuit_company_info_response.get('status_code')
    }


def process_company_info_message(message_decoded, realmId):

    # get query response from decoded message dict
    query_response = message_decoded.get('QueryResponse')

    # get query response time from decoded message dict
    query_response_time = message_decoded.get('time')

    # this query endpoint should only return 1 max result
    # if so, then grab the first item in the list as the company info
    # also get the meta data dict
    if query_response.get('maxResults') == 1:
        company_info = query_response.get('CompanyInfo')[0]
        meta_data = company_info.get('MetaData')
    else:
        return {
            "message": "The query response returned a different number of results than 1.",
            "status_code": 500
        }

    # call the database and read the company info already saved
    pers_read_intuit_company_info_resp = pers_intuit_company_info.read_company_info_by_id(
        id=company_info.get('Id')
    )

    if pers_read_intuit_company_info_resp.get('status_code') == 201:

        # get the response message
        pers_read_intuit_company_info_resp_message = pers_read_intuit_company_info_resp.\
            get('message')

        # set the company info id of the database record
        db_id = pers_read_intuit_company_info_resp_message.__getattribute__('Id')

        # if the company info database id equals the intuit query company info id
        # but, the database sync token does not equal the intuit query sync token
        if db_id == company_info.get('Id'):

            # step through a series of company info updates, but exit if response is not successful
            # 1. process company info
            process_company_info_response = process_company_info(
                company_info=company_info,
                meta_data=meta_data,
                realm_id=realmId
            )

            # 2. process company address
            process_company_address(company_info=company_info).get('status_code')

            # 3. process customer communication address
            process_customer_communication_address(company_info=company_info).get('status_code')

            # 4. process legal address
            process_legal_address(company_info=company_info).get('status_code')

            # 5. process customer communication email address
            process_customer_communication_email_address(company_info=company_info).\
                get('status_code')

            # 6. process primary phone
            process_primary_phone(company_info=company_info).get('status_code')

            # 7. process email
            process_email(company_info=company_info).get('status_code')

            # 8. process web_address
            process_web_address(company_info=company_info).get('status_code')

            # 9. process name value 
            process_name_value(company_info=company_info).get('status_code')

            pers_intuit_data_sync.update_intuit_data_sync_by_data_source_name(
                data_source_name="companyinfo",
                last_update_datetime=query_response_time
            )

            return {
                "message": "The company info has been updated.",
                "rowcount": process_company_info_response.get('rowcount'),
                "status_code": 200
            }

    elif pers_read_intuit_company_info_resp.get('status_code') == 501:

        # step through a series of company info updates, but exit if response is not successful
        # 1. process company info
        process_company_info_response = process_company_info(
            company_info=company_info,
            meta_data=meta_data,
            realm_id=realmId
        )

        # 2. process company address
        process_company_address(company_info=company_info).get('status_code')

        # 3. process customer communication address
        process_customer_communication_address(company_info=company_info).get('status_code')

        # 4. process legal address
        process_legal_address(company_info=company_info).get('status_code')

        # 5. process customer communication email address
        process_customer_communication_email_address(company_info=company_info).get('status_code')

        # 6. process primary phone
        process_primary_phone(company_info=company_info).get('status_code')

        # 7. process email
        process_email(company_info=company_info).get('status_code')

        # 8. process web_address
        process_web_address(company_info=company_info).get('status_code')

        # 9. process name value 
        process_name_value(company_info=company_info).get('status_code')

        pers_intuit_data_sync.update_intuit_data_sync_by_data_source_name(
            data_source_name="companyinfo",
            last_update_datetime=query_response_time
        )

        return {
            "message": "The company info has been created.",
            "rowcount": process_company_info_response.get('rowcount'),
            "status_code": 200
        }

    else:

        return {
            "message": pers_read_intuit_company_info_resp.get('message'),
            "status_code": pers_read_intuit_company_info_resp.get('status_code')
        }


def query_intuit_company_info(uri, access_token):
    # try to request a response from the intuit company info uri endpoint
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
            "message": "An error occured while trying to call openid production configuration.",
            "status_code": 500
        }


def build_uri(urls, realmId):
    uri = ""
    base = ""
    version = ""
    url = ""
    query = "select * from CompanyInfo"
    for row in urls:
        name = row.__getattribute__('Name')
        slug = row.__getattribute__('Slug')
        if name == 'base':
            base = slug
        elif name == 'minorversion':
            version = slug
        elif name == 'querycompanyinfo':
            url = slug
    uri = base + url.format(realmId, query, version)
    #uri = base + url.format(realmId, query)
    return uri


def run_company_info_process():

    # read realmId from intuit_auth in database
    pers_intuit_auth_resp = pers_intuit_auth.read_db_intuit_auth()

    if pers_intuit_auth_resp.get('status_code') == 201:

        row = pers_intuit_auth_resp.get('message')

        REALM_ID = row.__getattribute__('RealmId')

        ACCESS_TOKEN = row.__getattribute__('AccessToken')

    else:

        return {
            "message": pers_intuit_auth_resp.get('message'),
            "status_code": pers_intuit_auth_resp.get('status_code')
        }

    # read intuit urls from database
    pers_read_intuit_urls_resp = pers_intuit_urls.read_intuit_urls()

    if pers_read_intuit_urls_resp.get('status_code') == 201:

        # build uri for company info
        uri = build_uri(urls=pers_read_intuit_urls_resp.get('message'), realmId=REALM_ID)

    else:

        return {
            "message": pers_read_intuit_urls_resp.get('message'),
            "status_code": pers_read_intuit_urls_resp.get('status_code')
        }

    # request companyinfo from intuit
    query_company_info_resp = query_intuit_company_info(uri=uri, access_token=ACCESS_TOKEN)
    query_company_info_resp_message = query_company_info_resp.get('message')

    if query_company_info_resp.get('status_code') == 200:

        # company_info_resp_message is a string. must use json.loads to decode and return a dict
        company_info_resp_message_dict = json.loads(query_company_info_resp_message)
        
        # pass company_info_resp_message_dict to process company info
        process_company_info_message_resp = process_company_info_message(message_decoded=company_info_resp_message_dict, realmId=REALM_ID)

        return {
            "message": process_company_info_message_resp.get('message'),
            "rowcount": process_company_info_message_resp.get('rowcount'),
            "status_code": process_company_info_message_resp.get('status_code')
        }

    elif query_company_info_resp.get('status_code') == 401:

        # if this string is in query company info resposne, then authenticaion token has expired
        # possibly, in the future, we could call the connect_intuit_oauth_2_token_endpoint_refresh function
        # or the intuit_authorization_refresh endpoint
        if "message=AuthenticationFailed; errorCode=003200; statusCode=401" in query_company_info_resp_message:

            return {
                "message": "An error occured because the authentication token has expired. Please refresh the token.",
                "status_code": query_company_info_resp.get('status_code')
            }

        else:

            return {
                "message": query_company_info_resp_message,
                "status_code": query_company_info_resp.get('status_code')
            }

    else:

        return {
            "message": query_company_info_resp.get('message'),
            "status_code": query_company_info_resp.get('status_code')
        }
