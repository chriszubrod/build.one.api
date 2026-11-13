"""U-786 B2: draw-tab cloning for Box project trackers (zip/XML level, no openpyxl save).

Every fixture is built in-test: openpyxl makes the sheets, then the zip is
post-processed to add the logo / INVOICE drawings. No binary files committed.

Layout under test (smaller rows than production, same shape):
  Draw Request Trend  (D=draw 01, E=draw 02, F=spare, G=Total)
  Draw Request 02     (source for TST-03; keyed on $E$10)
  Draw Request        (draw 01, keyed on $D$4 with dated H tags)
  DETAILS             (ledger: B cost code, H draw tag, N amount)
"""

import datetime as _dt
import posixpath
import re
import struct
import zipfile
import zlib
from decimal import Decimal
from io import BytesIO
import xml.etree.ElementTree as ET

import openpyxl
import pytest
from openpyxl.worksheet.dimensions import ColumnDimension

from integrations.box.excel.business import draw_tabs as dtabs
from integrations.box.excel.business.draw_tabs import (
    DrawAlreadyFinished,
    DrawTabPlan,
    DrawTabsError,
    PreviousDrawMissing,
    UnsupportedTrackerLayout,
    finish_draw_tabs,
    one_tab_render_package,
    plan_next_draw,
    render_existing,
)

TREND = "Draw Request Trend"
SRC = "Draw Request 02"
NEW = "Draw Request 03"
DRAW01 = "Draw Request"
CATS = [(15, "Site Work"), (17, "Concrete"), (19, "Framing"), (21, "Paint")]
INVOICE = "TST-03"
DRAW_DATE = _dt.date(2026, 10, 30)
SERIAL_03 = 46325  # 2026-10-30 as an Excel date serial (epoch 1899-12-30)

NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


# ----------------------------------------------------------------- fixture build

def _png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


_XDR_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
)
_PIC = (
    '<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{id}" name="Picture {id}"/><xdr:cNvPicPr>'
    '<a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
    '<xdr:blipFill><a:blip r:embed="rIdImg1"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
    '<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic>'
)
_SP = (
    '<xdr:sp macro="" textlink=""><xdr:nvSpPr><xdr:cNvPr id="{id}" name="TextBox {id}"/>'
    '<xdr:cNvSpPr txBox="1"/></xdr:nvSpPr>'
    '<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
    '<xdr:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US"/>'
    '<a:t>INVOICE</a:t></a:r></a:p></xdr:txBody></xdr:sp>'
)


def _anchor(c1, r1, c2, r2, body):
    return (
        f"<xdr:twoCellAnchor><xdr:from><xdr:col>{c1}</xdr:col><xdr:colOff>0</xdr:colOff>"
        f"<xdr:row>{r1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
        f"<xdr:to><xdr:col>{c2}</xdr:col><xdr:colOff>0</xdr:colOff>"
        f"<xdr:row>{r2}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
        f"{body}<xdr:clientData/></xdr:twoCellAnchor>"
    )


def _invoice_drawing() -> str:
    return _XDR_HEAD + _anchor(1, 0, 3, 2, _SP.format(id=2)) + _anchor(4, 0, 5, 2, _PIC.format(id=3)) + "</xdr:wsDr>"


def _trend_drawing() -> str:
    # logo at col 0 (must not move) and a logo at col 7 (H, right of the Total column: must shift)
    return _XDR_HEAD + _anchor(0, 0, 1, 2, _PIC.format(id=2)) + _anchor(7, 0, 8, 2, _PIC.format(id=3)) + "</xdr:wsDr>"


def _read(data: bytes) -> dict:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist() if not i.is_dir()}


def _write(parts: dict) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _sheet_parts(parts: dict) -> dict:
    wbx = parts["xl/workbook.xml"].decode()
    rels = parts["xl/_rels/workbook.xml.rels"].decode()
    targets = {}
    for tag in re.findall(r"<Relationship\b[^>]*/>", rels):
        targets[re.search(r'Id="([^"]+)"', tag).group(1)] = re.search(r'Target="([^"]+)"', tag).group(1)
    out = {}
    for tag in re.findall(r"<sheet\b[^>]*/>", wbx):
        name = re.search(r'name="([^"]+)"', tag).group(1).replace("&amp;", "&")
        rid = re.search(r'r:id="([^"]+)"', tag).group(1)
        t = targets[rid]
        out[name] = t[1:] if t.startswith("/") else "xl/" + t
    return out


def _attach_drawing(parts: dict, sheet_part: str, drawing_name: str, drawing_xml: str) -> None:
    sdir, sbase = posixpath.split(sheet_part)
    rels_path = f"{sdir}/_rels/{sbase}.rels"
    rel = (
        f'<Relationship Id="rIdDrw1" Type="{NS_R}/drawing" Target="../drawings/{drawing_name}"/>'
    )
    if rels_path in parts:
        parts[rels_path] = parts[rels_path].decode().replace("</Relationships>", rel + "</Relationships>").encode()
    else:
        parts[rels_path] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{NS_PKG}">'
            f"{rel}</Relationships>"
        ).encode()
    sx = parts[sheet_part].decode()
    assert "</worksheet>" in sx
    sx = sx.replace("</worksheet>", f'<drawing xmlns:r="{NS_R}" r:id="rIdDrw1"/></worksheet>')
    parts[sheet_part] = sx.encode()
    parts[f"xl/drawings/{drawing_name}"] = drawing_xml.encode()
    parts[f"xl/drawings/_rels/{drawing_name}.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{NS_PKG}">'
        f'<Relationship Id="rIdImg1" Type="{NS_R}/image" Target="../media/image1.png"/></Relationships>'
    ).encode()
    ct = parts["[Content_Types].xml"].decode()
    if 'Extension="png"' not in ct:
        ct = ct.replace("</Types>", '<Default Extension="png" ContentType="image/png"/></Types>')
    ct = ct.replace(
        "</Types>",
        f'<Override PartName="/xl/drawings/{drawing_name}" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>',
    )
    parts["[Content_Types].xml"] = ct.encode()
    parts["xl/media/image1.png"] = _png()


def _post_process(data: bytes) -> bytes:
    parts = _read(data)
    sheets = _sheet_parts(parts)
    _attach_drawing(parts, sheets[SRC], "drawing1.xml", _invoice_drawing())
    _attach_drawing(parts, sheets[TREND], "drawing2.xml", _trend_drawing())
    wbx = parts["xl/workbook.xml"].decode()
    if "_FilterDatabase" not in wbx:
        order = re.findall(r'<sheet\b[^>]*name="([^"]+)"', wbx)
        adds = ""
        for idx, name in enumerate(order):
            if name in (TREND, SRC, DRAW01):
                adds += (
                    f'<definedName name="_xlnm._FilterDatabase" localSheetId="{idx}" hidden="1">'
                    f"'{name}'!$A$14:$G$28</definedName>"
                )
        wbx = re.sub(r"</sheets>", "</sheets><definedNames>" + adds + "</definedNames>", wbx, count=1)
        parts["xl/workbook.xml"] = wbx.encode()
    return _write(parts)


def _draw_tab(ws, *, label, key, draw_date, g15_override=None):
    ws["D4"] = draw_date
    ws["E10"] = label
    ws["B2"] = "Draw Request"
    for i, (code, name) in enumerate(CATS):
        r = 15 + 2 * i
        ws[f"A{r}"] = code
        ws[f"B{r}"] = name
        ws[f"G{r}"] = f"=SUMIFS(DETAILS!N:N,DETAILS!B:B,'{ws.title}'!A{r},DETAILS!H:H,{key})"
    if g15_override:
        ws["G15"] = g15_override
    ws["B24"] = "Subtotal"
    ws["G24"] = "=SUM(G15:G23)"
    ws["A26"] = 90
    ws["B26"] = "Builder's Fee"
    ws["G26"] = f"=SUMIFS(DETAILS!N:N,DETAILS!B:B,'{ws.title}'!A26,DETAILS!H:H,{key})"
    ws["E28"] = "Total Due"
    ws["G28"] = "=SUM(G24:G27)"
    ws.print_area = "A1:G28"
    ws.auto_filter.ref = "A14:G28"
    ws.column_dimensions["G"].width = 14
    ws.row_dimensions[21].hidden = True
    ws.row_dimensions[22].hidden = True


def _trend(ws):
    ws["D4"] = _dt.date(2026, 9, 30)
    ws["E10"] = "TST-02"
    ws["A13"] = "CATEGORY"
    ws["D13"] = "TST-M-01"
    ws["E13"] = "TST-M-02"
    ws["G13"] = "Total"
    for i, (code, name) in enumerate(CATS):
        r = 15 + 2 * i
        ws[f"A{r}"] = code
        ws[f"B{r}"] = name
        ws[f"D{r}"] = f"=VLOOKUP(A{r},'{DRAW01}'!A:G,7,FALSE)"
        ws[f"E{r}"] = f"=VLOOKUP($A{r},'{SRC}'!$A:$G,7,FALSE)"
        ws[f"G{r}"] = f"=SUM(D{r}:F{r})"
    ws["B24"] = "Subtotal"
    ws["D24"] = "=SUM(D15:D23)"
    ws["E24"] = "=SUM(E15:E23)"
    ws["G24"] = "=SUM(D24:F24)"
    ws["A26"] = 90
    ws["B26"] = "Builder's Fee"
    ws["D26"] = f"=VLOOKUP($A26,'{DRAW01}'!$A:$G,7,FALSE)"
    ws["E26"] = f"=VLOOKUP($A26,'{SRC}'!$A:$G,7,FALSE)"
    ws["G26"] = "=SUM(D26:F26)"
    ws["B28"] = "Total Due"
    ws["D28"] = "=SUM(D24:D27)"
    ws["E28"] = "=SUM(E24:E27)"
    ws["G28"] = "=SUM(D28:F28)"
    ws.print_area = "A1:G28"
    ws.auto_filter.ref = "A14:G28"
    ws.column_dimensions["D"] = ColumnDimension(ws, index="D", width=12, min=4, max=5, customWidth=True)
    ws.column_dimensions["G"] = ColumnDimension(ws, index="G", width=14, min=7, max=7, customWidth=True)


# DETAILS rows: (cost code, draw tag or date, amount)
LEDGER = [
    (15, "TST-02", 100),
    ("17", "TST-02", 50),          # numeric TEXT cost code
    (19, "TST-02", 12),
    (90, "TST-02", 2),
    (15, _dt.datetime(2026, 1, 15), 80),
    ("17", _dt.datetime(2026, 1, 15), 20),
    (90, _dt.datetime(2026, 1, 15), 4),
    (17, "TST-03", 25.5),
    ("17", "TST-03", 10),          # numeric TEXT cost code, TST-03
    (15, "TST-03", 40),
    (15, "TST-03", "n/a"),         # text amount: skipped
    (19, "TST-03", 7.25),
    (90, "TST-03", 3.5),
]


def _build(*, g15_override=None, g702=False, src_label="TST-02", extra_sheets=(), hooks=None) -> bytes:
    hooks = hooks or {}
    wb = openpyxl.Workbook()
    trend = wb.active
    trend.title = TREND
    d02 = wb.create_sheet(SRC)
    d01 = wb.create_sheet(DRAW01)
    det = wb.create_sheet("DETAILS")
    if g702:
        wb.create_sheet("G702 02")
    for name in extra_sheets:
        wb.create_sheet(name)
    det["B2"] = "Cost Code"
    det["H2"] = "Draw"
    det["N2"] = "Amount"
    for i, (code, tag, amount) in enumerate(LEDGER, start=3):
        det.cell(i, 2, code)
        det.cell(i, 8, tag)
        det.cell(i, 14, amount)
    _trend(trend)
    _draw_tab(d02, label=src_label, key="$E$10", draw_date=_dt.datetime(2026, 9, 30), g15_override=g15_override)
    _draw_tab(d01, label="TST-01", key="$D$4", draw_date=_dt.datetime(2026, 1, 15))
    for key, ws in (("trend", trend), ("src", d02), ("d01", d01), ("det", det)):
        if key in hooks:
            hooks[key](ws)
    if "wb" in hooks:
        hooks["wb"](wb)
    buf = BytesIO()
    wb.save(buf)
    return _post_process(buf.getvalue())


@pytest.fixture(scope="module")
def base() -> bytes:
    return _build()


