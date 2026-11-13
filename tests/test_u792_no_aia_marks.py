"""U-792: the draw packet is our own document. No AIA name, form number, footer or
form wording may appear on a rendered page, nor in the packet renderers' source."""

import io
import re
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from entities.invoice.business.pay_application import build_application_lines, build_application_pdf
from entities.invoice.business.schedule_of_values import build_schedule_of_values_pdf, build_sov_rows
from entities.project_draw_packet_setting.business.model import DrawPacketBlocks

ROOT = Path(__file__).resolve().parents[1]

# Marks of the published forms: names, numbers, footers and signature-block phrases.
FORBIDDEN = re.compile(
    r"\bAIA\b|G70[23]|D401|American Institute|Institute of Architects|"
    r"Certification for Payment|Certificate for Payment|AMOUNT CERTIFIED|"
    r"CONTINUATION SHEET|Contract Documents|not negotiable",
    re.IGNORECASE,
)

PACKET_SOURCES = [
    "entities/invoice/business/pay_application.py",
    "entities/invoice/business/schedule_of_values.py",
    "entities/invoice/business/packet_render.py",
    "entities/invoice/business/draw_request.py",
    "entities/invoice/business/trend.py",
    "entities/invoice/api/router.py",
]


def _text(pdf: bytes) -> str:
    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def test_rendered_pages_carry_no_aia_marks():
    sov = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("40150")}]
    draws = [{"label": "WR-01", "date": "2026-10-30",
              "categories": [{"cost_code_number": "06", "cost_code_name": "Grading",
                              "amount": Decimal("20075")}],
              "subtotal": Decimal("20075"), "builders_fee": Decimal("2810.50"),
              "total": Decimal("22885.50")}]
    rows, grand = build_sov_rows(sov, draws, "WR-01")
    header = {"project": "WR", "application_no": "WR-01", "application_date": "10/30/2026",
              "period_to": "10/15/2026",
              "from_lines": ["Rogers Build, Inc."],
              "to_name": "Owner", "to_lines": []}
    every_block = DrawPacketBlocks(contractor_statement=True, owner_approval=True,
                                   architect_certificate=True, notary=True)
    for pdf in (build_schedule_of_values_pdf(header, rows, grand),
                build_application_pdf(header, build_application_lines(grand), every_block)):
        text = _text(pdf)
        assert not FORBIDDEN.search(text), FORBIDDEN.search(text).group(0)


def test_packet_renderer_sources_carry_no_aia_marks():
    for rel in PACKET_SOURCES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        hit = FORBIDDEN.search(src)
        assert not hit, f"{rel}: {hit.group(0)!r}"


def test_the_old_modules_are_gone():
    assert not (ROOT / "entities/invoice/business/g702.py").exists()
    assert not (ROOT / "entities/invoice/business/g703.py").exists()
