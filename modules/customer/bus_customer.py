"""
Module for customer business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from shared.response import BusinessResponse
from modules.customer import pers_customer
from integrations.intuit import pers_intuit_customer
from integrations.map import pers_map_customer_intuit_customer as pers_map_cic


def get_customers() -> BusinessResponse:
    """
    Retrieves all customers from the database.
    """
    pers_resp = pers_customer.read_customers()
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def get_customer_by_id(customer_id: int) -> BusinessResponse:
    """Retrieves a customer by its ID."""
    pers_resp = pers_customer.read_customer_by_id(customer_id)
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def get_customer_by_guid(customer_guid: str) -> BusinessResponse:
    """Retrieves a customer by its GUID."""
    pers_resp = pers_customer.read_customer_by_guid(customer_guid)
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def post_customer(
        name: str,
        is_active: int
    ) -> BusinessResponse:
    """Creates a customer."""

    if not name:
        return BusinessResponse(
            data=None,
            message='Missing Customer name.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # optional: check duplicate name if available
    try:
        read_by_name = getattr(pers_customer, 'read_customer_by_name', None)
        if callable(read_by_name):
            existing = read_by_name(name)
            if existing.success and existing.data:
                return BusinessResponse(
                    data=existing.data,
                    message='Customer name already exists.',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now(tz.tzlocal())
                )
    except Exception:
        # if not available, continue without blocking creation
        pass

    _customer = pers_customer.Customer(
        name=name,
        is_active=is_active
    )

    pers_create_resp = pers_customer.create_customer(_customer)
    return BusinessResponse(
        data=pers_create_resp.data,
        message=pers_create_resp.message,
        status_code=pers_create_resp.status_code,
        success=pers_create_resp.success,
        timestamp=pers_create_resp.timestamp
    )


def patch_customer_by_guid(
        guid: str,
        name: str,
        is_active: int,
        map_customer_intuit_customer_id: int
    ) -> BusinessResponse:
    """Updates a customer by GUID."""

    if not guid:
        return BusinessResponse(
            data=None,
            message='Missing Customer guid.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    if not name:
        return BusinessResponse(
            data=None,
            message='Missing Customer name.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    read_resp = pers_customer.read_customer_by_guid(guid)
    if not read_resp.success or not read_resp.data:
        return BusinessResponse(
            data=None,
            message=read_resp.message,
            status_code=read_resp.status_code,
            success=False,
            timestamp=read_resp.timestamp
        )

    db_customer = read_resp.data

    _updated = pers_customer.Customer(
        id=db_customer.id,
        guid=db_customer.guid,
        name=name,
        is_active=db_customer.is_active if is_active is None else is_active,
        map_customer_intuit_customer_id=map_customer_intuit_customer_id
    )

    update_resp = pers_customer.update_customer_by_id(_updated)
    return BusinessResponse(
        data=update_resp.data,
        message=update_resp.message,
        status_code=update_resp.status_code,
        success=update_resp.success,
        timestamp=update_resp.timestamp
    )


def delete_customer_by_id(id: int) -> BusinessResponse:
    """Deletes a customer by Id."""
    if not id:
        return BusinessResponse(
            data=None,
            message='Missing Customer id.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    del_resp = pers_customer.delete_customer_by_id(id)
    return BusinessResponse(
        data=del_resp.data,
        message=del_resp.message,
        status_code=del_resp.status_code,
        success=del_resp.success,
        timestamp=del_resp.timestamp
    )


def get_available_intuit_customers_for_mapping() -> BusinessResponse:
    """Returns Intuit customers that are not mapped to any Build One customer (and not jobs/projects)."""
    resp = pers_map_cic.read_available_intuit_customers_for_customer_map()
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def map_customer_to_intuit_customer(
        customer_guid: str,
        intuit_customer_guid: str
    ) -> BusinessResponse:
    """Creates a mapping record linking dbo.Customer to intuit.Customer."""
    # basic validation
    if not customer_guid or not intuit_customer_guid:
        return BusinessResponse(
            data=None,
            message='Missing GUIDs for mapping',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # retrieve the customer
    customer_id = None
    read_cust = pers_customer.read_customer_by_guid(customer_guid)
    if not read_cust.success or not read_cust.data:
        return BusinessResponse(
            data=None,
            message='Customer not found',
            status_code=404,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    customer_id = read_cust.data.id
    print(f"Customer ID: {read_cust.data}")

    # retrieve the intuit customer
    intuit_customer_id = None
    read_intuit_cust = pers_intuit_customer.read_intuit_customer_by_guid(intuit_customer_guid)
    if not read_intuit_cust.success or not read_intuit_cust.data:
        return BusinessResponse(
            data=None,
            message='Intuit customer not found',
            status_code=404,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    intuit_customer_id = read_intuit_cust.data.id
    print(f"Intuit Customer ID: {read_intuit_cust.data}")

    # create mapping
    create_resp = pers_map_cic.create_map_customer_intuit_customer(customer_id, intuit_customer_id)
    return BusinessResponse(
        data=create_resp.data,
        message=create_resp.message,
        status_code=create_resp.status_code,
        success=create_resp.success,
        timestamp=create_resp.timestamp
    )


def get_mapped_intuit_customer_by_customer_id(customer_id: int) -> BusinessResponse:
    """Gets the mapped Intuit customer for a Build One customer."""
    try:
        # Get the mapping
        mapping_resp = pers_map_cic.read_map_customer_intuit_customer_by_customer_id(customer_id)
        print(f"Mapping response: {mapping_resp.data}")
        if not mapping_resp.success:
            return BusinessResponse(
                data=None,
                message="No Intuit customer mapping found",
                status_code=404,
                success=False,
                timestamp=datetime.now()
            )

        # Get the Intuit customer details
        intuit_customer_resp = pers_intuit_customer.read_intuit_customer_by_id(str(mapping_resp.data.intuit_customer_id))
        print(f"Intuit customer response: {intuit_customer_resp.data}")
        if isinstance(intuit_customer_resp, dict):
            if intuit_customer_resp.get('status_code') != 201:
                return BusinessResponse(
                    data=None,
                    message="Mapped Intuit customer not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
            intuit_customer = intuit_customer_resp.data
        else:
            if not intuit_customer_resp.success or not intuit_customer_resp.data:
                return BusinessResponse(
                    data=None,
                    message="Mapped Intuit customer not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
            intuit_customer = intuit_customer_resp.data
        
        return BusinessResponse(
            data=intuit_customer,
            message="Mapped Intuit customer found",
            status_code=200,
            success=True,
            timestamp=datetime.now()
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=str(e),
            status_code=500,
            success=False,
            timestamp=datetime.now()
        )