@pytest.fixture(scope="module")
def finished(base) -> "dtabs.FinishedDrawTabs":
    return finish_draw_tabs(base, invoice_number=INVOICE, draw_date=DRAW_DATE)


def _part(data: bytes, sheet: str) -> str:
    return _sheet_parts(_read(data))[sheet]


def _xml(data: bytes, part: str) -> str:
    return _read(data)[part].decode()


def _cell_xml(data: bytes, sheet: str, ref: str) -> str:
    sx = _xml(data, _part(data, sheet))
    m = re.search(rf'<c r="{ref}"[^>]*?(?:/>|>.*?</c>)', sx, re.S)
    assert m, f"no cell {ref} on {sheet}"
    return m.group(0)


def _resolve(src_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target[1:]
    return posixpath.normpath(posixpath.join(src_dir, target))


def _rels_of(parts: dict, part: str):
    sdir, sbase = posixpath.split(part)
    path = f"{sdir}/_rels/{sbase}.rels"
    if path not in parts:
        return []
    return re.findall(r"<Relationship\b[^>]*/>", parts[path].decode())


def _drawing_target(parts: dict, sheet_part: str):
    for tag in _rels_of(parts, sheet_part):
        if "/drawing" in tag and "drawingMLChart" not in tag:
            return _resolve(posixpath.dirname(sheet_part), re.search(r'Target="([^"]+)"', tag).group(1))
    return None


def _defined_names(data: bytes):
    """(name, sheet_name_or_None, text) for every definedName."""
    parts = _read(data)
    wbx = parts["xl/workbook.xml"].decode()
    order = [m.replace("&amp;", "&") for m in re.findall(r'<sheet\b[^>]*name="([^"]+)"', wbx)]
    out = []
    for attrs, text in re.findall(r"<definedName\b([^>]*)>(.*?)</definedName>", wbx, re.S):
        name = re.search(r'name="([^"]+)"', attrs).group(1)
        lid = re.search(r'localSheetId="(\d+)"', attrs)
        sheet = order[int(lid.group(1))] if lid else None
        out.append((name, sheet, text))
    return out


def _sheet_order(data: bytes):
    wbx = _read(data)["xl/workbook.xml"].decode()
    return [m.replace("&amp;", "&") for m in re.findall(r'<sheet\b[^>]*name="([^"]+)"', wbx)]


def _end_col(ref: str) -> str:
    return re.search(r":([A-Z]+)\d+$", ref).group(1)


# ------------------------------------------------------------------ 1. the finish

def test_finish_inserts_new_tab_before_source_with_cached_values_and_totals(base, finished):
    assert _sheet_order(finished.workbook) == [TREND, NEW, SRC, DRAW01, "DETAILS"]

    ws_f = openpyxl.load_workbook(BytesIO(finished.workbook))[NEW]
    assert ws_f["G15"].value.startswith("=SUMIFS(")
    assert "'Draw Request 03'!A15" in ws_f["G15"].value
    assert "$E$10" in ws_f["G15"].value

    assert '<v>46325</v>' in _cell_xml(finished.workbook, NEW, "D4")
    assert SERIAL_03 == 46325
    assert openpyxl.load_workbook(BytesIO(finished.workbook))[NEW]["E10"].value == INVOICE

    ws = openpyxl.load_workbook(BytesIO(finished.workbook), data_only=True)[NEW]
    assert ws["G15"].value == 40          # text amount skipped
    assert ws["G17"].value == 35.5        # 25.5 + 10, numeric-text "17" matched
    assert ws["G19"].value == 7.25
    assert ws["G21"].value == 0
    assert ws["G24"].value == 82.75
    assert ws["G26"].value == 3.5
    assert ws["G28"].value == 86.25

    assert finished.tab_total == Decimal("86.25")
    assert isinstance(finished.tab_total, Decimal)

    assert ws.row_dimensions[21].hidden and ws.row_dimensions[22].hidden
    assert not ws.row_dimensions[15].hidden and not ws.row_dimensions[16].hidden
    assert not ws.row_dimensions[17].hidden and not ws.row_dimensions[26].hidden


# --------------------------------------------------------------- 2. drawings

def test_new_tab_drawing_resolves_with_invoice_shape_and_source_drawing_intact(finished):
    out = finished.workbook
    parts = _read(out)
    new_sheet = _sheet_parts(parts)[NEW]
    src_sheet = _sheet_parts(parts)[SRC]

    assert _xml(out, new_sheet).count("<drawing ") == 1
    new_drawing = _drawing_target(parts, new_sheet)
    assert new_drawing is not None and new_drawing in parts
    assert "INVOICE" in parts[new_drawing].decode()

    src_drawing = _drawing_target(parts, src_sheet)
    assert src_drawing is not None and src_drawing in parts
    assert "INVOICE" in parts[src_drawing].decode()
    assert src_drawing != new_drawing
    assert _xml(out, src_sheet).count("<drawing ") == 1


# ---------------------------------------------------------- 3. defined names

def test_defined_names_keep_their_sheets_and_new_tab_gets_its_own_copies(base, finished):
    before = _defined_names(base)
    after = _defined_names(finished.workbook)
    for name, sheet, _text in before:
        assert (name, sheet) in [(n, s) for n, s, _ in after], f"{name} lost its sheet {sheet}"

    new_names = [(n, t) for n, s, t in after if s == NEW]
    assert any(n == "_xlnm.Print_Area" and f"'{NEW}'!" in t for n, t in new_names)
    assert any(n == "_xlnm._FilterDatabase" and f"'{NEW}'!" in t for n, t in new_names)
    assert all(NEW not in t or s == NEW for n, s, t in after)


# ----------------------------------------------------------------- 4. trend

def test_trend_gets_new_column_after_last_draw_with_widened_totals_and_anchors(base, finished):
    out = finished.workbook
    tw = openpyxl.load_workbook(BytesIO(out))[TREND]
    tv = openpyxl.load_workbook(BytesIO(out), data_only=True)[TREND]

    assert tw["F13"].value == "TST-M-03"
    assert tw["F15"].value == f"=VLOOKUP($A15,'{NEW}'!$A:$G,7,FALSE)"
    assert tv["F15"].value == 40
    assert tv["F17"].value == 35.5
    assert tv["F19"].value == 7.25
    assert tv["F21"].value == 0

    assert tw["H13"].value == "Total"
    assert tw["H15"].value == "=SUM(D15:G15)"
    assert tw["H28"].value == "=SUM(D28:G28)"
    assert tw["G15"].value is None  # the spare column moved right and stays empty

    assert tw["E13"].value == "TST-M-02"
    assert tw["F28"].value == "=SUM(F24:F27)"

    assert finished.trend_total == Decimal("354.25")  # 104 + 164 + 86.25
    assert tv["H28"].value == 354.25

    tx = _xml(out, _part(out, TREND))
    base_tx = _xml(base, _part(base, TREND))
    assert re.search(r'<autoFilter ref="A14:H28"', tx)
    assert _end_col(re.search(r'<dimension ref="([^"]+)"', base_tx).group(1)) != ""
    assert _end_col(re.search(r'<dimension ref="([^"]+)"', tx).group(1)) == chr(ord(_end_col(re.search(r'<dimension ref="([^"]+)"', base_tx).group(1))) + 1)

    names = {(n, s): t for n, s, t in _defined_names(out) if s == TREND}
    assert names[("_xlnm.Print_Area", TREND)] == f"'{TREND}'!$A$1:$H$28"

    assert re.search(r'<col [^>]*min="4" max="6"', tx)
    assert re.search(r'<col [^>]*min="8" max="8"', tx)

    assert re.search(r"<xdr:from><xdr:col>8</xdr:col>", _read(out)[_drawing_target(_read(out), _part(out, TREND))].decode())
    assert re.search(r"<xdr:from><xdr:col>0</xdr:col>", _read(out)[_drawing_target(_read(out), _part(out, TREND))].decode())

    assert _cell_xml(out, TREND, "D4").count(str(SERIAL_03)) == 1
    assert tv["E10"].value == INVOICE


# ------------------------------------------------------------- 5. refusals

def test_refinishing_same_draw_raises_already_finished(finished):
    with pytest.raises(DrawAlreadyFinished) as exc:
        finish_draw_tabs(finished.workbook, invoice_number=INVOICE, draw_date=DRAW_DATE)
    assert exc.value.tab_name == NEW


def test_missing_previous_draw_raises_previous_draw_missing(base):
    with pytest.raises(PreviousDrawMissing) as exc:
        finish_draw_tabs(base, invoice_number="TST-05", draw_date=DRAW_DATE)
    assert exc.value.previous_label == "TST-04"


def test_g702_tab_for_source_draw_refuses(g702_base=None):
    data = _build(g702=True)
    with pytest.raises(UnsupportedTrackerLayout) as exc:
        finish_draw_tabs(data, invoice_number=INVOICE, draw_date=DRAW_DATE)
    assert "G702" in str(exc.value)


def test_unknown_formula_shape_names_the_cell():
    data = _build(g15_override="=A15*2")
    with pytest.raises(UnsupportedTrackerLayout) as exc:
        finish_draw_tabs(data, invoice_number=INVOICE, draw_date=DRAW_DATE)
    msg = str(exc.value)
    assert NEW in msg and "G15" in msg


def test_plan_next_draw_fields_and_draw_one_refusal(base):
    plan = plan_next_draw(base, invoice_number=INVOICE)
    assert plan == DrawTabPlan(
        invoice_number=INVOICE,
        draw_no=3,
        previous_label="TST-02",
        source_tab=SRC,
        new_tab=NEW,
        trend_tab=TREND,
    )
    with pytest.raises(UnsupportedTrackerLayout):
        plan_next_draw(base, invoice_number="TST-01")


# ------------------------------------------------------------ 6. render package

def test_render_package_is_one_sheet_without_formulas(finished):
    rp = finished.render_invoice
    parts = _read(rp)
    wbx = parts["xl/workbook.xml"].decode()
    assert len(re.findall(r"<sheet\b", wbx)) == 1
    sheet = _sheet_parts(parts)[NEW]
    sx = parts[sheet].decode()
    assert re.search(r"<f[\s>/]", sx) is None
    wb = openpyxl.load_workbook(BytesIO(rp), data_only=True)
    assert wb.sheetnames == [NEW]
    assert wb[NEW]["G15"].value == 40
    assert wb[NEW]["G28"].value == 86.25
    drawing = _drawing_target(parts, sheet)
    assert drawing is not None and drawing in parts
    for target in re.findall(r'Target="([^"]+)"', parts[posixpath.join(posixpath.dirname(drawing), "_rels", posixpath.basename(drawing) + ".rels")].decode()):
        assert _resolve(posixpath.dirname(drawing), target) in parts

    trend_rp = finished.render_trend
    assert openpyxl.load_workbook(BytesIO(trend_rp), data_only=True).sheetnames == [TREND]


def test_render_package_refuses_formula_without_cached_value(finished):
    parts = _read(finished.workbook)
    sheet = _sheet_parts(parts)[NEW]
    sx = parts[sheet].decode()
    sx2, n = re.subn(r'(<c r="G15"[^>]*>(?:<f[^>]*>.*?</f>))<v>[^<]*</v>', r"\1", sx, count=1, flags=re.S)
    assert n == 1
    parts[sheet] = sx2.encode()
    with pytest.raises(DrawTabsError) as exc:
        one_tab_render_package(_write(parts), NEW)
    assert "G15" in str(exc.value)


# ------------------------------------------------------------ 7. render existing

def test_render_existing_is_pure_and_returns_same_totals(finished):
    snapshot = bytes(finished.workbook)
    again = render_existing(finished.workbook, invoice_number=INVOICE)
    assert again.tab_total == finished.tab_total == Decimal("86.25")
    assert again.trend_total == finished.trend_total == Decimal("354.25")
    assert finished.workbook == snapshot


# ------------------------------------------------------------ 8. integrity

def test_finished_package_integrity(finished):
    out = finished.workbook
    parts = _read(out)
    names = set(parts)

    for name in parts:
        if not name.endswith(".rels"):
            continue
        src_dir = posixpath.dirname(posixpath.dirname(name))
        for tag in re.findall(r"<Relationship\b[^>]*/>", parts[name].decode()):
            if 'TargetMode="External"' in tag:
                continue
            target = re.search(r'Target="([^"]+)"', tag).group(1)
            assert _resolve(src_dir, target) in names, f"{name} -> {target} missing"

    ct = parts["[Content_Types].xml"].decode()
    defaults = set(re.findall(r'<Default Extension="([^"]+)"', ct))
    overrides = set(re.findall(r'<Override PartName="([^"]+)"', ct))
    for name in names:
        if name == "[Content_Types].xml":
            continue
        ext = name.rsplit(".", 1)[-1]
        assert ext in defaults or ("/" + name) in overrides, f"no content type for {name}"

    sheet_names = _sheet_order(out)
    assert len(sheet_names) == len(set(sheet_names))
    assert openpyxl.load_workbook(BytesIO(out)).sheetnames == sheet_names


# ------------------------------------------------------------ 9. byte stability

def test_untouched_members_are_byte_identical(base, finished):
    before = _read(base)
    after = _read(finished.workbook)
    src_part = _part(base, SRC)
    expected_changed = {
        "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels",
        "[Content_Types].xml",
        _part(base, TREND),
        _drawing_target(before, _part(base, TREND)),
    }
    for name, data in before.items():
        if name in expected_changed:
            continue
        assert after[name] == data, f"{name} changed unexpectedly"
    assert src_part in after and after[src_part] == before[src_part]
    assert after[_part(base, "DETAILS")] == before[_part(base, "DETAILS")]
    assert after[_part(base, DRAW01)] == before[_part(base, DRAW01)]

    added = set(after) - set(before)
    assert added, "expected the new sheet and drawing parts"
    assert all(n.startswith(("xl/worksheets/", "xl/drawings/")) for n in added)


# ====================================================== U-786 fix round 2 (D1-D24, test gaps)

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _edit_part(data, part, fn):
    parts = _read(data)
    parts[part] = fn(parts[part].decode()).encode()
    return _write(parts)


def _edit_sheet(data, sheet, fn):
    return _edit_part(data, _part(data, sheet), fn)


def _set_cell_xml(ref, new_xml):
    return lambda x: re.sub(rf'<c r="{ref}"[^>]*?(?:/>|>.*?</c>)', lambda m: new_xml, x, count=1, flags=re.S)


def _inject_rows(rows_xml):
    return lambda x: x.replace("</sheetData>", rows_xml + "</sheetData>", 1)


def _inject_before_close(snippet):
    return lambda x: x.replace("</worksheet>", snippet + "</worksheet>", 1)


def _add_rel_to(data, source_part, rel_xml):
    parts = _read(data)
    sdir, sbase = posixpath.split(source_part)
    path = f"{sdir}/_rels/{sbase}.rels"
    if path in parts:
        parts[path] = parts[path].decode().replace("</Relationships>", rel_xml + "</Relationships>").encode()
    else:
        parts[path] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{NS_PKG}">{rel_xml}</Relationships>'
        ).encode()
    return _write(parts)


