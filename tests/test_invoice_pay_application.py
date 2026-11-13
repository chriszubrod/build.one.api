"""Application for Payment (U-208, de-branded U-792): the figures and the renderer,
including the four per-project optional blocks."""

import io
from decimal import Decimal

import pytest
from pypdf import PdfReader

from entities.invoice.business.packet_render import money_number
from entities.invoice.business.pay_application import (
    build_application_lines,
    build_application_pdf,
)
from entities.project_draw_packet_setting.business.model import DrawPacketBlocks


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() or "" for page in reader.pages)


def _pages(pdf_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def _header(**overrides):
    base = {
        "application_date": "April 1, 2026",
        "project": "Harbor View Residence",
        "application_no": "APP-2026-04",
        "period_to": "March 31, 2026",
        "from_lines": ["Rogers Build, Inc.", "PO Box 594", "Brentwood, TN 37024"],
        "to_name": "Acme Development LLC",
        "to_lines": ["123 Main Street", "Austin, TX 78701"],
    }
    base.update(overrides)
    return base


def _lines(**overrides):
    base = {
        "contract_sum": Decimal("850000.00"),
        "net_change_orders": Decimal("0"),
        "contract_sum_to_date": Decimal("850000.00"),
        "completed_to_date": Decimal("420000.00"),
        "retainage": Decimal("0"),
        "earned_less_retainage": Decimal("420000.00"),
        "previous_applications": Decimal("342000.00"),
        "amount_due": Decimal("78000.00"),
        "balance_to_finish": Decimal("430000.00"),
    }
    base.update(overrides)
    return base


ALL_ON = DrawPacketBlocks(contractor_statement=True, owner_approval=True,
                          architect_certificate=True, notary=True)
ALL_OFF = DrawPacketBlocks(contractor_statement=False, owner_approval=False,
                           architect_certificate=False, notary=False)


# ---- figures ---------------------------------------------------------------------

def test_lines_reconcile_from_the_schedule_grand():
    grand = {"scheduled": Decimal("4783414.60"), "prev": Decimal("1142564.98"),
             "this_period": Decimal("506388.46"), "total_to_date": Decimal("1648953.44")}
    L = build_application_lines(grand)
    assert L["contract_sum"] == Decimal("4783414.60")
    assert L["net_change_orders"] == Decimal("0")
    assert L["contract_sum_to_date"] == Decimal("4783414.60")
    assert L["completed_to_date"] == Decimal("1648953.44")
    assert L["retainage"] == Decimal("0")
    assert L["earned_less_retainage"] == Decimal("1648953.44")
    assert L["previous_applications"] == Decimal("1142564.98")
    assert L["amount_due"] == Decimal("506388.46")   # == grand this_period
    assert L["balance_to_finish"] == Decimal("3134461.16")


def test_lines_with_retainage():
    grand = {"scheduled": Decimal("1000"), "prev": Decimal("200"),
             "this_period": Decimal("100"), "total_to_date": Decimal("300")}
    L = build_application_lines(grand, retainage_rate=Decimal("0.10"))
    assert L["retainage"] == Decimal("30.00")          # 10% of cumulative 300
    assert L["earned_less_retainage"] == Decimal("270.00")
    # previous = 200 net of retainage = 180; due = 270 - 180 = 90 (= 100 x 0.9)
    assert L["previous_applications"] == Decimal("180.00")
    assert L["amount_due"] == Decimal("90.00")


# ---- renderer ----------------------------------------------------------------------

def test_pdf_core_content():
    pdf = build_application_pdf(_header(), _lines(), DrawPacketBlocks())
    assert pdf[:4] == b"%PDF"
    text = _pdf_text(pdf)
    assert "Application for Payment" in text
    assert "Harbor View Residence" in text and "APP-2026-04" in text
    assert "March 31, 2026" in text and "April 1, 2026" in text
    assert "Acme Development LLC" in text and "Rogers Build, Inc." in text
    assert "Contract Sum" in text and "Amount Due This Application" in text
    assert "Balance to Finish" in text and "Less Previous Applications" in text
    assert money_number(Decimal("850000.00")) in text
    assert money_number(Decimal("78000.00")) in text
    assert "Schedule of Values" in text


def test_retainage_rows_only_when_retainage_is_held():
    assert "Retainage" not in _pdf_text(build_application_pdf(_header(), _lines(), ALL_OFF))
    text = _pdf_text(build_application_pdf(
        _header(), _lines(retainage=Decimal("42000.00")), ALL_OFF))
    assert "Less Retainage Held" in text and "42,000.00" in text


_BLOCK_TITLES = {
    "contractor_statement": "Contractor's Statement",
    "notary": "Notary",
    "owner_approval": "Owner Approval",
    "architect_certificate": "Architect Review",
}


def test_default_blocks_are_statement_and_notary_only():
    text = _pdf_text(build_application_pdf(_header(), _lines(), DrawPacketBlocks()))
    assert "Contractor's Statement" in text
    assert "Notary" in text and "Subscribed and sworn" in text
    assert "Owner Approval" not in text   # U-792a: off by default
    assert "Architect" not in text


@pytest.mark.parametrize("only", sorted(_BLOCK_TITLES))
def test_each_block_follows_its_own_switch(only):
    blocks = DrawPacketBlocks(**{k: k == only for k in _BLOCK_TITLES})
    text = _pdf_text(build_application_pdf(_header(), _lines(), blocks))
    for key, title in _BLOCK_TITLES.items():
        assert (title in text) == (key == only), (only, key)


def test_no_blocks_leaves_only_the_figures():
    text = _pdf_text(build_application_pdf(_header(), _lines(), ALL_OFF))
    for title in _BLOCK_TITLES.values():
        assert title not in text
    assert "By:" not in text and "Date:" not in text


def test_statement_names_the_contractor_and_owner_block_names_the_owner():
    text = _pdf_text(build_application_pdf(_header(), _lines(), ALL_ON)).replace("\n", " ")
    assert "Rogers Build, Inc. states that the work and materials billed" in text
    assert "Approved amount: $" in text
    assert text.count("Acme Development LLC") >= 2   # the To block + the approval line


def test_all_blocks_fit_one_page():
    assert _pages(build_application_pdf(_header(), _lines(), ALL_ON)) == 1


def test_the_page_is_landscape_letter():
    # U-792a: printed landscape, like the Schedule of Values.
    for blocks in (ALL_ON, ALL_OFF, DrawPacketBlocks()):
        box = PdfReader(io.BytesIO(build_application_pdf(_header(), _lines(), blocks))).pages[0].mediabox
        assert (round(float(box.width)), round(float(box.height))) == (792, 612)


def test_a_long_owner_name_still_fits_one_page():
    # A real long owner name (WR-01's) with retainage rows and all four blocks on.
    header = _header(to_name="Chesnut Infill GP dba Craftsman Residential")
    assert _pages(build_application_pdf(header, _lines(retainage=Decimal("42000.00")), ALL_ON)) == 1


# U-792a review F1/F2: the two-column row cannot split. An unusually long name or
# address must fall back to the stacked layout — never raise (which would drop the
# Application AND the Schedule of Values from the packet) and never orphan the header.
@pytest.mark.parametrize("overrides", [
    {"from_lines": ["X " * 200]},                                   # LayoutError case
    {"to_lines": ["123 Main Street"] * 8},                          # whole row jumped to p2
    {"to_name": "Chesnut Infill GP dba Craftsman Residential " * 3},  # blocks overflow
])
def test_an_overlong_header_falls_back_to_the_stacked_layout(overrides):
    pdf = build_application_pdf(_header(**overrides), _lines(retainage=Decimal("5")), ALL_ON)
    pages = PdfReader(io.BytesIO(pdf)).pages
    assert "Amount Due This Application" in pages[0].extract_text()
    text = "".join(p.extract_text() for p in pages)
    for title in _BLOCK_TITLES.values():
        assert title in text


# U-792b: drawn writing lines, and one page shape whatever the switches say.
def test_fill_ins_are_drawn_rules_not_typed_underscores():
    text = _pdf_text(build_application_pdf(_header(), _lines(), ALL_ON))
    assert "___" not in text
    for label in ("By:", "Date:", "State of:", "County of:", "Notary Public:",
                  "Commission expires:", "Approved amount: $", "Recommended amount: $",
                  "Architect:"):
        assert label in text, label


def _figures_right_edge(blocks) -> float:
    """x of the AMOUNT band label — where the figures column ends."""
    xs = _label_xs(blocks)
    assert "AMOUNT" in xs, "AMOUNT label not found"
    return xs["AMOUNT"][0]


def test_figures_keep_the_left_column_with_or_without_blocks():
    assert _figures_right_edge(ALL_OFF) == pytest.approx(_figures_right_edge(ALL_ON), abs=1)
    assert _figures_right_edge(ALL_OFF) < 792 * 0.6   # left column, not full width


# U-792c: room to write. Measured from where the labels print: a rule runs from the end
# of its label to the start of the next label on the same row.
def _label_xs(blocks=ALL_ON) -> dict:
    page = PdfReader(io.BytesIO(build_application_pdf(_header(), _lines(), blocks))).pages[0]
    xs: dict = {}
    # Table cells are placed by the current transformation matrix, so x = cm + tm.
    page.extract_text(visitor_text=lambda t, cm, tm, *_: xs.setdefault(t.strip(), []).append(cm[4] + tm[4]))
    return xs


def test_signature_lines_have_room_to_sign():
    xs = _label_xs()
    by, date = xs["By:"][0], xs["Date:"][0]
    assert date - by >= 3.4 * 72          # "By:" label + a ≥ ~3.3in signature rule


def test_state_and_county_lines_have_room():
    xs = _label_xs()
    assert xs["County of:"][0] - xs["State of:"][0] >= 2.3 * 72   # label + ≥ ~2in rule


def test_the_signature_column_is_wider_than_the_figures_column():
    blocks_x = _label_xs()["Contractor's Statement"][0]
    left, right = 36, 792 - 36                     # the 0.5in landscape frame
    assert right - blocks_x > blocks_x - left      # signature column wider than figures + gutter
