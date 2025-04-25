from flask import (
    Blueprint,
    render_template,
    request,
    jsonify
)
from pdf2image import convert_from_bytes
import cv2
import base64
import json
import numpy as np
import pytesseract

from agents import document_classification_crew

pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'

api_document_classification_bp = Blueprint(
    'api_document_classification',
    __name__
)


@api_document_classification_bp.route('/api/document/classification', methods=["POST"])
def post_document_classification():
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data received'}), 400

        results = []
        for document in data.get('documents'):
            filename = document.get('filename')
            filetype = document.get('filetype')
            filesize = document.get('filesize')
            filedata = document.get('filedata')

            if not filedata:
                return jsonify({'error': 'No file data received'}), 400

            print(filedata[:30])
            all_text = ""
            if filedata.startswith('data:image'):
                filedata = filedata.split(',')[1]

                # Decode the Base64 file data
                file_bytes = base64.b64decode(filedata)

                # Convert bytes to numpy array
                nparr = np.frombuffer(file_bytes, dtype=np.uint8)
                if nparr is None or len(nparr) == 0:
                    return jsonify({'error': 'Decoded file data is empty'}), 400

                # Decode the numpy array to an image
                image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if image is None:
                    return jsonify({'error': 'Image decode failed'}), 400

                # Convert image to grayscale
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

                # Use pytesseract to extract text from the image
                all_text = pytesseract.image_to_string(gray)

            if filedata.startswith('data:application/pdf'):
                filedata = filedata.split(',')[1]

                images = convert_from_bytes(
                    base64.b64decode(filedata),
                    poppler_path="/opt/homebrew/bin"
                )
                for i, image in enumerate(images):
                    # Use pytesseract to extract text from the image
                    text = pytesseract.image_to_string(image)
                    all_text += text

            crew_result = document_classification_crew.run_crew(all_text)
            crew_result['filename'] = filename
            results.append(crew_result)

        return jsonify(
            {
                'message': 'Results received from crew.',
                'results': results
            }
        )