def _sheet_tag(data, name):
    return re.search(rf'<sheet\b[^>]*name="{re.escape(name)}"[^>]*/>', _read(data)["xl/workbook.xml"].decode()).group(0)


def _wb_view(x, attr, val):
    def fix(m):
        tag = m.group(0)
        if re.search(rf"\s{attr}=\"", tag):
            return re.sub(rf'(\s{attr}=")\d+(")', rf"\g<1>{val}\2", tag)
        return tag.replace("<workbookView", f'<workbookView {attr}="{val}"', 1)

    return re.sub(r"<workbookView\b[^>]*/?>", fix, x, count=1)


def _finish(data, invoice=INVOICE):
    return finish_draw_tabs(data, invoice_number=invoice, draw_date=DRAW_DATE)


# ---- D1: Trend Total must cover the new column; remap every own-sheet reference

def _tight_trend(ws):
    for row in ws.iter_rows(min_row=15, max_row=28, min_col=7, max_col=7):
        c = row[0]
        if isinstance(c.value, str) and c.value.startswith("=SUM(D"):
            c.value = re.sub(r":F(\d+)\)$", r":E\1)", c.value)


def test_d1_total_range_ending_at_last_draw_column_refuses():
    data = _build(hooks={"trend": _tight_trend})
    assert openpyxl.load_workbook(BytesIO(data))[TREND]["G15"].value == "=SUM(D15:E15)"
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


def test_d1_own_sheet_formula_outside_moved_cells_is_remapped():
    def hook(ws):
        ws["C30"] = "=SUM(G28:G28)"

    out = _finish(_build(hooks={"trend": hook})).workbook
    assert openpyxl.load_workbook(BytesIO(out))[TREND]["C30"].value == "=SUM(H28:H28)"


# ---- D2: spacer is a row with no value, formula or string at all

def _zero_row_above_subtotal(ws):
    ws["A23"] = 92
    ws["B23"] = "Zero Fee"
    ws["G23"] = f"=SUMIFS(DETAILS!N:N,DETAILS!B:B,'{SRC}'!A23,DETAILS!H:H,$E$10)"


def test_d2_zero_category_does_not_hide_the_subtotal_under_it():
    out = _finish(_build(hooks={"src": _zero_row_above_subtotal})).workbook
    ws = openpyxl.load_workbook(BytesIO(out))[NEW]
    assert ws.row_dimensions[23].hidden
    assert not ws.row_dimensions[24].hidden
    assert not ws.row_dimensions[28].hidden


def _zero_trend_row_above_subtotal(ws):
    ws["A23"] = 21
    ws["B23"] = "Paint again"
    ws["D23"] = f"=VLOOKUP(A23,'{DRAW01}'!A:G,7,FALSE)"
    ws["E23"] = f"=VLOOKUP($A23,'{SRC}'!$A:$G,7,FALSE)"


def test_d2_trend_zero_category_does_not_hide_the_subtotal_under_it():
    out = _finish(_build(hooks={"trend": _zero_trend_row_above_subtotal})).workbook
    tv = openpyxl.load_workbook(BytesIO(out))[TREND]
    assert tv.row_dimensions[23].hidden
    assert not tv.row_dimensions[24].hidden


# ---- D3: every trend category row needs a lookup at the last draw column

def _trend_row_missing_last_lookup(ws):
    ws["A23"] = 21
    ws["B23"] = "Missing"
    ws["D23"] = f"=VLOOKUP(A23,'{DRAW01}'!A:G,7,FALSE)"


def test_d3_category_row_without_lookup_at_last_draw_column_names_the_cell():
    with pytest.raises(UnsupportedTrackerLayout) as exc:
        _finish(_build(hooks={"trend": _trend_row_missing_last_lookup}))
    assert "E23" in str(exc.value)


# ---- D4: calcChain is dropped when the package changes

def _add_calc_chain(data):
    parts = _read(data)
    parts["xl/calcChain.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<calcChain xmlns="{NS_MAIN}"><c r="G15" i="3"/></calcChain>'
    ).encode()
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].decode().replace(
        "</Relationships>",
        '<Relationship Id="rIdCc1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/></Relationships>',
    ).encode()
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].decode().replace(
        "</Types>",
        '<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/></Types>',
    ).encode()
    return _write(parts)


def test_d4_calc_chain_is_removed_when_finishing():
    data = _add_calc_chain(_build())
    out = _finish(data).workbook
    parts = _read(out)
    assert "xl/calcChain.xml" not in parts
    assert "calcChain" not in parts["xl/_rels/workbook.xml.rels"].decode()
    assert "calcChain" not in parts["[Content_Types].xml"].decode()
    openpyxl.load_workbook(BytesIO(out))


# ---- D5: render_existing only for the latest finished draw; duplicates refuse

def test_d5_render_existing_refuses_an_older_invoice(finished):
    with pytest.raises(UnsupportedTrackerLayout) as exc:
        render_existing(finished.workbook, invoice_number="TST-02")
    assert "latest" in str(exc.value)


def test_d5_duplicate_e10_labels_refuse(base):
    def hook(ws):
        ws["E10"] = "TST-02"

    with pytest.raises(UnsupportedTrackerLayout):
        render_existing(_build(hooks={"d01": hook}), invoice_number="TST-02")


# ---- D6: visibility looks at every draw column and the Total column

def _typed_money_in_old_draw(ws):
    ws["A23"] = 21
    ws["B23"] = "Typed"
    ws["D23"] = 5
    ws["E23"] = f"=VLOOKUP($A23,'{SRC}'!$A:$G,7,FALSE)"


def test_d6_row_with_typed_money_in_an_old_draw_column_stays_visible():
    out = _finish(_build(hooks={"trend": _typed_money_in_old_draw})).workbook
    assert not openpyxl.load_workbook(BytesIO(out))[TREND].row_dimensions[23].hidden


# ---- D7: Excel error values never computed around

def test_d7_error_in_a_matching_detail_amount_names_the_cell():
    data = _edit_sheet(_build(), "DETAILS", _set_cell_xml("N11", '<c r="N11" t="e"><v>#N/A</v></c>'))
    with pytest.raises(DrawTabsError) as exc:
        _finish(data)
    assert "N11" in str(exc.value)


def test_d7_error_operand_of_a_sum_names_the_cell():
    data = _edit_sheet(_build(), SRC, _inject_rows('<row r="23"><c r="G23" t="e"><v>#N/A</v></c></row>'))
    with pytest.raises(DrawTabsError) as exc:
        _finish(data)
    assert "G23" in str(exc.value)


# ---- D8: a hidden source tab never makes the copy or the render package hidden

def test_d8_hidden_source_tab_does_not_hide_the_clone_or_the_render_package():
    data = _edit_part(
        _build(), "xl/workbook.xml",
        # U-786: replace the tag's existing state attribute; appending a second one made malformed XML
        lambda x: re.sub(r'(<sheet\b[^>]*name="Draw Request 02"[^>]*?)(/>)', r'\1 state="hidden"\2',
                         re.sub(r'(<sheet\b[^>]*name="Draw Request 02"[^>]*?)\s+state="[^"]*"', r'\1', x, count=1),
                         count=1),
    )
    assert 'state="hidden"' in _sheet_tag(data, SRC)
    res = _finish(data)
    assert "state=" not in _sheet_tag(res.workbook, NEW)
    assert "state=" not in _sheet_tag(res.render_invoice, NEW)


# ---- D9: names are case-insensitive, length- and character-checked, and escaped

def test_d9_new_tab_name_clash_ignores_case():
    data = _build(extra_sheets=("draw request 03",))
    with pytest.raises(UnsupportedTrackerLayout):
        plan_next_draw(data, invoice_number=INVOICE)


@pytest.mark.parametrize("bad", ["X" * 32, "A:B", "A/B", "A\\B", "A?B", "A*B", "A[B", "A]B"])
def test_d9_invalid_sheet_names_refuse(bad):
    with pytest.raises(UnsupportedTrackerLayout):
        dtabs._check_sheet_name(bad)


def test_d9_sheet_name_is_escaped_for_xml_attributes():
    assert dtabs._xml_attr('A "B" & \'C\' <D>') == "A &quot;B&quot; &amp; &apos;C&apos; &lt;D&gt;"


def test_d9_ampersand_label_is_escaped_in_the_written_cell():
    out = _finish(_build(src_label="A&B-02"), invoice="A&B-03").workbook
    assert openpyxl.load_workbook(BytesIO(out))[NEW]["E10"].value == "A&B-03"


# ---- D10: render package keeps no element whose relationship it dropped

def test_d10_render_package_drops_elements_that_point_at_dropped_relationships():
    res = _finish(_build())
    inj = _inject_before_close(
        f'<hyperlinks><hyperlink ref="A1" xmlns:r="{NS_R}" r:id="rIdZ1"/></hyperlinks>'
        f'<legacyDrawing xmlns:r="{NS_R}" r:id="rIdZ2"/>'
    )
    rp = one_tab_render_package(_edit_sheet(res.workbook, NEW, inj), NEW)
    sx = _xml(rp, _part(rp, NEW))
    assert "hyperlinks" not in sx and "legacyDrawing" not in sx and "rIdZ" not in sx
    assert sx.count("<drawing ") == 1


def test_d10_string_formula_cells_become_inline_strings_in_the_render_package():
    res = _finish(_build())
    data = _edit_sheet(res.workbook, NEW, _inject_rows('<row r="30"><c r="B30" t="str"><f>"hi"</f><v>hi</v></c></row>'))
    rp = one_tab_render_package(data, NEW)
    cell = _cell_xml(rp, NEW, "B30")
    assert "inlineStr" in cell and "hi" in cell and "<f" not in cell


