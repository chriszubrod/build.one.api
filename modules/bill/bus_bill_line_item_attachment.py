"""Business logic for attachments."""


# python standard library imports
import os
import base64
import logging

# local imports
from datetime import datetime
from typing import Union, Tuple
from pathlib import Path
from shared.response import BusinessResponse

# persistence imports
from modules.bill import (
    pers_bill_line_item_attachment,
    pers_bill_line_item
)



def get_bill_line_item_attachment_by_bill_line_item_id(
        bill_line_item_id
    ) -> BusinessResponse:
    """
    Retrieves a bill line item attachment by its bill line item ID.
    """
    pers_bill_line_item_attachment_resp = pers_bill_line_item_attachment\
        .read_bill_line_item_attachment_by_bill_line_item_id(
            bill_line_item_id
        )
    if pers_bill_line_item_attachment_resp.success:
        return BusinessResponse(
            data=pers_bill_line_item_attachment_resp.data,
            message="Bill line item attachment retrieved successfully",
            status_code=200,
            success=True,
            timestamp=datetime.now()
        )
    else:
        return BusinessResponse(
            data=[],
            message="Bill line item attachment not found",
            status_code=404,
            success=False,
            timestamp=datetime.now()
        )
