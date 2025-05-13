"""
Module for company business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from modules.company import pers_company


def get_company() -> BusinessResponse:
    """
    Retrieves a company from the database.
    """
    pers_company_resp = pers_company.read_company()

    return BusinessResponse(
        data=pers_company_resp.data,
        message=pers_company_resp.message,
        status_code=pers_company_resp.status_code,
        success=pers_company_resp.success,
        timestamp=pers_company_resp.timestamp
    )


def get_company_by_guid(
        company_guid: str
    ) -> BusinessResponse:
    """
    Retrieves a company from the database by guid.
    """
    pers_company_resp = pers_company.read_company_by_guid(company_guid)

    return BusinessResponse(
        data=pers_company_resp.data,
        message=pers_company_resp.message,
        status_code=pers_company_resp.status_code,
        success=pers_company_resp.success,
        timestamp=pers_company_resp.timestamp
    )


def post_company(
        created_datetime: datetime,
        modified_datetime: datetime,
        name: str
    ) -> BusinessResponse:
    """
    Posts a company to the database.
    """

    # validate company name
    if not name or name is None or name == '':
        return BusinessResponse(
            data=None,
            message='Missing Company name.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # create company object instance
    _company = pers_company.Company(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        name=name
    )
    
    # post company to database
    pers_company_resp = pers_company.create_company(_company)
    
    return BusinessResponse(
        data=pers_company_resp.data,
        message=pers_company_resp.message,
        status_code=pers_company_resp.status_code,
        success=pers_company_resp.success,
        timestamp=pers_company_resp.timestamp
    )


def patch_company(
        company_guid: str,
        modified_datetime: datetime,
        name: str
    ) -> BusinessResponse:
    """
    Updates a company in the database.
    """

    # validate company guid
    if not company_guid or company_guid is None or company_guid == '':
        return BusinessResponse(
            data=None,
            message='Missing Company GUID.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    
    # validate company name
    if not name or name is None or name == '':
        return BusinessResponse(
            data=None,
            message='Missing Company name.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # get database company by guid
    _company = None
    _company_resp = pers_company.read_company_by_guid(company_guid)
    if _company_resp.success:
        _company = _company_resp.data
    else:
        return BusinessResponse(
            data=None,
            message=_company_resp.message,
            status_code=_company_resp.status_code,
            success=_company_resp.success,
            timestamp=_company_resp.timestamp
        )

    # update company object instance
    _company.modified_datetime = modified_datetime
    _company.name = name

    # patch company to database
    pers_company_resp = pers_company.update_company_by_id(_company)
    
    return BusinessResponse(
        data=pers_company_resp.data,
        message=pers_company_resp.message,
        status_code=pers_company_resp.status_code,
        success=pers_company_resp.success,
        timestamp=pers_company_resp.timestamp
    )


def delete_company(
        company_id: int
    ) -> BusinessResponse:
    """
    Deletes a company from the database.
    """ 

    # validate company id
    if not company_id or company_id is None or company_id == '':
        return BusinessResponse(
            data=None,
            message='Missing Company ID.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    
    # delete company from database
    pers_company_resp = pers_company.delete_company(company_id)
    
    return BusinessResponse(
        data=pers_company_resp.data,
        message=pers_company_resp.message,
        status_code=pers_company_resp.status_code,
        success=pers_company_resp.success,
        timestamp=pers_company_resp.timestamp
    )