def test_d10_render_package_sheet_id_is_one_and_has_no_state():
    rp = _finish(_build()).render_invoice
    assert 'sheetId="1"' in _sheet_tag(rp, NEW)


# ---- D11: markup the copy cannot shift is refused

REFUSED_TREND_MARKUP = {
    "extLst": '<extLst><ext uri="{00000000-0000-0000-0000-000000000000}"/></extLst>',
    "colBreaks": '<colBreaks count="1" manualBreakCount="1"><brk id="3" max="1048575" man="1"/></colBreaks>',
    "hyperlinks": '<hyperlinks><hyperlink ref="A1" location="A1" display="x"/></hyperlinks>',
    "legacyDrawing": f'<legacyDrawing xmlns:r="{NS_R}" r:id="rIdLd1"/>',
    "tableParts": '<tableParts count="0"/>',
    "conditionalFormatting": '<conditionalFormatting sqref="A1"><cfRule type="expression" priority="1"><formula>1</formula></cfRule></conditionalFormatting>',
}


@pytest.mark.parametrize("name", list(REFUSED_TREND_MARKUP))
def test_d11_trend_with_unsupported_sheet_markup_refuses(name):
    data = _edit_sheet(_build(), TREND, _inject_before_close(REFUSED_TREND_MARKUP[name]))
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


def test_d11_trend_autofilter_with_filter_column_refuses():
    data = _edit_sheet(
        _build(), TREND,
        lambda x: re.sub(r'<autoFilter ref="A14:G28"\s*/>',
                         '<autoFilter ref="A14:G28"><filterColumn colId="0"><filters><filter val="x"/></filters></filterColumn></autoFilter>',
                         x, count=1),
    )
    assert "filterColumn" in _xml(data, _part(data, TREND))
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


def test_d11_trend_with_a_table_relationship_refuses():
    base = _build()
    data = _add_rel_to(base, _part(base, TREND), f'<Relationship Id="rIdT9" Type="{NS_R}/table" Target="../tables/table9.xml"/>')
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


def test_d11_trend_drawing_with_a_chart_refuses():
    base = _build()
    data = _add_rel_to(base, "xl/drawings/drawing2.xml", f'<Relationship Id="rIdC9" Type="{NS_R}/chart" Target="../charts/chart9.xml"/>')
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


def test_d11_source_drawing_with_a_chart_refuses():
    base = _build()
    data = _add_rel_to(base, "xl/drawings/drawing1.xml", f'<Relationship Id="rIdC8" Type="{NS_R}/chart" Target="../charts/chart8.xml"/>')
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


# ---- D12: date1904 workbooks refuse

def test_d12_date1904_workbook_refuses():
    base = _build()
    assert "<workbookPr" in _read(base)["xl/workbook.xml"].decode()
    data = _edit_part(base, "xl/workbook.xml", lambda x: re.sub(r"<workbookPr\b", '<workbookPr date1904="1"', x, count=1))
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


# ---- D13: G702/G703 tabs for the source draw by integer, any spelling

@pytest.mark.parametrize("name", ["G702-02", "G702 (02)", "G703 Draw 02", "G702 CW 2"])
def test_d13_g702_or_g703_tab_for_the_source_draw_refuses(name):
    with pytest.raises(UnsupportedTrackerLayout) as exc:
        _finish(_build(extra_sheets=(name,)))
    assert "G70" in str(exc.value)


def test_d13_g702_tabs_for_older_draws_stay_allowed():
    _finish(_build(extra_sheets=("G702 01",)))


# U-792: the de-branded tab names are the same per-draw pages and refuse the same way.
@pytest.mark.parametrize("name", ["Pay App 02", "PAY APP 2", "PayApp-02", "Pay Application 02",
                                  "SOV 02", "sov (02)", "Schedule of Values 02",
                                  "Pay-App 02", "Pay_App 02", "Pay Apps 02",
                                  "Schedule-of-Values 02", "Application for Payment 02"])
def test_d13_pay_app_or_sov_tab_for_the_source_draw_refuses(name):
    with pytest.raises(UnsupportedTrackerLayout) as exc:
        _finish(_build(extra_sheets=(name,)))
    assert "Pay App / SOV" in str(exc.value)


@pytest.mark.parametrize("name", ["Pay App 01", "SOV 01", "Sovereign 02", "Payables 02"])
def test_d13_older_draw_or_unrelated_tabs_stay_allowed(name):
    _finish(_build(extra_sheets=(name,)))


# ---- D14: the source draw tab holds only its own formulas

def test_d14_source_formula_into_another_tab_refuses_naming_the_cell():
    def hook(ws):
        ws["G26"] = f"=VLOOKUP(A26,'{DRAW01}'!$A:$G,7,FALSE)"

    with pytest.raises(UnsupportedTrackerLayout) as exc:
        _finish(_build(hooks={"src": hook}))
    assert "G26" in str(exc.value)


# ---- D15: unpadded labels across a digit boundary

def test_d15_unpadded_previous_label_is_found_for_a_padded_invoice():
    plan = plan_next_draw(_build(src_label="TST-9"), invoice_number="TST-10")
    assert plan.source_tab == SRC
    assert plan.previous_label == "TST-9"
    assert plan.new_tab == "Draw Request 10"


def test_d15_single_digit_tab_name_becomes_the_padded_new_name():
    base = _build(src_label="TST-9")
    data = _edit_part(base, "xl/workbook.xml", lambda x: x.replace('name="Draw Request 02"', 'name="Draw Request 9"'))
    plan = plan_next_draw(data, invoice_number="TST-10")
    assert plan.source_tab == "Draw Request 9"
    assert plan.new_tab == "Draw Request 10"


# ---- D16: calcPr goes at its schema position

def test_d16_missing_calc_pr_goes_before_the_first_schema_successor():
    out = dtabs._ensure_full_calc("<workbook><sheets/><definedNames/><webPublishing/><extLst/></workbook>")
    assert out.index("<calcPr") < out.index("<webPublishing")
    assert 'fullCalcOnLoad="1"' in out


# ---- D17: bad input is a DrawTabsError; a datetime is a valid draw date

def test_d17_datetime_draw_date_is_accepted(base):
    res = finish_draw_tabs(base, invoice_number=INVOICE, draw_date=_dt.datetime(2026, 10, 30))
    assert '<v>46325</v>' in _cell_xml(res.workbook, NEW, "D4")


@pytest.mark.parametrize(
    "rows",
    [
        '<row><c r="A41"><v>1</v></c></row>',
        '<row r="41"><c><v>1</v></c></row>',
        '<row r="41"><c r="A41" t="d"><v>2026-01-01T00:00:00Z</v></c></row>',
        '<row r="41"><c r="A41" t="s"><v>99999</v></c></row>',
    ],
    ids=["row-no-r", "cell-no-r", "date-type", "sst-out-of-range"],
)
def test_d17_malformed_cells_raise_unsupported_layout(base, rows):
    data = _edit_sheet(base, SRC, _inject_rows(rows))
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


# ---- D18: firstSheet and activeTab shift rules

def test_d18_first_sheet_equal_to_insert_position_is_not_shifted(base):
    data = _edit_part(base, "xl/workbook.xml", lambda x: _wb_view(x, "firstSheet", "1"))
    assert 'firstSheet="1"' in _read(_finish(data).workbook)["xl/workbook.xml"].decode()


def test_d18_active_tab_after_the_insert_position_shifts(base):
    data = _edit_part(base, "xl/workbook.xml", lambda x: _wb_view(x, "activeTab", "2"))
    assert 'activeTab="3"' in _read(_finish(data).workbook)["xl/workbook.xml"].decode()


# ---- D19: codeName and xr:uid never copied

def test_d19_clone_drops_code_name_and_xr_uid(base):
    def inj(x):
        x = x.replace(
            "<worksheet ",
            '<worksheet xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" '
            'xr:uid="{11111111-2222-3333-4444-555555555555}" ',
            1,
        )
        if "<sheetPr" in x:
            return re.sub(r"<sheetPr\b", '<sheetPr codeName="Sheet2"', x, count=1)
        return re.sub(r"(<worksheet\b[^>]*>)", r'\1<sheetPr codeName="Sheet2"/>', x, count=1)

    new_xml = _xml(_finish(_edit_sheet(base, SRC, inj)).workbook, _part(_finish(_edit_sheet(base, SRC, inj)).workbook, NEW))
    assert "codeName" not in new_xml and "xr:uid" not in new_xml


# ---- D20: phonetic runs are not string text

def test_d20_phonetic_runs_are_not_part_of_the_string():
    sst = (
        '<sst><si><t>Foo</t><rPh sb="0" eb="1"><t>PHON</t></rPh></si>'
        '<si><r><t>Bar</t></r><rPh sb="0" eb="1"><t>PHON</t></rPh></si></sst>'
    )
    assert dtabs._parse_sst(sst) == ["Foo", "Bar"]


# ---- D21: a VLOOKUP into text or a boolean refuses

def test_d21_vlookup_into_text_refuses(base):
    data = _edit_sheet(base, DRAW01, _set_cell_xml("G15", '<c r="G15" t="inlineStr"><is><t>text</t></is></c>'))
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


# ---- D24: float noise in stored money text is canonicalised; totals are cents

def test_d24_float_noise_text_is_read_as_its_shortest_form_and_totals_are_cents(base):
    data = _edit_sheet(base, "DETAILS", lambda x: x.replace("<v>25.5</v>", "<v>1974.0699999999999</v>", 1))
    res = _finish(data)
    assert "<v>1984.07</v>" in _cell_xml(res.workbook, NEW, "G17")
    assert res.tab_total == Decimal("2034.82")
    assert res.trend_total == Decimal("2302.82")


# ---- test gaps: mutants the reviewer proved survived

def _zero_and_revive(ws):
    ws["N12"] = 0            # the (15, TST-03) row becomes zero: its category must hide
    ws["B30"] = 21           # code 21 gets money for TST-03: its hidden source row must unhide
    ws["H30"] = "TST-03"
    ws["N30"] = 4


def test_gap_hidden_rows_are_recomputed_both_ways_on_the_new_tab():
    ws = openpyxl.load_workbook(BytesIO(_finish(_build(hooks={"det": _zero_and_revive})).workbook))[NEW]
    assert ws.row_dimensions[15].hidden
    assert not ws.row_dimensions[21].hidden


def test_gap_tab_selected_is_not_carried_into_the_clone(base):
    data = _edit_sheet(base, SRC, lambda x: re.sub(r"<sheetView\b", '<sheetView tabSelected="1"', x, count=1))
    assert 'tabSelected="1"' in _xml(data, _part(data, SRC))
    assert "tabSelected" not in _xml(_finish(data).workbook, _part(_finish(data).workbook, NEW))


def test_gap_full_calc_on_load_is_set(finished):
    assert 'fullCalcOnLoad="1"' in _read(finished.workbook)["xl/workbook.xml"].decode()


def test_gap_shared_or_array_formula_refuses(base):
    data = _edit_sheet(base, SRC, lambda x: re.sub(r'(<c r="G15"[^>]*>)<f>', r'\1<f t="array" ref="G15">', x, count=1))
    with pytest.raises(UnsupportedTrackerLayout):
        _finish(data)


def test_gap_duplicate_previous_tab_refuses(base):
    def hook(ws):
        ws["E10"] = "TST-02"

    with pytest.raises(UnsupportedTrackerLayout):
        plan_next_draw(_build(hooks={"d01": hook}), invoice_number=INVOICE)


def test_gap_cross_sheet_formula_right_of_the_last_draw_column_refuses():
    def hook(ws):
        ws["H15"] = f"=VLOOKUP(A15,'{DRAW01}'!A:G,7,FALSE)"

    with pytest.raises(UnsupportedTrackerLayout):
        _finish(_build(hooks={"trend": hook}))


def test_gap_formula_in_a_ledger_key_column_refuses():
    def hook(ws):
        ws["B3"] = "=15"

    with pytest.raises(UnsupportedTrackerLayout):
        _finish(_build(hooks={"det": hook}))


# ============================================================ U-786 close-out (B1-B4 + gaps)
# Each block is a regression for one close-out item or one surviving mutant gap.

_ZERO_CAT15_LEDGER_ROWS = (3, 7, 12)  # DETAILS rows tagged for category 15 in every draw


