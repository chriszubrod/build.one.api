"""

"""
from business.bus_business_responses import BusinessResponse
from persistence.pers_response import SuccessResponse
from persistence import pers_ms_sharepoint_site

def get_sharepoint_sites():
    """
    Retrieves all ms sharepoint sites from the database.
    """
    pers_read_sites_response = pers_ms_sharepoint_site.read_sharepoint_sites()
    if isinstance(pers_read_sites_response, SuccessResponse):

        return BusinessResponse(
            success=True,
            message="Sites read successfully",
            data=pers_read_sites_response.data,
            status_code=200
        )

    else:
        return BusinessResponse(
            success=False,
            message=pers_read_sites_response.message,
            status_code=pers_read_sites_response.status_code
        )

