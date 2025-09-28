"""
Service module for mapping Build One Customers to Intuit Customers.
"""

# python standard library imports
from datetime import datetime

# local imports
from shared.response import BusinessResponse
from integrations.intuit.persistence import pers_intuit_customer
from integrations.map import pers_map_customer_intuit_customer as pers_map_cic
from modules.customer import pers_customer


def get_available_intuit_customers() -> BusinessResponse:
    """Returns Intuit customers not yet mapped or created in Build One."""
    try:
        resp = pers_intuit_customer.read_intuit_customers_available()
        return BusinessResponse(
            data=resp.data,
            message=resp.message,
            status_code=resp.status_code,
            success=getattr(resp, 'success', resp.status_code == 200),
            timestamp=datetime.now()
        )
    except Exception as e:
        return BusinessResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now())


def get_customer_mapping_by_customer_id(customer_id: int) -> BusinessResponse:
    try:
        resp = pers_map_cic.read_map_customer_intuit_customer_by_customer_id(customer_id)
        return BusinessResponse(
            data=resp.data,
            message=resp.message,
            status_code=resp.status_code,
            success=resp.success,
            timestamp=resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now())


def map_customer_to_intuit_customer(customer_id: int, intuit_customer_id: int) -> BusinessResponse:
    """Creates/updates mapping of a Customer -> Intuit Customer."""
    # ensure build one customer exists
    bo = pers_customer.read_customer_by_id(customer_id)
    if not bo.success or not bo.data:
        return BusinessResponse(data=None, message="Customer not found", status_code=404, success=False, timestamp=datetime.now())

    # ensure intuit customer exists in mirror table
    ic = pers_intuit_customer.read_intuit_customer_by_id(str(intuit_customer_id))
    if isinstance(ic, dict):
        # older pattern returns dict; treat not-found as error
        if ic.get('status_code') != 201:
            return BusinessResponse(data=None, message="Intuit Customer not found", status_code=404, success=False, timestamp=datetime.now())
    # attempt to read existing mapping
    existing = pers_map_cic.read_map_customer_intuit_customer_by_customer_id(customer_id)
    if existing.success and existing.data:
        mapping = existing.data
        mapping.intuit_customer_id = intuit_customer_id
        upd = pers_map_cic.update_map_customer_intuit_customer(mapping)
        return BusinessResponse(data=upd.data, message=upd.message, status_code=upd.status_code, success=upd.success, timestamp=upd.timestamp)
    # create new mapping
    crt = pers_map_cic.create_map_customer_intuit_customer(customer_id, intuit_customer_id)
    return BusinessResponse(data=crt.data, message=crt.message, status_code=crt.status_code, success=crt.success, timestamp=crt.timestamp)


def unmap_customer_by_mapping_id(mapping_id: int) -> BusinessResponse:
    del_resp = pers_map_cic.delete_map_customer_intuit_customer_by_id(mapping_id)
    return BusinessResponse(data=del_resp.data, message=del_resp.message, status_code=del_resp.status_code, success=del_resp.success, timestamp=del_resp.timestamp)

