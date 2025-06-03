"""
Module for customer business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from modules.customer import pers_customer


def get_customers() -> BusinessResponse:
    """
    Retrieves all customers from the database.
    """

    pers_customers_resp = pers_customer.read_customers()

    return BusinessResponse(
        data=pers_customers_resp.data,
        message=pers_customers_resp.message,
        status_code=pers_customers_resp.status_code,
        success=pers_customers_resp.success,
        timestamp=pers_customers_resp.timestamp
    )


def get_customer_by_id(customer_id: int) -> BusinessResponse:
    """
    Retrieves a customer by its ID.
    """

    pers_buildone_customer_resp = pers_customer.read_customer_by_id(customer_id)

    return BusinessResponse(
        data=pers_buildone_customer_resp.data,
        message=pers_buildone_customer_resp.message,
        status_code=pers_buildone_customer_resp.status_code,
        success=pers_buildone_customer_resp.success,
        timestamp=pers_buildone_customer_resp.timestamp
    )


def get_customer_by_guid(customer_guid: str) -> BusinessResponse:
    """
    Retrieves a customer by its GUID.
    """

    pers_buildone_customer_resp = pers_customer.read_customer_by_guid(customer_guid)

    #print("Customer Bus:")
    #print(pers_buildone_customer_resp.data)

    return BusinessResponse(
        data=pers_buildone_customer_resp.data,
        message=pers_buildone_customer_resp.message,
        status_code=pers_buildone_customer_resp.status_code,
        success=pers_buildone_customer_resp.success,
        timestamp=pers_buildone_customer_resp.timestamp
    )


def post_customer(
        created_datetime: datetime,
        modified_datetime: datetime,
        name: str,
        is_active: int
    ) -> BusinessResponse:
    """Posts a customer to the database.
    """

    # validate customer name
    if not name or name is None or name == '':
        return BusinessResponse(
            data=None,
            message='Missing Customer name.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # check if customer name already exists.
    read_customer_pers_response = pers_customer.read_customer_by_name(name)
    if read_customer_pers_response.success:
        return BusinessResponse(
            data=read_customer_pers_response.data,
            message='Customer name already exists.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # create customer object instance
    _customer = pers_customer.Customer(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        name=name,
        is_active=is_active
    )


    # create customer in database
    post_customer_pers_response = pers_customer.create_customer(_customer)

    return BusinessResponse(
        data=post_customer_pers_response.data,
        message=post_customer_pers_response.message,
        status_code=post_customer_pers_response.status_code,
        success=post_customer_pers_response.success,
        timestamp=post_customer_pers_response.timestamp
    )
