# Python Standard Library Imports
import logging
from typing import Optional, List

# Local Imports
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorExpenseHistoryRepository:
    """Repository for querying historical vendor expense patterns."""

    def read_vendor_expense_history(
        self, vendor_id: int, limit: int = 20
    ) -> List[dict]:
        """
        Read historical SubCostCode/Project usage patterns for a vendor.
        Returns list of dicts sorted by usage frequency descending.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadVendorExpenseHistory",
                        params={"VendorId": vendor_id, "Limit": limit},
                    )
                    rows = cursor.fetchall()
                    result = []
                    for row in rows:
                        if not row:
                            continue
                        sample_desc_raw = getattr(row, "SampleDescriptions", None) or ""
                        sample_descriptions = [
                            d.strip()
                            for d in sample_desc_raw.rstrip(" | ").split(" | ")
                            if d.strip()
                        ]
                        result.append({
                            "sub_cost_code_id": getattr(row, "SubCostCodeId", None),
                            "sub_cost_code_number": getattr(row, "SubCostCodeNumber", None),
                            "sub_cost_code_name": getattr(row, "SubCostCodeName", None),
                            "project_id": getattr(row, "ProjectId", None),
                            "project_name": getattr(row, "ProjectName", None),
                            "project_abbreviation": getattr(row, "ProjectAbbreviation", None),
                            "usage_count": getattr(row, "UsageCount", 0),
                            "total_amount": float(getattr(row, "TotalAmount", 0) or 0),
                            "most_recent_date": getattr(row, "MostRecentDate", None),
                            "avg_amount": float(getattr(row, "AvgAmount", 0) or 0),
                            "sample_descriptions": sample_descriptions,
                        })
                    return result
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error reading vendor expense history: %s", error)
            raise map_database_error(error)
