"""
This is a blueprint for the Address API route.
"""
# python standard library imports



# third party imports
from datetime import datetime
from dateutil import tz
from flask import (
    Blueprint,
    jsonify,
    request
)

# local imports
from blueprints.api.api_response import ApiResponse
from modules.address import bus_address

api_address_bp = Blueprint('api_address', __name__, url_prefix='/api')




@api_address_bp.route('/post/address', methods=['POST'])
def api_post_address_route():
    """
    Handles the POST request for creating a new address.
    """
    if request.is_json:

        try:
            data = request.json
            submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
            submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]

            # street one
            street_one = data.get('streetOne', '')

            # street two
            street_two = data.get('streetTwo', '')

            # city
            city = data.get('city', '')

            # state
            state = data.get('state', '')

            # zip code
            zip_code = data.get('zipCode', '')

            address_bus_resp = bus_address.post_address(
                street_one=street_one,
                street_two=street_two,
                city=city,
                state=state,
                zip_code=zip_code
            )

            if address_bus_resp.success:
                return jsonify(
                    ApiResponse(
                        data=None,
                        message="Address posted successfully.",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    ).to_dict()
                )

            return jsonify(
                ApiResponse(
                    data=None,
                    message=address_bus_resp.message,
                    status_code=500,
                    success=False,
                    timestamp=datetime.now()
                ).to_dict()
            )

        except (ValueError, TypeError, KeyError) as e:
            return jsonify(
                ApiResponse(
                    data=None,
                    message=str(e),
                    status_code=500,
                    success=False,
                    timestamp=datetime.now()
                ).to_dict()
            )

    return jsonify(
            ApiResponse(
                data=None,
                message="Invalid request.",
                status_code=400,
                success=False,
                timestamp=datetime.now()
            ).to_dict()
        )
