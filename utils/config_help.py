"""
Module for configuration helper.
"""
import os
import json
from typing import Dict, Any
from flask import current_app

# Get the secrets.json path from environment variable or use default
project_root = os.path.realpath(os.path.join(os.path.dirname(__file__), '..'))
SECRETS_URL = os.getenv('SECRETS_PATH', os.path.join(project_root, 'secrets.json'))
print(f"Secrets file path: {SECRETS_URL}")  # This will help verify the path

def get_secrets() -> Dict[str, Any]:
    """
    Read and return the contents of secrets.json file.
    
    Returns:
        Dict[str, Any]: The contents of secrets.json as a dictionary
        
    Raises:
        FileNotFoundError: If secrets.json file is not found
        json.JSONDecodeError: If secrets.json contains invalid JSON
        Exception: For any other unexpected errors
    """
    try:
        with open(SECRETS_URL, encoding="utf-8") as f:
            return json.loads(f.read())
    except FileNotFoundError:
        raise FileNotFoundError(f"secrets.json file not found at {SECRETS_URL}")
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON in secrets.json: {str(e)}", e.doc, e.pos)
    except Exception as e:
        raise Exception(f"Error reading secrets.json: {str(e)}")


def write_secrets(secrets: Dict[str, Any]) -> None:
    """
    Write the contents of secrets.json file.
    
    Args:
        secrets (Dict[str, Any]): The contents of secrets.json as a dictionary
    """
    try:
        with open(SECRETS_URL, 'w', encoding="utf-8") as f:
            json.dump(secrets, f, indent=4)
    except Exception as e:
        raise Exception(f"Error writing secrets.json: {str(e)}")


def update_secrets(new_secrets: Dict[str, Any]) -> None:
    """
    Update the secrets in both the Flask app config and the secrets.json file.
    
    Args:
        new_secrets (Dict[str, Any]): The new secrets to update with
        
    Note:
        This function should only be called within a Flask application context
    """
    try:
        # Update the Flask app config
        current_app.config['SECRETS'] = new_secrets
        # Write to the secrets.json file
        write_secrets(new_secrets)
    except Exception as e:
        raise Exception(f"Error updating secrets: {str(e)}")
