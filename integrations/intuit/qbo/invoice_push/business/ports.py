"""U-778d1 b2 -- the seams the dry-run service talks through (design u778d sections 3-4).

`READ_ONLY_QBO_METHODS` is the whole QBO surface the dry run may touch: GET / query reads (plus `close`, a
transport lifecycle call). The service refuses any other name (`service._qbo_call`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

READ_ONLY_QBO_METHODS = frozenset({"read_source_lines", "read_charges", "doc_number_invoice_ids", "get_invoice", "get_item",
                                   "close"})


@dataclass(frozen=True)
class ProjectFacts:
    project_id: Union[int, str]
    qbo_id: Optional[str]
    realm_id: Optional[str]
    doc_number_used_locally: bool
    doc_number_local_invoice_qbo_ids: tuple = ()  # per local invoice with the number: QboId in the realm, else None
