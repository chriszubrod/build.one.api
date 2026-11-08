"""U-778d1 b2 -- the QBO_INVOICE_PUSH mode flag: off | dry_run | on. Default and any unknown value = off."""
from __future__ import annotations

import os

MODES = ("off", "dry_run", "on")


def invoice_push_mode() -> str:
    raw = (os.environ.get("QBO_INVOICE_PUSH") or "").strip().lower()
    return raw if raw in MODES else "off"
