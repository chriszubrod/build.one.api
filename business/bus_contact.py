"""
Module for contact business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from helper import function_help as fhp
from persistence import pers_contact, pers_customer, pers_user


def post_contact(
        created_datetime: datetime,
        modified_datetime: datetime,
        firstname: str,
        lastname: str,
        email: str,
        phone: str,
        customer_guid: str,
        user_guid: str
    ) -> BusinessResponse:
    """
    Posts a contact.
    """

    # validate email
    if not email or not fhp.is_valid_email(email):
        return BusinessResponse(
            data=None,
            message='Invalid or missing email.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )


    # check if email already exists.
    # an email can only be associated with one contact,
    # but a contact can have multiple email addresses.
    read_contact_pers_response = pers_contact.read_contact_by_email(email)
    if read_contact_pers_response.success:
        return BusinessResponse(
            data=read_contact_pers_response.data,
            message='Email already exists.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # get customer id or user id
    # a contact can be associated with a customer or a user.
    customer_id = None
    user_id = None
    if customer_guid == '' or customer_guid is None:
        read_user_by_guid_pers_response = pers_user.read_user_by_guid(user_guid)
        if read_user_by_guid_pers_response.success:
            user_data = read_user_by_guid_pers_response.data
            customer_id = user_data.id
        else:
            return BusinessResponse(
                data=read_user_by_guid_pers_response.data,
                message=read_user_by_guid_pers_response.message,
                status_code=read_user_by_guid_pers_response.status_code,
                success=read_user_by_guid_pers_response.success,
                timestamp=read_user_by_guid_pers_response.timestamp
            )
    elif user_guid == '' or user_guid is None:
        read_customer_by_guid_pers_response = pers_customer.read_customer_by_guid(customer_guid)
        if read_customer_by_guid_pers_response.success:
            customer_data = read_customer_by_guid_pers_response.data
            customer_id = customer_data.id
        else:
            return BusinessResponse(
                data=read_customer_by_guid_pers_response.data,
                message=read_customer_by_guid_pers_response.message,
                status_code=read_customer_by_guid_pers_response.status_code,
                success=read_customer_by_guid_pers_response.success,
                timestamp=read_customer_by_guid_pers_response.timestamp
            )

    # create contact object instance
    _contact = pers_contact.Contact(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        first_name=firstname,
        last_name=lastname,
        email=email,
        phone=phone,
        customer_id=customer_id,
        user_id=user_id
    )

    # create contact in database
    post_contact_pers_response = pers_contact.create_contact(_contact)

    return BusinessResponse(
        data=post_contact_pers_response.data,
        message=post_contact_pers_response.message,
        status_code=post_contact_pers_response.status_code,
        success=post_contact_pers_response.success,
        timestamp=post_contact_pers_response.timestamp
    )


def get_contacts() -> BusinessResponse:
    """
    Retrieves all contacts from the database.
    """
    read_contacts_pers_response = pers_contact.read_contacts()
    return BusinessResponse(
        data=read_contacts_pers_response.data,
        success=read_contacts_pers_response.success,
        message=read_contacts_pers_response.message,
        status_code=read_contacts_pers_response.status_code,
        timestamp=datetime.now(tz.tzlocal())
    )


def get_contact_by_guid(contact_guid: str) -> BusinessResponse:
    """
    Retrieves a contact by guid from the database.
    """
    # read contact by guid
    pers_read_contact_response = pers_contact.read_contact_by_guid(contact_guid)
    return BusinessResponse(
        data=pers_read_contact_response.data,
        message=pers_read_contact_response.message,
        status_code=pers_read_contact_response.status_code,
        success=pers_read_contact_response.success,
        timestamp=pers_read_contact_response.timestamp
    )


def get_contact_by_user_id(user_id: int) -> BusinessResponse:
    """
    Retrieves a contact by user id from the database.
    """
    # read contact by user id
    pers_read_contact_response = pers_contact.read_contact_by_user_id(user_id)
    return BusinessResponse(
        data=pers_read_contact_response.data,
        message=pers_read_contact_response.message,
        status_code=pers_read_contact_response.status_code,
        success=pers_read_contact_response.success,
        timestamp=pers_read_contact_response.timestamp
    )


def patch_contact_by_user_id(
        modified_datetime: datetime,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        customer_id: int,
        user_id: int
    ) -> BusinessResponse:
    """
    Patches a contact.
    """
    # read contact by user id
    pers_read_contact_response = pers_contact.read_contact_by_user_id(user_id)

    # if contact exists, update instance of contact
    if pers_read_contact_response.success:
        db_contact_data = pers_read_contact_response.data
        _contact = pers_contact.Contact(
            id=db_contact_data.id,
            guid=db_contact_data.guid,
            modified_datetime=modified_datetime,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            customer_id=customer_id,
            user_id=user_id
        )
        # update contact by user id in database
        pers_update_contact_response = pers_contact.update_contact_by_user_id(_contact)
        return BusinessResponse(
            data=pers_update_contact_response.data,
            message=pers_update_contact_response.message,
            status_code=pers_update_contact_response.status_code,
            success=pers_update_contact_response.success,
            timestamp=pers_update_contact_response.timestamp
        )
    else:
        # if contact does not exist, return message
        return BusinessResponse(
            data=None,
            message=pers_read_contact_response.message,
            status_code=pers_read_contact_response.status_code,
            success=pers_read_contact_response.success,
            timestamp=pers_read_contact_response.timestamp
        )
