"""
RBAC Module Name Constants
==========================
Canonical module names for use with require_module_api / require_module_web.
These MUST match the Name column in dbo.[Module] exactly.

Usage:
    from shared.rbac_constants import Modules
    current_user=Depends(require_module_api(Modules.BILLS, "can_create"))
"""


class Modules:
    """
    Each attribute maps to a dbo.[Module].Name value.
    Referencing Modules.BILLS instead of the string "Bills"
    turns typos into AttributeError at import time.
    """

    # Financial entities
    BILLS           = "Bills"
    BILL_CREDITS    = "Bill Credits"
    EXPENSES        = "Expenses"
    INVOICES        = "Invoices"
    CONTRACT_LABOR  = "Contract Labor"

    # Reference data
    VENDORS         = "Vendors"
    CUSTOMERS       = "Customers"
    PROJECTS        = "Projects"
    COST_CODES      = "Cost Codes"

    ATTACHMENTS     = "Attachments"

    USERS           = "Users"
    ROLES           = "Roles"
    ORGANIZATIONS   = "Organizations"
    COMPANIES       = "Companies"

    INTEGRATIONS    = "Integrations"
    QBO_SYNC        = "QBO Sync"

    DASHBOARD       = "Dashboard"

    REVIEW_STATUSES = "Review Statuses"

    # Pending actions
    PENDING_ACTIONS = "Pending Actions"

    # Time tracking
    TIME_TRACKING   = "Time Tracking"
