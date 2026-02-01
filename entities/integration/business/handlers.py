# Python Standard Library Imports
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

# Third-party Imports

# Local Imports
from entities.integration.business.model import Integration
from integrations.intuit.qbo.auth.external.client import (
    connect_intuit_oauth_2_endpoint,
    connect_intuit_oauth_2_token_endpoint_revoke
)
from integrations.ms.auth.external.client import (
    connect_ms_oauth_2_endpoint,
    connect_ms_oauth_2_token_endpoint_revoke
)


class IntegrationHandler(ABC):
    """
    Abstract base class for integration-specific handlers.
    Each integration type (QBO, MS365, etc.) should implement this interface.
    """
    
    @abstractmethod
    def connect(self, integration: Integration) -> Dict[str, Any]:
        """
        Initiate the connection process for this integration type.
        Returns a dict with 'redirect_url' or 'message' for the client.
        """
        pass
    
    @abstractmethod
    def disconnect(self, integration: Integration) -> Dict[str, Any]:
        """
        Disconnect/revoke the connection for this integration type.
        Returns a dict with success/error information.
        """
        pass


class QuickBooksOnlineHandler(IntegrationHandler):
    """
    Handler for QuickBooks Online integration.
    """
    
    def connect(self, integration: Integration) -> Dict[str, Any]:
        """
        Connect QuickBooks Online by generating OAuth authorization URL.
        """
        try:
            result = connect_intuit_oauth_2_endpoint()
            
            # The function returns a dict with "message" (the URL) and "status_code"
            if isinstance(result, dict):
                if result.get("status_code") == 201 or result.get("status_code") == 200:
                    auth_url = result.get("message")
                    if auth_url:
                        return {
                            "success": True,
                            "redirect_url": auth_url,
                            "message": "Authorization URL generated successfully"
                        }
                    else:
                        return {
                            "success": False,
                            "redirect_url": None,
                            "message": "No authorization URL in response"
                        }
                else:
                    return {
                        "success": False,
                        "redirect_url": None,
                        "message": result.get("message", "Failed to generate authorization URL")
                    }
            elif isinstance(result, str):
                # Handle case where it returns a string directly
                return {
                    "success": True,
                    "redirect_url": result,
                    "message": "Authorization URL generated successfully"
                }
            else:
                return {
                    "success": False,
                    "redirect_url": None,
                    "message": "Unexpected response format from OAuth endpoint"
                }
        except Exception as e:
            return {
                "success": False,
                "redirect_url": None,
                "message": f"Error generating authorization URL: {str(e)}"
            }
    
    def disconnect(self, integration: Integration) -> Dict[str, Any]:
        """
        Disconnect QuickBooks Online by revoking OAuth tokens.
        """
        try:
            result = connect_intuit_oauth_2_token_endpoint_revoke()
            
            if isinstance(result, dict):
                if result.get("status_code") == 200 or result.get("status_code") == 201:
                    return {
                        "success": True,
                        "message": "Integration disconnected successfully",
                        "callback_url": f"/integration/disconnect/callback?success=true&message=Integration disconnected successfully&integration_id={integration.public_id}"
                    }
                else:
                    return {
                        "success": False,
                        "message": result.get("message", "Failed to disconnect"),
                        "callback_url": f"/integration/disconnect/callback?success=false&message={result.get('message', 'Failed to disconnect')}&integration_id={integration.public_id}"
                    }
            elif isinstance(result, str):
                if "error" not in result.lower():
                    return {
                        "success": True,
                        "message": "Integration disconnected successfully",
                        "callback_url": f"/integration/disconnect/callback?success=true&message=Integration disconnected successfully&integration_id={integration.public_id}"
                    }
                else:
                    return {
                        "success": False,
                        "message": result,
                        "callback_url": f"/integration/disconnect/callback?success=false&message={result}&integration_id={integration.public_id}"
                    }
            else:
                return {
                    "success": False,
                    "message": "Unexpected response from disconnect endpoint",
                    "callback_url": f"/integration/disconnect/callback?success=false&message=Unexpected response from disconnect endpoint&integration_id={integration.public_id}"
                }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error disconnecting: {str(e)}",
                "callback_url": f"/integration/disconnect/callback?success=false&message=Error disconnecting: {str(e)}&integration_id={integration.public_id}"
            }


