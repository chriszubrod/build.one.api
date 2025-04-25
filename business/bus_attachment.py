"""Business logic for attachments."""

import os
import base64
import logging
from business.bus_response import BusinessResponse
from datetime import datetime
from typing import Union, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

def write_file_to_project_id_bill_folder(
        file_content: Union[bytes, str],
        file_path: str,
        file_name: str,
        max_file_size: int = 50 * 1024 * 1024  # 50MB default limit
    ) -> BusinessResponse:
    """
    Write a file to disk with validation and error handling.
    
    # Example usage:
    success, message = write_file_to_project_id_bill_folder(
        file_content=pdf_content,
        file_path='project/3/bill',
        file_name='invoice.pdf'
    )

    if success:
        print(f"Success: {message}")
    else:
        print(f"Error: {message}")

    Args:
        file_content: The content to write (either bytes or base64 string)
        file_path: The directory path where the file should be saved
        file_name: The name of the file
        max_file_size: Maximum allowed file size in bytes
        
    Returns:
        Tuple[bool, str]: (Success status, Message)
    """
    try:
        # Validate inputs
        if not file_content:
            return False, "File content is empty"

        if not file_name:
            return False, "File name is required"

        # Clean and validate file path
        full_path = Path(file_path).resolve()
        if not str(full_path).startswith(str(Path('project').resolve())):
            return False, "Invalid file path - must be within project directory"

        # Create the directory if it doesn't exist
        full_path.mkdir(parents=True, exist_ok=True)

        # Combine path and filename
        file_full_path = full_path / file_name

        # Convert content to bytes if it's base64
        if isinstance(file_content, str):
            try:
                if ';base64,' in file_content:
                    file_content = file_content.split(';base64,')[1]
                file_content = base64.b64decode(file_content)
            except Exception as e:
                logger.error(f"Base64 decode error: {str(e)}")
                return False, "Invalid base64 content"

        # Check file size
        content_size = len(file_content)
        if content_size > max_file_size:
            return (
                False,
                f"File size ({content_size} bytes) exceeds maximum allowed ({max_file_size} bytes)"
            )

        # Write the file
        with open(file_full_path, 'wb') as f:
            f.write(file_content)

        logger.info(f"File successfully saved: {file_full_path}")

        return BusinessResponse(
            data=None,
            message=f"File saved successfully at {file_full_path}",
            status_code=200,
            success=True,
            timestamp=datetime.now()
        )

    except Exception as e:
        error_msg = f"Error saving file: {str(e)}"
        logger.error(error_msg)
        return BusinessResponse(
            data=None,
            message=error_msg,
            status_code=500,
            success=False,
            timestamp=datetime.now()
        )
