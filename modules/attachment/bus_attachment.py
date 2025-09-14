"""
Module for attachment business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from shared.response import BusinessResponse
from utils import function_help as fhp
from modules.attachment import pers_attachment


def post_attachment(
        name: str,
        size: int,
        type: str,
    ) -> BusinessResponse:
    """
    Posts an attachment.
    """

    # validate name
    if not name or name == "" or name is None:
        return BusinessResponse(
            data=None,
            message='Invalid or missing name.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # create attachment object instance
    _attachment = pers_attachment.Attachment(
        name=name,
        size=size,
        type=type
    )

    # create attachment in database
    post_attachment_pers_response = pers_attachment.create_attachment(_attachment)

    return BusinessResponse(
        data=post_attachment_pers_response.data,
        message=post_attachment_pers_response.message,
        status_code=post_attachment_pers_response.status_code,
        success=post_attachment_pers_response.success,
        timestamp=post_attachment_pers_response.timestamp
    )


def get_attachments() -> BusinessResponse:
    """
    Retrieves all attachments from the database.
    """
    read_attachments_pers_response = pers_attachment.read_attachments()
    return BusinessResponse(
        data=read_attachments_pers_response.data,
        success=read_attachments_pers_response.success,
        message=read_attachments_pers_response.message,
        status_code=read_attachments_pers_response.status_code,
        timestamp=read_attachments_pers_response.timestamp
    )


def get_attachment_by_guid(attachment_guid: str) -> BusinessResponse:
    """
    Retrieves an attachment by guid from the database.
    """
    # read attachment by guid
    pers_read_attachment_response = pers_attachment.read_attachment_by_guid(attachment_guid)
    return BusinessResponse(
        data=pers_read_attachment_response.data,
        message=pers_read_attachment_response.message,
        status_code=pers_read_attachment_response.status_code,
        success=pers_read_attachment_response.success,
        timestamp=pers_read_attachment_response.timestamp
    )


def patch_attachment_by_guid(
        attachment_guid: str,
        name: str,
        size: int,
        type: str
    ) -> BusinessResponse:
    """
    Patches an attachment.
    """
    # read attachment by guid
    pers_read_attachment_response = pers_attachment.read_attachment_by_guid(attachment_guid)

    # if attachment exists, update instance of attachment
    if pers_read_attachment_response.success:
        db_attachment = pers_read_attachment_response.data
        db_attachment.name = name
        db_attachment.size = size
        db_attachment.type = type
        
        # update attachment by guid in database
        pers_update_attachment_response = pers_attachment.update_attachment_by_id(db_attachment)
        return BusinessResponse(
            data=pers_update_attachment_response.data,
            message=pers_update_attachment_response.message,
            status_code=pers_update_attachment_response.status_code,
            success=pers_update_attachment_response.success,
            timestamp=pers_update_attachment_response.timestamp
        )
    else:
        # if attachment does not exist, return message
        return BusinessResponse(
            data=None,
            message=pers_read_attachment_response.message,
            status_code=pers_read_attachment_response.status_code,
            success=pers_read_attachment_response.success,
            timestamp=pers_read_attachment_response.timestamp
        )



def delete_attachment_by_guid(attachment_guid: str) -> BusinessResponse:
    """
    Deletes an attachment by guid from the database.
    """
    db_attachment = None
    pers_read_attachment_response = pers_attachment.read_attachment_by_guid(attachment_guid)
    if pers_read_attachment_response.success:
        db_attachment = pers_read_attachment_response.data

    if db_attachment:
        # delete attachment by id
        pers_delete_attachment_response = pers_attachment.delete_attachment_by_id(db_attachment.id)
        return BusinessResponse(
            data=pers_delete_attachment_response.data,
            message=pers_delete_attachment_response.message,
            status_code=pers_delete_attachment_response.status_code,
            success=pers_delete_attachment_response.success,
            timestamp=pers_delete_attachment_response.timestamp
        )
    else:
        # if attachment does not exist, return message
        return BusinessResponse(
            data=None,
            message=pers_read_attachment_response.message,
            status_code=pers_read_attachment_response.status_code,
            success=pers_read_attachment_response.success,
            timestamp=pers_read_attachment_response.timestamp
        )
