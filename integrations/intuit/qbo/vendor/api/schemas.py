# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


# U-529: `QboVendorCreate` and `QboVendorUpdate` were DELETED with the
# create/update routes that were this module's only consumers. Both routes
# called `QboVendorService` methods that have never existed, so neither schema
# ever validated a body that reached a service; both also declared
# `bill_addr_id`, binding a caller-supplied value straight onto the
# `qbo.Vendor.BillAddrId` column U-513 is dropping. Do not reintroduce them:
# `qbo.Vendor` is staging, written by the QBO pull alone.
#
# NB the identically-named `QboVendorCreate` / `QboVendorUpdate` in
# `integrations/intuit/qbo/vendor/external/schemas.py` are a DIFFERENT pair —
# the outbound QBO push payloads — and are untouched by this.


class QboVendorSync(BaseModel):
    realm_id: str = Field(
        description="QBO company realm ID.",
    )
    last_updated_time: Optional[str] = Field(
        default=None,
        description="Optional ISO format datetime. If provided, only sync vendors updated after this time.",
    )
    sync_to_modules: bool = Field(
        default=False,
        description="If True, also sync to Vendor module.",
    )
