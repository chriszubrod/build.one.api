"""
This is a blueprint for the Attachment API route.
DELETE /api/attachment/<guid>, methods=['DELETE']
GET /api/attachment/<guid>, methods=['GET']
PATCH /api/attachment/<guid>, methods=['PATCH']
POST /api/post/attachment, methods=['POST']
"""
# python standard library imports



# third party imports
from datetime import datetime
from flask import (
    Blueprint,
    jsonify,
    request
)

# local imports
from modules.attachment import bus_attachment
from shared.response import ApiResponse

api_attachment_bp = Blueprint('api_attachment', __name__, url_prefix='/api')



@api_attachment_bp.route('/attachment/<guid>', methods=['DELETE'])
def api_delete_attachment_by_guid_route(guid):
    """
    Handles the DELETE request for deleting an attachment.
    """
    attachment_bus_resp = bus_attachment.delete_attachment_by_guid(guid)
    return jsonify(
        ApiResponse(
            data=attachment_bus_resp.data,
            message=attachment_bus_resp.message,
            status_code=attachment_bus_resp.status_code,
            success=attachment_bus_resp.success,
            timestamp=attachment_bus_resp.timestamp
        ).to_dict()
    )




@api_attachment_bp.route('/attachment/<guid>', methods=['GET'])
def api_get_attachment_by_guid_route(guid):
    """
    Handles the GET request for getting an attachment.
    """
    attachment_bus_resp = bus_attachment.get_attachment_by_guid(guid)
    return jsonify(
        ApiResponse(
            data=attachment_bus_resp.data,
            message=attachment_bus_resp.message,
            status_code=attachment_bus_resp.status_code,
            success=attachment_bus_resp.success,
            timestamp=attachment_bus_resp.timestamp
        ).to_dict()
    )



@api_attachment_bp.route('/attachment/<guid>', methods=['PATCH'])
def api_patch_attachment_by_guid_route(guid):
    """
    Handles the PATCH request for updating an attachment.
    """
    if request.is_json:
        try:
            data = request.json
            name = data.get('name', '')
            size = data.get('size', '')
            type = data.get('type', '')
            attachment_bus_resp = bus_attachment.patch_attachment_by_guid(
                guid=guid,
                name=name,
                size=size,
                type=type
            )
            if attachment_bus_resp.success:
                return jsonify(
                    ApiResponse(
                        data=None,
                        message="Attachment updated successfully.",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    ).to_dict()
                )
            return jsonify(
                ApiResponse(
                    data=None,
                    message=attachment_bus_resp.message,
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




@api_attachment_bp.route('/post/attachment', methods=['POST'])
def api_post_attachment_route():
    """
    Handles the POST request for creating a new attachment.
    """
    if request.is_json:

        try:
            data = request.json

            # name
            name = data.get('name', '')

            # size
            size = data.get('size', '')

            # type
            type = data.get('type', '')



            attachment_bus_resp = bus_attachment.post_attachment(
                name=name,
                size=size,
                type=type
            )

            if attachment_bus_resp.success:
                return jsonify(
                    ApiResponse(
                        data=None,
                        message="Attachment posted successfully.",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    ).to_dict()
                )

            return jsonify(
                ApiResponse(
                    data=None,
                    message=attachment_bus_resp.message,
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



