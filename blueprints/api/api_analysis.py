"""
Module for analysis API.
"""

# python standard library imports
import base64
import io
import json
import time
from datetime import datetime

# third party imports
import pdf2image
import pypdf
import pytesseract
import requests
from flask import Blueprint, request, jsonify


# local imports
from blueprints.api.api_response import ApiResponse
from business import bus_analysis


analysis_api_bp = Blueprint('analysis_api', __name__, url_prefix='/api')


@analysis_api_bp.route('/document/analysis', methods=['POST'])
async def api_document_analysis_route():
    """
    Analyzes a document.
    """
    print("Document is being analyzed.")
    if not request.is_json:
        return jsonify(
            ApiResponse(
                data=None,
                message="Request must be in JSON format.",
                status_code=400,
                success=False,
                timestamp=datetime.now()
            ).to_dict()
        )

    try:
        form_data = request.get_json()
        files = form_data.get('files', [])

        if not files or files == []:
            return jsonify(
                ApiResponse(
                    data=None,
                    message="No files provided.",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                ).to_dict()
            )

        base64_data = files[0].get('data').split('base64,')[1]
        pdf_bytes = base64.b64decode(base64_data)

        # Convert PDF pages to images from bytes
        images = pdf2image.convert_from_bytes(pdf_bytes)

        # Get the page count
        page_count = len(images)

        # Get the text data
        text_data = ""
        for image in images:
            text_data += pytesseract.image_to_string(image)

        print(f"Page count: {page_count}")
        print(f"Text data: {text_data}")

        return jsonify(
            ApiResponse(
                data={
                    "page_count": page_count,
                    "text_data": text_data
                },
                message="Document analyzed successfully.",
                status_code=200,
                success=True,
                timestamp=datetime.now()
            ).to_dict()
        )

    except Exception as e:
        return jsonify(
            ApiResponse(
                data=None,
                message=f"Error analyzing document: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            ).to_dict()
        )


@analysis_api_bp.route('/pass/document/to/slm', methods=['POST'])
async def api_pass_document_to_slm_route():
    """
    Passes a document to SLM for analysis.
    """
    print("Document is being passed to SLM.")
    if not request.is_json:
        return jsonify({'message': 'Request must be in JSON format.'}), 400

    document_text = ""

    try:
        form_data = request.get_json()
        files = form_data.get('files', [])

        first_file = files[0]
        base64_data = first_file.get('data').split('base64,')[1]
        pdf_bytes = base64.b64decode(base64_data)
        pdf_file = io.BytesIO(pdf_bytes)

        file_reader = pypdf.PdfReader(pdf_file)
        for page in file_reader.pages:
            text = page.extract_text()
            document_text += text
    except Exception as e:
        return jsonify({'message': str(e)}), 500

    prompt = (
        f"""Given the following document text, please tell me what type of document it is.
    
        Your choices are:
        - Bill
        - Expense
        - Invoice

        Be concise.

        -------------------------------------------------------------------------------------------

        Format the output as JSON with the following structure:

        {{
            "document_type": "YOUR_RESPONSE_HERE",
        }}

        -------------------------------------------------------------------------------------------

        Document Text:
        {document_text}
    """)

    print(f'Prompt: {prompt}')
    url = 'http://localhost:11434/api/generate'
    headers = {
        'Content-Type': 'application/json'
    }
    data = {
        'model': 'llama3.2:3b',
        'prompt': prompt,
        'format': 'json',
        'stream': False
    }

    print("Sending request to SLM.")
    start_time = time.time()
    slm = requests.post(
        url=url,
        data=json.dumps(data),
        headers=headers,
        timeout=60
    )
    print("Received response from SLM.")
    print(slm)
    if slm.json().get('response'):
        slm_response = slm.json().get('response')
        print(slm_response)
    print(f'Elapsed time: {time.time() - start_time}')

    return jsonify(slm_response), 201


@analysis_api_bp.route('/chat/message', methods=['POST'])
async def api_chat_message_route():
    """
    Handles the POST request for the chat message.
    """
    print("Chat message is being handled.")
    if not request.is_json:
        return jsonify({'message': 'Request must be in JSON format.'}), 400

    form_data = request.get_json()
    message = form_data.get('message')
    timestamp = form_data.get('timestamp')

    print(f"Message: {message}")
    print(f"Timestamp: {timestamp}")

    analysis_bus_resp = bus_analysis.pass_chat_to_slm_route(message)
    if not analysis_bus_resp.success:
        return jsonify(
            ApiResponse(
                data=None,
                message="Error passing chat to SLM.",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            ).to_dict()
        )

    return jsonify(
        ApiResponse(
            data=analysis_bus_resp.data,
            message="Chat message received.",
            status_code=200,
            success=True,
            timestamp=datetime.now()
        ).to_dict()
    )