def _zero_cat15_ledger(ws):
    for r in _ZERO_CAT15_LEDGER_ROWS:
        ws[f"N{r}"] = 0


def _set_non_draw_column_d(ws):
    """Make column D a non-draw column: constants, with one 100 in row 15."""
    for r in range(15, 28):
        v = ws[f"D{r}"].value
        if isinstance(v, str) and v.startswith("=VLOOKUP"):
            ws[f"D{r}"] = 0
    ws["D15"] = 100


# ---- B1: per-row delta replaces invariant 2 ---------------------------------

def test_b1_constant_in_a_non_draw_column_covered_by_total_does_not_refuse():
    data = _build(hooks={"trend": _set_non_draw_column_d})
    before = dtabs._trend_total(dtabs._Model(dtabs._Workbook(dtabs._Pkg(data))), TREND)
    res = _finish(data)
    assert res.trend_total == before + res.tab_total
    assert res.tab_total == _finish(_build()).tab_total


def _spare_f15_five(ws):
    ws["F15"] = 5  # the spare column right of the last draw column


def _before_row15_total_excludes_spare(ws):
    ws["F15"] = 5
    ws["G15"] = "=SUM(D15:E15)"


def test_d1_row_delta_alone_refuses_when_one_category_total_moved_wrongly():
    # The final Trend carries a spare 5 in row 15 that the before-model's Total does not
    # cover. Invariants 1 and 3 hold (the Total Due row is unchanged); only row 15 moves wrong.
    final = _finish(_build(hooks={"trend": _spare_f15_five})).workbook
    plan = plan_next_draw(base_bytes(), invoice_number=INVOICE)
    with pytest.raises(UnsupportedTrackerLayout, match="row 15"):
        dtabs._check_totals(final, plan, 6, _build(hooks={"trend": _before_row15_total_excludes_spare}))


def test_d1_invariant_1_alone_new_column_total_must_equal_the_draw_tab_total():
    plan = plan_next_draw(base_bytes(), invoice_number=INVOICE)
    wrong = DrawTabPlan(**{**plan.__dict__, "new_tab": DRAW01})
    with pytest.raises(UnsupportedTrackerLayout, match="new column"):
        dtabs._check_totals(_finish(base_bytes()).workbook, wrong, 6, base_bytes())


def _spare_f28_one(ws):
    ws["F28"] = 1  # a spare cell in the Total Due row, right of the last draw column


def _before_due_total_excludes_spare(ws):
    ws["F28"] = 1
    ws["G28"] = "=SUM(D28:E28)"


def test_d1_invariant_3_trend_total_must_grow_by_the_draw_total():
    final = _finish(_build(hooks={"trend": _spare_f28_one})).workbook
    plan = plan_next_draw(base_bytes(), invoice_number=INVOICE)
    # Invariant 3 is checked before the per-row delta, so it is the reported refusal.
    with pytest.raises(UnsupportedTrackerLayout, match="did not grow"):
        dtabs._check_totals(final, plan, 6, _build(hooks={"trend": _before_due_total_excludes_spare}))


# ---- B2: D6 counts numeric values in the draw and Total columns only ---------

def _note_right_of_total_on_row_15(ws):
    ws["H15"] = "note"


def test_b2_text_right_of_the_total_column_on_a_zero_row_still_hides_the_row():
    out = _finish(_build(hooks={"trend": _note_right_of_total_on_row_15, "det": _zero_cat15_ledger})).workbook
    assert openpyxl.load_workbook(BytesIO(out))[TREND].row_dimensions[15].hidden


# ---- B3: render package drops customProperties and control wrappers ---------

_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
_CUSTOM_AND_CONTROLS = (
    '<customProperties><customPr name="ObjectLink" r:id="rId77"/></customProperties>'
    f'<mc:AlternateContent xmlns:mc="{_MC}"><mc:Choice Requires="x14" xmlns:x14="{_X14}">'
    '<controls><control r:id="rId78" name="Check"/></controls></mc:Choice><mc:Fallback/></mc:AlternateContent>'
    f'<mc:AlternateContent xmlns:mc="{_MC}"><mc:Choice Requires="x14" xmlns:x14="{_X14}">'
    '<oleObjects><oleObject r:id="rId79"/></oleObjects></mc:Choice><mc:Fallback/></mc:AlternateContent>'
)


def test_b3_render_package_drops_custom_properties_and_control_or_ole_wrappers(finished):
    data = _edit_sheet(finished.workbook, NEW, _inject_before_close(_CUSTOM_AND_CONTROLS))
    assert "customPr" in _xml(data, _part(data, NEW))
    out = one_tab_render_package(data, NEW)
    sx = _xml(out, _part(out, NEW))
    for token in ("customPr", "AlternateContent", "controls", "oleObject"):
        assert token not in sx, token
    ET.fromstring(sx.encode())
    openpyxl.load_workbook(BytesIO(out))


# ---- B4: calcPr goes before a DIRECT successor, not one nested in bookViews --

def test_b4_calc_pr_is_placed_before_a_direct_successor_not_a_nested_extlst():
    wbx = ('<workbook><bookViews><workbookView activeTab="0"><extLst><ext uri="x"/></extLst>'
           '</workbookView></bookViews><sheets/></workbook>')
    assert dtabs._ensure_full_calc(wbx) == (
        '<workbook><bookViews><workbookView activeTab="0"><extLst><ext uri="x"/></extLst>'
        '</workbookView></bookViews><sheets/><calcPr fullCalcOnLoad="1"/></workbook>')


def test_b4_calc_pr_goes_before_a_direct_extlst_child():
    wbx = '<workbook><sheets/><extLst><ext uri="y"/></extLst></workbook>'
    assert dtabs._ensure_full_calc(wbx) == (
        '<workbook><sheets/><calcPr fullCalcOnLoad="1"/><extLst><ext uri="y"/></extLst></workbook>')


# ---- surviving gaps (spacer, anchor, cols, E10 case, fullCalcOnLoad, guards) ----

def base_bytes():
    return _build()


def test_gap_spacer_not_pre_hidden_is_hidden_when_its_category_becomes_zero():
    ws = openpyxl.load_workbook(BytesIO(_finish(_build(hooks={"det": _zero_cat15_ledger})).workbook))[NEW]
    assert ws.row_dimensions[15].hidden and ws.row_dimensions[16].hidden


def test_gap_pre_hidden_spacer_is_shown_when_its_category_is_nonzero():
    def hide_spacer(ws):
        ws.row_dimensions[16].hidden = True

    ws = openpyxl.load_workbook(BytesIO(_finish(_build(hooks={"src": hide_spacer})).workbook))[NEW]
    assert not ws.row_dimensions[15].hidden and not ws.row_dimensions[16].hidden


def test_gap_drawing_anchor_at_the_insert_column_shifts_and_the_one_before_does_not():
    dpart = _drawing_target(_read(base_bytes()), _part(base_bytes(), TREND))
    extra = _anchor(4, 0, 5, 2, _PIC.format(id=7)) + _anchor(5, 0, 6, 2, _PIC.format(id=8))
    data = _edit_part(base_bytes(), dpart, lambda x: x.replace("</xdr:wsDr>", extra + "</xdr:wsDr>"))
    out = _finish(data).workbook
    dp = _drawing_target(_read(out), _part(out, TREND))
    cols = re.findall(r"<xdr:from><xdr:col>(\d+)</xdr:col>", _xml(out, dp))
    assert cols == ["0", "8", "4", "6"]


def _cols_of(data, sheet):
    out = {}
    for el in re.findall(r"<col\b([^>]*)/>", _xml(data, _part(data, sheet))):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', el))
        out[(int(attrs["min"]), int(attrs["max"]))] = float(attrs["width"])
    return out


def _spans_around_insert(ws):
    ws.column_dimensions["D"].max = 4
    ws.column_dimensions["E"] = ColumnDimension(ws, index="E", width=8, min=5, max=5, customWidth=True)
    ws.column_dimensions["F"] = ColumnDimension(ws, index="F", width=9, min=6, max=6, customWidth=True)


def test_gap_cols_span_starting_at_insert_plus_one_shifts_by_one():
    out = _finish(_build(hooks={"trend": _spans_around_insert})).workbook
    assert _cols_of(out, TREND) == {(4, 4): 12.0, (5, 6): 8.0, (7, 7): 9.0, (8, 8): 14.0}


def _lowercase_tst03_tag(ws):
    ws["H12"] = "tst-03"


def test_gap_e10_match_is_case_insensitive_for_detail_tags():
    base_total = _finish(base_bytes()).tab_total
    assert _finish(_build(hooks={"det": _lowercase_tst03_tag})).tab_total == base_total


def test_gap_full_calc_on_load_is_set_when_calc_pr_lacks_it():
    data = _edit_part(base_bytes(), "xl/workbook.xml",
                      lambda x: re.sub(r"<calcPr\b[^>]*/>", '<calcPr calcId="191029"/>', x))
    assert "fullCalcOnLoad" not in _read(data)["xl/workbook.xml"].decode()
    out = _finish(data).workbook
    assert re.search(r'<calcPr\b[^>]*fullCalcOnLoad="1"', _read(out)["xl/workbook.xml"].decode())


def _last_draw_col_reads_draw01(ws):
    for r in range(15, 28):
        v = ws[f"E{r}"].value
        if isinstance(v, str) and SRC in v:
            ws[f"E{r}"] = v.replace(SRC, DRAW01)


def test_gap_last_draw_column_reading_the_source_tab_guard_message():
    with pytest.raises(UnsupportedTrackerLayout, match="Trend's last draw column reads"):
        _finish(_build(hooks={"trend": _last_draw_col_reads_draw01}))


def _cross_sheet_right_of_last_draw(ws):
    # G20 is right of the last draw column and outside the columns the total-row scan reads
    ws["G20"] = "=DETAILS!N3"


def test_gap_cross_sheet_formula_right_of_the_insert_refuses_with_that_message():
    with pytest.raises(UnsupportedTrackerLayout, match="right of the new column and holds a cross-sheet formula"):
        _finish(_build(hooks={"trend": _cross_sheet_right_of_last_draw}))


def test_gap_d4_clone_alone_removes_the_calc_chain():
    plan = plan_next_draw(base_bytes(), invoice_number=INVOICE)
    out = dtabs.clone_draw_tab(_add_calc_chain(base_bytes()), plan, draw_date=DRAW_DATE)
    assert "xl/calcChain.xml" not in _read(out)


def test_gap_d4_extend_alone_removes_the_calc_chain():
    plan = plan_next_draw(base_bytes(), invoice_number=INVOICE)
    cloned = dtabs.clone_draw_tab(base_bytes(), plan, draw_date=DRAW_DATE)
    out = dtabs.extend_trend(_add_calc_chain(cloned), plan, draw_date=DRAW_DATE)
    assert "xl/calcChain.xml" not in _read(out)


def test_gap_d5_trend_label_that_is_not_the_invoice_refuses_on_its_own():
    def label_99(ws):
        ws["E10"] = "TST-99"

    with pytest.raises(UnsupportedTrackerLayout, match="latest"):
        render_existing(_build(hooks={"trend": label_99}), invoice_number="TST-02")


def test_gap_d5_last_column_reading_another_tab_refuses_on_its_own():
    with pytest.raises(UnsupportedTrackerLayout, match="latest"):
        render_existing(_build(hooks={"trend": _last_draw_col_reads_draw01}), invoice_number="TST-02")


def test_gap_d8_render_package_of_a_hidden_sheet_carries_no_state():
    def hide(x):
        return re.sub(rf'<sheet\b[^>]*name="{NEW}"[^>]*/>',
                      lambda m: re.sub(r'\sstate="[^"]*"', "", m.group(0))[:-2] + ' state="hidden"/>',
                      x, count=1)

    fin = finish_draw_tabs(base_bytes(), invoice_number=INVOICE, draw_date=DRAW_DATE)
    data = _edit_part(fin.workbook, "xl/workbook.xml", hide)
    assert 'state="hidden"' in _read(data)["xl/workbook.xml"].decode()
    out = one_tab_render_package(data, NEW)
    assert "state=" not in _sheet_tag(out, NEW)


def test_gap_d12_date1904_spelled_true_refuses():
    data = _edit_part(base_bytes(), "xl/workbook.xml",
                      lambda x: re.sub(r"<workbookPr\b", '<workbookPr date1904="true"', x, count=1))
    with pytest.raises(UnsupportedTrackerLayout, match="1904"):
        _finish(data)


