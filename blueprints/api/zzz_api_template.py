"""
This is a template for creating a new API route.
"""
# third party imports
from datetime import datetime
from flask import (
    Blueprint,
    jsonify,
    request
)

# local imports
from blueprints.api.api_response import ApiResponse

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route('/api', methods=['GET', 'POST'])
def api_route():
    """
    Handles the GET and POST requests for the API route.
    """
    if request.is_json:
        try:
            return jsonify(
                ApiResponse(
                    data=None,
                    message="API Success.",
                    status_code=200,
                    success=True,
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
    else:
        return jsonify(
            ApiResponse(
                data=None,
                message="Invalid request.",
                status_code=400,
                success=False,
                timestamp=datetime.now()
            ).to_dict()
        )
