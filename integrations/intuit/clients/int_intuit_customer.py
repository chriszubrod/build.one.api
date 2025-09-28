"""Module for handling Intuit customer API integration."""
from typing import Dict, Any
import requests
from requests.exceptions import RequestException

from integrations.intuit.exceptions_int import IntuitAPIError


def query_intuit_customer_count_info(uri: str, access_token: str) -> Dict[str, Any]:
    """
    Query Intuit API for customer count information.
    
    Args:
        uri: The API endpoint URI
        access_token: OAuth access token
        
    Returns:
        Dict containing response message and status code
        
    Raises:
        IntuitAPIError: If API request fails
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"bearer {access_token}"
    }

    try:
        response = requests.get(
            url=uri,
            headers=headers,
            timeout=30  # Add reasonable timeout
        )

        return {
            "message": response.text,
            "status_code": response.status_code
        }

    except RequestException as e:
        raise IntuitAPIError(
            f"Failed to query Intuit customer endpoint: {str(e)}"
        ) from e


def query_intuit_customer_info(uri: str, access_token: str) -> Dict[str, Any]:
    """
    Query Intuit API for customer information.
    
    Args:
        uri: The API endpoint URI
        access_token: OAuth access token
        
    Returns:
        Dict containing response message and status code
        
    Raises:
        IntuitAPIError: If API request fails
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"bearer {access_token}"
    }

    try:
        response = requests.get(
            url=uri,
            headers=headers,
            timeout=30  # Add reasonable timeout
        )

        return {
            "message": response.text,
            "status_code": response.status_code
        }

    except RequestException as e:
        raise IntuitAPIError(
            f"Failed to query Intuit customer endpoint: {str(e)}"
        ) from e