def test_gap_d13_lowercase_g702_tab_refuses():
    with pytest.raises(UnsupportedTrackerLayout, match=r"G702/G703\) tab"):
        plan_next_draw(_build(extra_sheets=("g702 02",)), invoice_number=INVOICE)


def _half_cent_tst03_ledger(ws):
    ws["N15"] = 3.615  # the (90, TST-03) amount: 86.25 - 3.5 + 3.615 = 86.365


def test_gap_d24_total_has_two_decimals_and_half_cent_rounds_half_up():
    res = _finish(_build(hooks={"det": _half_cent_tst03_ledger}))
    assert str(res.tab_total) == "86.37"


# ====================================================== U-786 zip member metadata (idempotent bytes)


def test_finish_is_byte_identical_across_clock_ticks(base, monkeypatch):
    import itertools
    import time

    # zipfile stamps every member from time.localtime(); the clock is advanced one year between runs.
    clock = {"year": 2026}
    monkeypatch.setattr(time, "localtime", lambda *a: time.struct_time((clock["year"], 3, 4, 5, 6, 7, 0, 1, 0)))
    first = finish_draw_tabs(base, invoice_number=INVOICE, draw_date=DRAW_DATE)
    clock["year"] = 2027
    second = finish_draw_tabs(base, invoice_number=INVOICE, draw_date=DRAW_DATE)
    assert first.workbook == second.workbook
    assert first.render_invoice == second.render_invoice
    assert first.render_trend == second.render_trend


def test_zip_member_dates_keep_source_and_new_members_are_1980(base, finished):
    src = {i.filename: i.date_time for i in zipfile.ZipFile(BytesIO(base)).infolist() if not i.is_dir()}
    out = {i.filename: i.date_time for i in zipfile.ZipFile(BytesIO(finished.workbook)).infolist()}
    for name, dt in src.items():
        assert out[name] == dt, f"{name} rewrote its member date"
    new = set(out) - set(src)
    assert new, "expected new members"
    for name in new:
        assert out[name] == (1980, 1, 1, 0, 0, 0), name


# ---- R1: Excel nests AlternateContent inside controls / oleObjects; the render package
# must stay well-formed (balanced removal, then drop the emptied wrappers).

_NESTED_CONTROLS = (
    f'<mc:AlternateContent xmlns:mc="{_MC}"><mc:Choice Requires="x14" xmlns:x14="{_X14}">'
    '<controls><mc:AlternateContent xmlns:mc="' + _MC + '"><mc:Choice Requires="x14">'
    '<control shapeId="1025" r:id="rId78" name="Check Box 1"><controlPr defaultSize="0"/></control>'
    '</mc:Choice><mc:Fallback><control shapeId="1025" r:id="rId78" name="Check Box 1"/></mc:Fallback>'
    '</mc:AlternateContent></controls></mc:Choice></mc:AlternateContent>'
)
_NESTED_OLE = (
    f'<mc:AlternateContent xmlns:mc="{_MC}"><mc:Choice Requires="x14" xmlns:x14="{_X14}">'
    '<oleObjects><mc:AlternateContent xmlns:mc="' + _MC + '"><mc:Choice Requires="x14">'
    '<oleObject progId="Package" shapeId="1026" r:id="rId79"><objectPr defaultSize="0" r:id="rId80"/></oleObject>'
    '</mc:Choice><mc:Fallback><oleObject progId="Package" shapeId="1026" r:id="rId79"/></mc:Fallback>'
    '</mc:AlternateContent></oleObjects></mc:Choice></mc:AlternateContent>'
)


@pytest.mark.parametrize("fragment", [_NESTED_CONTROLS, _NESTED_OLE], ids=["nested-controls", "nested-ole"])
def test_r1_nested_alternate_content_in_controls_or_ole_renders_well_formed(fragment):
    res = _finish(_build())
    data = _edit_sheet(res.workbook, NEW, _inject_before_close(fragment))
    rp = one_tab_render_package(data, NEW)
    sx = _xml(rp, _part(rp, NEW))
    ET.fromstring(sx.encode())  # raises ParseError on orphan closing tags
    for gone in ("<controls", "<oleObjects", "AlternateContent", "<mc:Choice", "<mc:Fallback"):
        assert gone not in sx, gone
    openpyxl.load_workbook(BytesIO(rp))


# ---- R4: every attribute value _ser_rels writes is escaped (&, <, >, ", \t \n \r)

def test_r4_rels_target_with_ampersand_quote_and_tab_round_trips():
    target = 'a&b"c\td<e>'
    out = dtabs._ser_rels([{"Id": "rId1", "Type": "http://x/rel", "Target": target}])
    root = ET.fromstring(out)
    assert root[0].get("Target") == target


# ------------------------------------------------ U-786 security fix round (S1, S3, S4, S6, S2 caps)

import contextlib as _ctx
import signal as _signal
import time as _time

_U_WS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"


def _u786_pkg(rows_xml: str, *, ws_type: str = _U_WS, extra=()) -> bytes:
    """A one-sheet package whose only sheet is named S, holding rows_xml."""
    ct = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    wb = (
        '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        '<sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{ws_type}" Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{rows_xml}</sheetData></worksheet>"
    )
    members = [("[Content_Types].xml", ct), ("_rels/.rels", root), ("xl/workbook.xml", wb),
               ("xl/_rels/workbook.xml.rels", wb_rels), ("xl/worksheets/sheet1.xml", sheet), *extra]
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members:
            z.writestr(name, body)
    return out.getvalue()


class _U786Deadline(Exception):
    pass


