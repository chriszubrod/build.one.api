# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query

# Local Imports
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.base.errors import QboValidationError
from integrations.intuit.qbo.report.external.client import QboReportClient
from shared.api.responses import item_response
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-reports"])


@router.get("/qbo/reports/{report_name}")
def get_qbo_report_router(
    report_name: str,
    realm_id: Optional[str] = Query(None, description="QBO realm ID. Omit for single-tenant default."),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    accounting_method: Optional[str] = Query(None, description="Accrual or Cash"),
    date_macro: Optional[str] = Query(None, description="e.g. 'Today', 'Last Month', 'This Fiscal Year-to-date'"),
    summarize_column_by: Optional[str] = Query(None),
    columns: Optional[str] = Query(None, description="Comma-separated column names"),
    customer: Optional[str] = Query(None, description="QBO Customer Id filter (where supported)"),
    vendor: Optional[str] = Query(None, description="QBO Vendor Id filter (where supported)"),
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """
    Read-only pass-through to the QBO Reports API.

    Proves the existing `QboHttpClient` auth + retry + 401-refresh plumbing
    works for the Reports surface. Returns the raw QBO JSON body
    (`{Header, Columns, Rows}`) unchanged.
    """
    auth_service = QboAuthService()
    auth = auth_service.ensure_valid_token(realm_id=realm_id)
    if not auth or not auth.access_token:
        raise HTTPException(status_code=503, detail="No valid QBO auth token available")

    resolved_realm_id = auth.realm_id

    params = {
        "start_date": start_date,
        "end_date": end_date,
        "accounting_method": accounting_method,
        "date_macro": date_macro,
        "summarize_column_by": summarize_column_by,
        "columns": columns,
        "customer": customer,
        "vendor": vendor,
    }
    params = {k: v for k, v in params.items() if v is not None}

    try:
        with QboReportClient(realm_id=resolved_realm_id) as client:
            data = client.get_report(report_name, params=params)
    except QboValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return item_response({
        "report_name": report_name,
        "realm_id": resolved_realm_id,
        "params": params,
        "data": data,
    })
