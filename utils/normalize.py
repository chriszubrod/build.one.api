from dateutil import parser
import re

def normalize_invoice_fields(extracted: dict) -> dict:
    def normalize_date(value):
        try:
            date_obj = parser.parse(
                value,
                fuzzy=True,
                dayfirst=False
            )
            return date_obj.strftime("%m/%d/%Y")
        except Exception:
            return None

    def normalize_amount(value):
        try:
            # Remove non-numeric except period and dollar
            amount = re.sub(r"[^\d.]", "", value)
            return f"${float(amount):,.2f}"
        except Exception:
            return "$0.00"

    normalized = {
        "vendor": extracted.get("vendor", "").strip(),
        "invoice_number": extracted.get("invoice_number", "").strip(),
        "invoice_date": normalize_date(
            extracted.get("invoice_date", "")
        ),
        "total_amount": normalize_amount(
            extracted.get("total_amount", "")
        ),
    }

    return normalized
