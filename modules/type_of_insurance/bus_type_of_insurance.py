"""
Business layer for Type of Insurance.
"""

# python standard library imports


# local imports
from shared.response import BusinessResponse
from modules.type_of_insurance import pers_type_of_insurance as pers_toi
from datetime import datetime


def get_type_of_insurances() -> BusinessResponse:
    resp = pers_toi.read_type_of_insurances()
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp or datetime.now()
    )