class Microsoft365Handler(IntegrationHandler):
    """
    Handler for Microsoft 365 integration.
    """
    
    def connect(self, integration: Integration) -> Dict[str, Any]:
        """
        Connect Microsoft 365 by generating OAuth authorization URL.
        """
        try:
            result = connect_ms_oauth_2_endpoint()
            
            # The function returns a dict with "message" (the URL) and "status_code"
            if isinstance(result, dict):
                if result.get("status_code") == 201 or result.get("status_code") == 200:
                    auth_url = result.get("message")
                    if auth_url:
                        return {
                            "success": True,
                            "redirect_url": auth_url,
                            "message": "Authorization URL generated successfully"
                        }
                    else:
                        return {
                            "success": False,
                            "redirect_url": None,
                            "message": "No authorization URL in response"
                        }
                else:
                    return {
                        "success": False,
                        "redirect_url": None,
                        "message": result.get("message", "Failed to generate authorization URL")
                    }
            elif isinstance(result, str):
                # Handle case where it returns a string directly
                return {
                    "success": True,
                    "redirect_url": result,
                    "message": "Authorization URL generated successfully"
                }
            else:
                return {
                    "success": False,
                    "redirect_url": None,
                    "message": "Unexpected response format from OAuth endpoint"
                }
        except Exception as e:
            return {
                "success": False,
                "redirect_url": None,
                "message": f"Error generating authorization URL: {str(e)}"
            }
    
    def disconnect(self, integration: Integration) -> Dict[str, Any]:
        """
        Disconnect Microsoft 365 by revoking OAuth tokens.
        """
        try:
            result = connect_ms_oauth_2_token_endpoint_revoke()
            
            if isinstance(result, dict):
                if result.get("status_code") == 200 or result.get("status_code") == 201:
                    return {
                        "success": True,
                        "message": "Integration disconnected successfully",
                        "callback_url": f"/integration/disconnect/callback?success=true&message=Integration disconnected successfully&integration_id={integration.public_id}"
                    }
                else:
                    return {
                        "success": False,
                        "message": result.get("message", "Failed to disconnect"),
                        "callback_url": f"/integration/disconnect/callback?success=false&message={result.get('message', 'Failed to disconnect')}&integration_id={integration.public_id}"
                    }
            elif isinstance(result, str):
                if "error" not in result.lower():
                    return {
                        "success": True,
                        "message": "Integration disconnected successfully",
                        "callback_url": f"/integration/disconnect/callback?success=true&message=Integration disconnected successfully&integration_id={integration.public_id}"
                    }
                else:
                    return {
                        "success": False,
                        "message": result,
                        "callback_url": f"/integration/disconnect/callback?success=false&message={result}&integration_id={integration.public_id}"
                    }
            else:
                return {
                    "success": False,
                    "message": "Unexpected response from disconnect endpoint",
                    "callback_url": f"/integration/disconnect/callback?success=false&message=Unexpected response from disconnect endpoint&integration_id={integration.public_id}"
                }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error disconnecting: {str(e)}",
                "callback_url": f"/integration/disconnect/callback?success=false&message=Error disconnecting: {str(e)}&integration_id={integration.public_id}"
            }


class IntegrationHandlerFactory:
    """
    Factory to get the appropriate handler for an integration type.
    """
    
    _handlers: Dict[str, IntegrationHandler] = {
        "quickbooks online": QuickBooksOnlineHandler(),
        "quickbooks": QuickBooksOnlineHandler(),
        "qbo": QuickBooksOnlineHandler(),
        "microsoft 365": Microsoft365Handler(),
        "ms365": Microsoft365Handler(),
        "office 365": Microsoft365Handler(),
    }
    
    @classmethod
    def get_handler(cls, integration: Integration) -> Optional[IntegrationHandler]:
        """
        Get the appropriate handler for an integration based on its name.
        """
        if not integration or not integration.name:
            return None
        
        integration_name_lower = integration.name.lower()
        
        # Check for exact matches or partial matches
        for key, handler in cls._handlers.items():
            if key in integration_name_lower:
                return handler
        
        return None

