"""
Module for payment term business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports


# local imports
from business.bus_response import BusinessResponse
from modules.payment_term import pers_payment_term


def get_payment_terms():
    """
    Retrieves all payment terms from the database.
    """

    pers_read_payment_terms_response = pers_payment_term.read_payment_terms()

    return BusinessResponse(
        data=pers_read_payment_terms_response.data,
        message=pers_read_payment_terms_response.message,
        status_code=pers_read_payment_terms_response.status_code,
        success=pers_read_payment_terms_response.success,
        timestamp=pers_read_payment_terms_response.timestamp
    )


def get_payment_term_by_guid(payment_term_guid):
    """
    Retrieves a payment term by its GUID.
    """
    pers_read_payment_term_response = pers_payment_term.\
        read_payment_term_by_guid(payment_term_guid)

    return BusinessResponse(
        data=pers_read_payment_term_response.data,
        message=pers_read_payment_term_response.message,
        status_code=pers_read_payment_term_response.status_code,
        success=pers_read_payment_term_response.success,
        timestamp=pers_read_payment_term_response.timestamp
    )


def post_payment_term(
        created_datetime: datetime,
        modified_datetime: datetime,
        name: str,
        value: str
    ) -> BusinessResponse:
    """
    Posts a payment term.
    """
    # validate name
    if not name or name == "" or name is None:
        return BusinessResponse(
            data=None,
            message="Missing Payment Term name.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # validate value
    if not value or value == "" or value is None:
        return BusinessResponse(
            data=None,
            message="Missing Payment Term value.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Check if the payment term name already exists
    read_payment_term_by_name_pers = pers_payment_term.\
        read_payment_term_by_name(name)
    if read_payment_term_by_name_pers.success:
        return BusinessResponse(
            data=None,
            message="Payment Term Name already exists.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # create payment term object instance
    _payment_term = pers_payment_term.PaymentTerm(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        name=name,
        value=value
    )

    # create payment term in database
    create_payment_term_pers_reponse = pers_payment_term.\
        create_payment_term(_payment_term)

    return BusinessResponse(
        data=create_payment_term_pers_reponse.data,
        message=create_payment_term_pers_reponse.message,
        status_code=create_payment_term_pers_reponse.status_code,
        success=create_payment_term_pers_reponse.success,
        timestamp=create_payment_term_pers_reponse.timestamp
    )