@_ctx.contextmanager
def _u786_deadline(seconds: float):
    def _boom(*_):
        raise _U786Deadline(f"still running after {seconds}s")

    old = _signal.signal(_signal.SIGALRM, _boom)
    _signal.setitimer(_signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        _signal.setitimer(_signal.ITIMER_REAL, 0)
        _signal.signal(_signal.SIGALRM, old)


def _u786_model(data: bytes):
    return dtabs._Model(dtabs._Workbook(dtabs._Pkg(data)))


def test_u786_s1_sum_to_bottom_is_sparse_and_memoises_only_real_cells():
    data = _u786_pkg(
        '<row r="12"><c r="G12"><v>5</v></c></row><row r="13"><c r="G13"><v>7</v></c></row>'
        '<row r="1"><c r="A1"><f>SUM(G12:G1048576)</f></c></row>'
    )
    m = _u786_model(data)
    with _u786_deadline(5):
        assert m.value("S", 1, 1) == Decimal(12)
    assert len(m._memo) <= 3, f"memo holds {len(m._memo)} entries for a 3-cell sheet"


def test_u786_s1_sum_over_whole_sheet_completes_quickly():
    data = _u786_pkg('<row r="5"><c r="B5"><v>3</v></c></row><row r="1"><c r="A1"><f>SUM(B1:XFD1048576)</f></c></row>')
    m = _u786_model(data)
    t0 = _time.monotonic()
    with _u786_deadline(5):
        assert m.value("S", 1, 1) == Decimal(3)
    assert _time.monotonic() - t0 < 1.0


def test_u786_s4_formula_echo_is_capped_at_eighty_characters():
    data = _u786_pkg('<row r="1"><c r="A1"><f>NOTAFUNC(' + "Q" * 5000 + ")</f></c></row>")
    m = _u786_model(data)
    with pytest.raises(UnsupportedTrackerLayout) as exc:
        m.value("S", 1, 1)
    assert "Q" * 81 not in str(exc.value)


def test_u786_s2_package_refuses_a_member_over_the_size_cap():
    bomb = ("customXml/unreferenced.bin", b"\0" * (65 * 1024 * 1024))
    with pytest.raises(UnsupportedTrackerLayout, match="too large"):
        dtabs._Pkg(_u786_pkg("", extra=[bomb]))


def test_u786_s3_crafted_relationship_type_round_trips_well_formed(base):
    crafted = _edit_part(base, "xl/_rels/workbook.xml.rels",
                         lambda x: x.replace('relationships/worksheet"', 'relationships/x&amp;&quot;/worksheet"'))
    assert b"x&amp;&quot;/worksheet" in _read(crafted)["xl/_rels/workbook.xml.rels"]
    out = _finish(crafted)
    for name, body in _read(out.workbook).items():
        if name.endswith((".xml", ".rels")):
            ET.fromstring(body)  # raises ParseError naming nothing; the test fails on the first bad part


def test_u786_s3_malformed_rewrite_raises_drawtabs_error_naming_the_part(base, monkeypatch):
    monkeypatch.setattr(dtabs._SheetDoc, "to_xml", lambda self: "<a>")
    with pytest.raises(DrawTabsError, match=r"xl/worksheets/sheet\d+\.xml"):
        dtabs.refresh_cached_values(base, [TREND])


def test_u786_s6_text_in_a_draw_column_of_a_zero_row_keeps_it_hidden():
    def hook(ws):
        ws["D15"] = "n/a"

    out = _finish(_build(hooks={"trend": hook, "det": _zero_cat15_ledger})).workbook
    assert openpyxl.load_workbook(BytesIO(out))[TREND].row_dimensions[15].hidden


# ---- U-786 security follow-up round (T1 sparse SUM budget, T2 echo, T3 scope, T5 mapping, T6 caps)


def _t1_rows_pkg(n: int) -> bytes:
    rows = "".join(
        f'<row r="{r}"><c r="B{r}"><v>1</v></c><c r="G{r}"><f>SUM(B{r}:F{r})</f></c></row>' for r in range(1, n + 1)
    )
    return _u786_pkg(rows)


def test_t1_twenty_thousand_per_row_sums_on_a_twenty_thousand_row_sheet_are_fast():
    m = _u786_model(_t1_rows_pkg(20000))
    t0 = _time.monotonic()
    for r in range(1, 20001):
        assert m.value("S", 7, r) == Decimal(1)
    assert _time.monotonic() - t0 < 2.0


def test_t1_sums_over_a_dense_sheet_hit_the_evaluation_budget():
    rows = "".join(f'<row r="{r}"><c r="A{r}"><v>1</v></c></row>' for r in range(2, 200_002))
    cells = "".join(f'<c r="{dtabs._n2c(c)}1"><f>SUM(A2:XFD1048576)</f></c>' for c in range(2, 202))
    m = _u786_model(_u786_pkg(f'<row r="1">{cells}</row>' + rows))
    with _u786_deadline(60):
        with pytest.raises(UnsupportedTrackerLayout, match="evaluation budget"):
            for c in range(2, 202):
                m.value("S", c, 1)


def test_t2_drawtabs_error_message_is_capped_at_600_characters():
    assert len(str(DrawTabsError("x" * 5000))) <= 600


def test_t2_cross_sheet_formula_refusal_does_not_echo_the_full_formula():
    xml = ('<worksheet><sheetData><row r="1"><c r="A1"><f>' + "'" + "Q" * 300 + "'!B1"
           + '</f></c></row></sheetData></worksheet>')
    with pytest.raises(UnsupportedTrackerLayout) as exc:
        dtabs._check_no_cross_sheet_formulas(xml, SRC)
    assert "Q" * 100 not in str(exc.value)


def test_t3_untouched_malformed_customxml_part_does_not_block_a_finish(base):
    parts = _read(base)
    parts["customXml/item9.xml"] = b'<?xml version="1.0" encoding="utf-16"?><a/>'
    assert _finish(_write(parts)).workbook


def _t3_corrupt_worksheets_on_write(monkeypatch):
    real = dtabs._Pkg.to_bytes

    def corrupt(self, names=None):
        for n in list(self.parts):
            if n.startswith("xl/worksheets/") and n.endswith(".xml"):
                self.parts[n] = b"<a>"
        return real(self, names)

    monkeypatch.setattr(dtabs._Pkg, "to_bytes", corrupt)


def test_t3_trend_check_catches_a_malformed_rewrite(base, monkeypatch):
    plan = dtabs.plan_next_draw(base, invoice_number=INVOICE)
    cloned = dtabs.clone_draw_tab(base, plan, draw_date=DRAW_DATE)
    _t3_corrupt_worksheets_on_write(monkeypatch)
    with pytest.raises(DrawTabsError, match="not well-formed"):
        dtabs.extend_trend(cloned, plan, draw_date=DRAW_DATE)


def _t3_corrupt_worksheet_puts(monkeypatch):
    real = dtabs._Pkg.put

    def put(self, name, data):
        # Keep <sheetData> parseable by the regex reader but break the XML itself.
        if name.startswith("xl/worksheets/") and name.endswith(".xml"):
            data = data.replace("</worksheet>", "</worksheet><x>") if isinstance(data, str) else data.replace(b"</worksheet>", b"</worksheet><x>")
        return real(self, name, data)

    monkeypatch.setattr(dtabs._Pkg, "put", put)


def test_t3_clone_check_catches_a_malformed_rewrite(base, monkeypatch):
    plan = dtabs.plan_next_draw(base, invoice_number=INVOICE)
    _t3_corrupt_worksheet_puts(monkeypatch)
    monkeypatch.setattr(dtabs, "_refresh", lambda p, _names: p)  # isolate the clone's own check
    with pytest.raises(DrawTabsError, match="not well-formed"):
        dtabs.clone_draw_tab(base, plan, draw_date=DRAW_DATE)


def test_t3_render_check_catches_a_malformed_rewrite(finished, monkeypatch):
    _t3_corrupt_worksheet_puts(monkeypatch)
    with pytest.raises(DrawTabsError, match="not well-formed"):
        dtabs.one_tab_render_package(finished.workbook, NEW)


def test_t5_corrupt_zip_is_refused_as_unsupported_layout():
    with pytest.raises(UnsupportedTrackerLayout):
        dtabs.finish_draw_tabs(b"PK\x03\x04 not really a zip", invoice_number=INVOICE, draw_date=DRAW_DATE)


def test_t5_deep_sum_chain_is_refused_not_a_recursion_error():
    rows = "".join(f'<row r="{r}"><c r="A{r}"><f>SUM(A{r + 1}:A{r + 1})</f></c></row>' for r in range(1, 2001))
    m = _u786_model(_u786_pkg(rows))
    with pytest.raises(UnsupportedTrackerLayout, match="nest too deeply"):
        m.value("S", 1, 1)


def test_t6_package_total_size_cap_refuses_members_that_sum_over_it(monkeypatch):
    base_len = sum(len(v) for v in dtabs._Pkg(_u786_pkg("")).parts.values())
    monkeypatch.setattr(dtabs, "_MAX_PACKAGE_READ_BYTES", base_len + 1000)
    extra = [(f"customXml/item{i}.bin", b"\0" * 600) for i in range(3)]
    with pytest.raises(UnsupportedTrackerLayout, match="too large"):
        dtabs._Pkg(_u786_pkg("", extra=extra))


# --------------------------------------------------------------------------------------------
# U-786 final security round (EM-built): SUMIFS memo + budget, the Trend Total column found once,
# bounded A1 references, every crafted-zip read error a refusal, and every tracker-derived value
# in a refusal routed through echo_text.

_U786_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_U786_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_U786_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _u786_book(sheets) -> bytes:
    """A package of several sheets: [(name, rows_xml)]."""
    n = len(sheets)
    ct = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(n)
        )
        + "</Types>"
    )
    root = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_NS_PKG}">'
        f'<Relationship Id="rId1" Type="{_U786_NS_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    wb = (
        f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{_U786_NS_MAIN}" xmlns:r="{_U786_NS_REL}"><sheets>'
        + "".join(f'<sheet name="{nm}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, (nm, _) in enumerate(sheets))
        + "</sheets></workbook>"
    )
    wb_rels = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_NS_PKG}">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" Type="{_U786_NS_REL}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(n)
        )
        + "</Relationships>"
    )
    members = [("[Content_Types].xml", ct), ("_rels/.rels", root), ("xl/workbook.xml", wb), ("xl/_rels/workbook.xml.rels", wb_rels)]
    members += [
        (f"xl/worksheets/sheet{i + 1}.xml",
         f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{_U786_NS_MAIN}"><sheetData>{rows}</sheetData></worksheet>')
        for i, (_, rows) in enumerate(sheets)
    ]
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members:
            z.writestr(name, body)
    return out.getvalue()


def _u786_istr(ref, text):
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def test_u786_sumifs_total_is_walked_once_per_key_and_charged_to_the_budget():
    """Many SUMIFS cells keyed on the same (cost code, draw) used to re-walk every matching ledger
    row each (cells x rows, uncharged): a padded tracker stalled a finish for minutes. Now each key
    is walked once and its rows are charged to the evaluation budget."""
    rows_ledger = 1500
    rows_sumifs = 1500
    ledger = "".join(
        f'<row r="{r}"><c r="B{r}"><v>1</v></c>{_u786_istr(f"H{r}", "X")}<c r="N{r}"><v>1</v></c></row>'
        for r in range(2, rows_ledger + 2)
    )
    tab = f'<row r="10">{_u786_istr("E10", "X")}</row>' + "".join(
        f'<row r="{r}"><c r="A{r}"><v>1</v></c>'
        f'<c r="G{r}"><f>SUMIFS(DETAILS!N:N,DETAILS!B:B,S!$A{r},DETAILS!H:H,$E$10)</f></c></row>'
        for r in range(12, rows_sumifs + 12)
    )
    m = _u786_model(_u786_book([("S", tab), ("DETAILS", ledger)]))
    with _u786_deadline(20):
        for r in range(12, rows_sumifs + 12):
            assert m.value("S", 7, r) == Decimal(rows_ledger)
    assert m._visits == rows_ledger, f"{m._visits} ledger visits charged for one (cost code, draw) key"


def test_u786_trend_total_column_is_found_once_per_model(monkeypatch):
    """The visibility pass asked for the Total column on every category row, and each ask scanned
    the whole sheet for its CATEGORY header (quadratic on a long Trend)."""
    n = 200
    src = "".join(f'<row r="{r}"><c r="A{r}"><v>{r}</v></c><c r="G{r}"><v>1</v></c></row>' for r in range(1, n + 1))
    rows = [f'<row r="5">{_u786_istr("A5", "CATEGORY")}{_u786_istr("D5", "Draw 1")}{_u786_istr("E5", "Total")}</row>']
    for k in range(n):
        r = k + 6
        rows.append(
            f'<row r="{r}"><c r="A{r}"><v>{k + 1}</v></c><c r="D{r}"><f>VLOOKUP($A{r},Src!A:G,7,FALSE)</f></c>'
            f'<c r="E{r}"><f>SUM(D{r}:D{r})</f></c></row>'
        )
    rows.append(
        f'<row r="{n + 6}">{_u786_istr(f"C{n + 6}", "Total Due")}'
        f'<c r="D{n + 6}"><f>SUM(D6:D{n + 5})</f></c><c r="E{n + 6}"><f>SUM(E6:E{n + 5})</f></c></row>'
    )
    data = _u786_book([(dtabs.TREND_TAB, "".join(rows)), ("Src", src)])
    calls = {"n": 0}
    real = dtabs._category_header

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(dtabs, "_category_header", counted)
    dtabs.refresh_cached_values(data, [dtabs.TREND_TAB])
    assert calls["n"] <= 3, f"_category_header ran {calls['n']} times for one Trend refresh"


@pytest.mark.parametrize("ref", ["x", "", "AAAA1", "A0", "a1", "A" + "9" * 5000])
def test_u786_a_malformed_cell_reference_is_a_refusal(ref):
    with pytest.raises(UnsupportedTrackerLayout):
        dtabs._split_ref(ref)


def test_u786_a_crafted_cell_reference_in_a_sheet_is_a_refusal_not_a_500():
    data = _u786_pkg('<row r="1"><c r="' + "A" + "9" * 5000 + '"><v>1</v></c></row>')
    with pytest.raises(UnsupportedTrackerLayout):
        dtabs.refresh_cached_values(data, ["S"])


def _u786_patch_zip(data: bytes, *, flag: int = 0, method=None) -> bytes:
    """Set a general-purpose flag bit and/or the compression method on every member header."""
    b = bytearray(data)
    for sig, flag_at, method_at in ((b"PK\x01\x02", 8, 10), (b"PK\x03\x04", 6, 8)):
        i = b.find(sig)
        while i >= 0:
            struct.pack_into("<H", b, i + flag_at, struct.unpack_from("<H", b, i + flag_at)[0] | flag)
            if method is not None:
                struct.pack_into("<H", b, i + method_at, method)
            i = b.find(sig, i + 4)
    return bytes(b)


@pytest.mark.parametrize(
    "patch",
    [
        {"flag": 0x1},  # encrypted member -> RuntimeError in zipfile
        {"method": 99},  # unknown compression method -> NotImplementedError
        {"method": 12},  # deflate bytes read as bzip2 -> OSError
    ],
    ids=["encrypted", "unknown-method", "bad-bzip2"],
)
def test_u786_every_crafted_zip_read_error_is_a_refusal(patch):
    with pytest.raises(UnsupportedTrackerLayout, match="not a readable"):
        dtabs._Pkg(_u786_patch_zip(_u786_pkg('<row r="1"><c r="A1"><v>1</v></c></row>'), **patch))


# Refusal text (U-786 S4/H). A value formatted into a refusal is tracker text unless it is one of:
# echo_text(...) (repr, capped), a cell reference rebuilt from a validated (col, row) via _cref/_n2c,
# or an entry below. Entries are keyed by (enclosing function, expression), so the same name in a
# new place is not trusted until someone reads it and adds it here with its reason.
_U786_SAFE_FORMATTED = {
    ("_charge_member", "_MAX_MEMBER_BYTES"): "constant",
    ("_charge_member", "_MAX_PACKAGE_READ_BYTES"): "constant",
    ("_charge_member", "info.file_size"): "int from the zip directory",
    ("_check_wellformed", "ex"): "ET.ParseError: position only, no content",
    ("_SheetDoc.__init__", "e"): "_CellError, itself built under these rules",
    ("_const_at", "cell.ref"): "rebuilt from the validated (col, row)",
    ("_const_at", "e"): "_CellError, itself built under these rules",
    ("_Model.value", "e"): "_CellError, itself built under these rules",
    ("_Model._formula", "ref"): "cell.ref, rebuilt from the validated (col, row)",
    ("_Model._ledger", "c.ref"): "rebuilt from the validated (col, row)",
    ("_Model._sumifs", "ref"): "cell.ref, rebuilt from the validated (col, row)",
    ("_Model._sumifs", "crow"): "int from a bounded regex group",
    ("_Model._sumifs", "ncell.ref"): "rebuilt from the validated (col, row)",
    ("_Model._vlookup", "cell.ref"): "rebuilt from the validated (col, row)",
    ("_Model._vlookup", "kr"): "int from a bounded regex group",
    ("_Model._vlookup", "key"): "Decimal",
    ("_Model._vlookup", "row"): "int row number",
    ("plan_next_draw", "draw_no"): "int",
    ("plan_next_draw", "suffix"): "digits from re (\\d+)$ on a sheet name",
    ("_check_markup", "m.group(1)"): "one of the fixed tag names in _MARKUP_REFUSED",
    ("_remove_balanced", "name"): "a tag name passed by this module, never tracker text",
    ("_num", "what"): "a label built by this module (row Decimal + fixed words)",
    ("_check_totals", "new_due"): "Decimal",
    ("_check_totals", "tab_total"): "Decimal",
    ("_check_totals", "label"): "Decimal cost code or the constant 'Total Due'",
    ("_check_totals", "after_total - before_total"): "Decimal",
    ("_check_totals", "added"): "Decimal",
    ("_tracker_boundary.guarded", "type(error).__name__"): "an exception class name",
    ("_tracker_boundary.guarded", "fn.__name__"): "a function name in this module",
    ("_check_no_cross_sheet_formulas", "cell.ref"): "rebuilt from the validated (col, row)",
    ("_extend_trend", "cell.ref"): "rebuilt from the validated (col, row)",
    ("_extend_trend", "sc.ref"): "rebuilt from the validated (col, row)",
    ("one_tab_render_package", "cell.ref"): "rebuilt from the validated (col, row)",
    ("render_existing", "latest"): "a constant message bound to a local name",
}
# These take the raw value and format it themselves; their __init__ messages are checked below.
_U786_SELF_FORMATTING = {"DrawAlreadyFinished", "PreviousDrawMissing"}
_U786_TRUSTED_CALLS = {"echo_text", "_cref", "_n2c"}


def _u786_trusted(node) -> bool:
    """The WHOLE formatted expression is a trusted call, or a join whose every item is one: a prefix
    match would also pass `echo_text(a) + raw`."""
    import ast

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in _U786_TRUSTED_CALLS
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join"
            and isinstance(node.func.value, ast.Constant) and len(node.args) == 1 and not node.keywords
            and isinstance(node.args[0], (ast.GeneratorExp, ast.ListComp)) and _u786_trusted(node.args[0].elt))
_U786_REFUSALS = {"DrawTabsError", "UnsupportedTrackerLayout", "DrawAlreadyFinished", "PreviousDrawMissing", "_CellError"}


def _u786_refusal_messages():
    """(function, lineno, message node) for every refusal raised in draw_tabs, and every message a
    refusal subclass passes to super().__init__."""
    import ast
    import inspect

    src = inspect.getsource(dtabs)
    found = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def _scope(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = visit_ClassDef = _scope

        def visit_Call(self, node):
            f = node.func
            is_raise_target = isinstance(f, ast.Name) and f.id in _U786_REFUSALS
            is_super_init = (isinstance(f, ast.Attribute) and f.attr == "__init__" and isinstance(f.value, ast.Call)
                             and isinstance(f.value.func, ast.Name) and f.value.func.id == "super"
                             and len(self.stack) >= 2 and self.stack[-2] in _U786_REFUSALS)
            if is_super_init and self.stack[-2] == "DrawTabsError":
                is_super_init = False  # the base class's own plumbing (it escapes and caps)
            if is_raise_target and f.id in _U786_SELF_FORMATTING:
                is_raise_target = False
            if is_raise_target or is_super_init:
                for arg in node.args:
                    found.append((".".join(self.stack), node.lineno, arg))
            self.generic_visit(node)

    V().visit(ast.parse(src))
    return src, found


def test_u786_every_tracker_value_in_a_refusal_goes_through_echo_text():
    import ast

    src, found = _u786_refusal_messages()
    assert len(found) > 50, "the visitor found too few refusals to be looking at the module"
    raw, used = [], set()
    for scope, lineno, arg in found:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            continue
        if not isinstance(arg, ast.JoinedStr):  # a variable, '+', .format() or % hides what it holds
            key = (scope, ast.get_source_segment(src, arg))
            if key in _U786_SAFE_FORMATTED:
                used.add(key)
            else:
                raw.append((lineno, *key))
            continue
        for fv in arg.values:
            if not isinstance(fv, ast.FormattedValue):
                continue
            text = ast.get_source_segment(src, fv.value)
            if _u786_trusted(fv.value):
                continue
            key = (scope, text)
            if key in _U786_SAFE_FORMATTED:
                used.add(key)
            else:
                raw.append((lineno, scope, text))
    assert not raw, f"tracker-derived values formatted raw into refusals: {raw}"
    assert not set(_U786_SAFE_FORMATTED) - used, f"stale allowlist entries: {set(_U786_SAFE_FORMATTED) - used}"


# --------------------- U-786 hardening round (H): every crafted-input failure is a refusal, never a 500

import functools as _h_functools  # noqa: E402
import inspect as _h_inspect  # noqa: E402
import logging as _h_logging  # noqa: E402


def test_h_infinite_detail_amount_is_a_refusal_naming_the_cell():
    """1e999 parses to Infinity; Infinity - Infinity raised decimal.InvalidOperation (a 500)."""
    data = _edit_sheet(_build(), "DETAILS", _set_cell_xml("N11", '<c r="N11"><v>1e999</v></c>'))
    with pytest.raises(UnsupportedTrackerLayout, match="N11"):
        _finish(data)


@pytest.mark.parametrize("r", ["x1", "1" * 5000, "0", "²", " 1", "12345678"], ids=["text", "5000-digits", "zero", "superscript", "space", "8-digits"])
def test_h_row_number_that_is_not_an_excel_row_is_a_refusal(r):
    data = _edit_sheet(_build(), SRC, _inject_rows(f'<row r="{r}"/>'))
    with pytest.raises(UnsupportedTrackerLayout, match="row"):
        _finish(data)


_H_BIG = "1" * 5000


# A row past Excel's limits no longer matches the v1 shapes, so the formula is refused by the check
# that meets it first (copy check, Trend check or evaluator) with a message naming the cell: a
# targeted refusal, never the boundary's catch-all and never a 500.
@pytest.mark.parametrize(
    "sheet, ref, xml, why",
    [
        (SRC, "G15", f"<c r=\"G15\"><f>SUMIFS(DETAILS!N:N,DETAILS!B:B,'{SRC}'!A{_H_BIG},DETAILS!H:H,$E$10)</f></c>",
         "G15 reads another sheet"),
        (SRC, "G24", f'<c r="G24"><f>SUM(G15:G{_H_BIG})</f></c>', "G24 formula .* outside the v1 layout"),
        (SRC, "G24", '<c r="G24"><f>SUM(G15:G12345678)</f></c>', "G24 formula .* outside the v1 layout"),
        (SRC, "G24", f'<c r="G24"><f>SUM(G{_H_BIG}:G23)</f></c>', "G24 formula .* outside the v1 layout"),
        (TREND, "E15", f"<c r=\"E15\"><f>VLOOKUP($A{_H_BIG},'{SRC}'!$A:$G,7,FALSE)</f></c>", "E15 is not a VLOOKUP"),
    ],
    ids=["sumifs-5000-digit-row", "sum-5000-digit-row", "sum-8-digit-row", "sum-5000-digit-first-row",
         "vlookup-5000-digit-row"],
)
def test_h_formula_row_past_excel_limits_is_a_targeted_refusal(sheet, ref, xml, why):
    data = _edit_sheet(_build(), sheet, _set_cell_xml(ref, xml))
    with pytest.raises(UnsupportedTrackerLayout, match=why) as exc:
        _finish(data)
    assert "could not be evaluated" not in str(exc.value)


@pytest.mark.parametrize("v", ["²", "٣", "1" * 5000], ids=["superscript", "arabic-indic", "5000-digits"])
def test_h_shared_string_index_that_is_not_an_ascii_index_is_a_refusal(v):
    data = _edit_sheet(_build(), SRC, _inject_rows(f'<row r="30"><c r="A30" t="s"><v>{v}</v></c></row>'))
    with pytest.raises(UnsupportedTrackerLayout, match="shared-string index"):
        _finish(data)


def test_h_non_utf8_sheet_part_is_a_refusal():
    data = _build()
    parts = _read(data)
    part = _part(data, SRC)
    parts[part] = parts[part].decode("utf-8").encode("utf-16")
    with pytest.raises(UnsupportedTrackerLayout, match="UTF-8"):
        _finish(_write(parts))


def _h_corrupt_lzma(data: bytes) -> bytes:
    """Re-zip every member with LZMA, then flip bytes inside the first member's LZMA stream."""
    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_LZMA) as z:
        for info in src.infolist():
            z.writestr(info.filename, src.read(info.filename) + b" " * 4000)
    b = bytearray(out.getvalue())
    name_len, extra_len = struct.unpack_from("<HH", b, 26)
    body = 30 + name_len + extra_len
    for k in range(body + 9, body + 40):
        b[k] ^= 0xFF
    return bytes(b)


def test_h_bad_lzma_member_is_a_refusal():
    """A corrupt LZMA stream raised lzma.LZMAError, which _Pkg did not list (a 500)."""
    with pytest.raises(UnsupportedTrackerLayout, match="not a readable"):
        dtabs._Pkg(_h_corrupt_lzma(_u786_pkg('<row r="1"><c r="A1"><v>1</v></c></row>')))


@pytest.mark.parametrize("ref", ["A1\n", "A1\r", "a1", "A01", "A١"])
def test_h_split_ref_takes_whole_ascii_a1_references_only(ref):
    with pytest.raises(dtabs._CellError):
        dtabs._split_ref(ref)


def test_h_next_rid_ignores_ids_too_long_to_be_ours_and_never_collides():
    assert dtabs._next_rid('<Relationship Id="rId' + "9" * 5000 + '"/>') == "rId1"
    rels = '<Relationship Id="rId999999999"/><Relationship Id="rId1000000000"/>'
    assert dtabs._next_rid(rels) == "rId1000000001"


_H_PLAN = DrawTabPlan(invoice_number=INVOICE, draw_no=3, previous_label="TST-02", source_tab=SRC, new_tab=NEW, trend_tab=TREND)

_H_ENTRY_POINTS = {
    "plan_next_draw": lambda b: dtabs.plan_next_draw(b, invoice_number=INVOICE),
    "refresh_cached_values": lambda b: dtabs.refresh_cached_values(b, [SRC]),
    "clone_draw_tab": lambda b: dtabs.clone_draw_tab(b, _H_PLAN, draw_date=DRAW_DATE),
    "extend_trend": lambda b: dtabs.extend_trend(b, _H_PLAN, draw_date=DRAW_DATE),
    "one_tab_render_package": lambda b: dtabs.one_tab_render_package(b, SRC),
    "finish_draw_tabs": lambda b: dtabs.finish_draw_tabs(b, invoice_number=INVOICE, draw_date=DRAW_DATE),
    "render_existing": lambda b: dtabs.render_existing(b, invoice_number=INVOICE),
}


@pytest.mark.parametrize("entry", sorted(_H_ENTRY_POINTS))
@pytest.mark.parametrize("error", [KeyError("k"), TypeError("t"), IndexError("i"), ArithmeticError("a"), MemoryError()],
                         ids=lambda e: type(e).__name__)
def test_h_unexpected_error_in_any_entry_point_is_a_logged_refusal(monkeypatch, caplog, entry, error):
    def boom(self, data):
        raise error

    monkeypatch.setattr(dtabs._Pkg, "__init__", boom)
    with caplog.at_level(_h_logging.ERROR, logger=dtabs.__name__):
        with pytest.raises(UnsupportedTrackerLayout) as exc:
            _H_ENTRY_POINTS[entry](b"PK")
    assert type(exc.value) is UnsupportedTrackerLayout
    assert type(error).__name__ in str(exc.value) and "nothing was written" in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    logged = [r for r in caplog.records if getattr(r, "event_name", "") == "box.excel.draw_tabs_unexpected_error"]
    assert len(logged) == 1 and logged[0].exc_info is not None and logged[0].error_class == type(error).__name__


def test_h_the_module_s_own_refusals_pass_through_the_boundary_with_their_type(finished, base, caplog):
    with caplog.at_level(_h_logging.ERROR, logger=dtabs.__name__):
        with pytest.raises(DrawAlreadyFinished) as exc:
            _finish(finished.workbook)
        assert type(exc.value) is DrawAlreadyFinished
        with pytest.raises(PreviousDrawMissing) as exc2:
            _finish(base, invoice="TST-09")
        assert type(exc2.value) is PreviousDrawMissing
    assert not [r for r in caplog.records if getattr(r, "event_name", "") == "box.excel.draw_tabs_unexpected_error"]


def test_h_every_public_function_is_behind_the_boundary():
    public = {
        name for name, fn in vars(dtabs).items()
        if _h_inspect.isfunction(fn) and not name.startswith("_") and fn.__module__ == dtabs.__name__
    }
    assert public - {"echo_text"} == set(_H_ENTRY_POINTS), public
    for name in _H_ENTRY_POINTS:
        assert getattr(getattr(dtabs, name), "__wrapped__", None) is not None, f"{name} is not behind _tracker_boundary"


def test_h_refusal_messages_never_carry_raw_control_characters():
    msg = str(UnsupportedTrackerLayout("tab 'X'\nInjected: line\x1b[31m end"))
    assert msg.isprintable(), repr(msg)
    assert "\\n" in msg and "\\x1b" in msg
    long = str(DrawTabsError("\n" * 2000))
    assert len(long) <= 600 and long.isprintable()


@pytest.mark.parametrize("expr, trusted", [
    ("echo_text(a)", True), ("_cref(c, r)", True), ("', '.join(echo_text(t) for t in xs)", True),
    ("echo_text(a) + raw", False), ("_cref(c, r) + raw", False), ("', '.join(t for t in xs)", False),
    ("', '.join(echo_text(t) for t in xs) + raw", False), ("raw", False), ("str(echo_text(a))", False),
])
def test_h_refusal_text_trust_is_decided_on_the_whole_expression(expr, trusted):
    import ast

    assert _u786_trusted(ast.parse(expr, mode="eval").body) is trusted
