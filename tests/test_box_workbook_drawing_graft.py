"""U-786: a DETAILS write must not delete the drawings on a Box tracker tab.

openpyxl silently drops DrawingML shapes (xdr:sp / grpSp / cxnSp) on save and
keeps pictures and charts only by re-writing them. These tests build a tracker
whose "Draw Request 08" tab carries a text-box shape ("INVOICE") and a picture,
run the PUBLIC DETAILS writers, and assert the original drawing comes back
byte-for-byte. No binary fixtures are committed: everything is built in-test.
"""

import base64
import logging
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.table import Table, TableStyleInfo

from integrations.box.excel.business import workbook_editor as we
from integrations.box.excel.business.workbook_editor import (
    DRAW_REQUEST_COL_INDEX,
    apply_rows_to_details,
    stamp_columns_by_key,
)

SHEET = "Tracking Budget DETAILS"
DRAW = "Draw Request 08"
PLAIN = "Plain"
NEW_KEY = "41C564EE-F027-4EF8-9FA6-70DE9268A8F5"
EXISTING_KEY = "KEY-EXISTING"

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
DRAWING_CT = "application/vnd.openxmlformats-officedocument.drawing+xml"

# A 1x1 PNG (valid image, small enough to inline).
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _drawing_xml(*, picture: bool) -> bytes:
    pic = ""
    if picture:
        pic = (
            '<xdr:twoCellAnchor>'
            '<xdr:from><xdr:col>6</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>0</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
            '<xdr:to><xdr:col>7</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
            '<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="3" name="Picture 2"/>'
            '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
            '<xdr:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
            '<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="200000" cy="200000"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
            '</xdr:pic><xdr:clientData/></xdr:twoCellAnchor>'
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        f'xmlns:r="{NS_R}">'
        '<xdr:twoCellAnchor>'
        '<xdr:from><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>0</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
        '<xdr:to><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>2</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
        '<xdr:sp macro="" textlink="">'
        '<xdr:nvSpPr><xdr:cNvPr id="2" name="TextBox 1"/><xdr:cNvSpPr txBox="1"/></xdr:nvSpPr>'
        '<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1000000" cy="400000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
        '<xdr:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US"/>'
        '<a:t>INVOICE</a:t></a:r></a:p></xdr:txBody>'
        '</xdr:sp><xdr:clientData/></xdr:twoCellAnchor>'
        f'{pic}'
        '</xdr:wsDr>'
    )
    return xml.encode("utf-8")


DRAWING_WITH_PICTURE = _drawing_xml(picture=True)
DRAWING_SHAPE_ONLY = _drawing_xml(picture=False)


# ---------------------------------------------------------------- fixtures

def _build_tracker(*, with_table: bool = False) -> bytes:
    """DETAILS laid out like test_box_details_row_alignment._tracker_bytes, plus
    a 'Draw Request 08' tab and a 'Plain' tab with no drawing."""
    wb = Workbook()
    det = wb.active
    det.title = SHEET
    headers = {
        2: "Cost Code", 3: "Cost Sub Code", 4: "CODE", 5: "CATEGORY",
        6: "BUDGET AMOUNT", 8: "DRAW REQUEST DATE", 9: "DATE",
        10: "PAYABLE TO", 11: "INVOICE #", 12: "DESCRIPTION", 13: "Ck",
        14: "AMOUNT BILLABLE", 15: "AMOUNT NOT BILLABLE",
    }
    for col, text in headers.items():
        det.cell(row=2, column=col, value=text)
    existing = {2: "35", 3: "35.01", 10: "Prior Vendor", 12: "Prior Line",
                13: "Bill", 14: 100.0, 26: EXISTING_KEY}
    for col, value in existing.items():
        det.cell(row=3, column=col, value=value)
    det.cell(row=4, column=5, value="SUBTOTAL")

    draw = wb.create_sheet(DRAW)
    if with_table:
        draw["A1"] = "h1"
        draw["B1"] = "h2"
        draw["A2"] = 1
        draw["B2"] = 2
        table = Table(displayName="TblDraw08", ref="A1:B2")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=True)
        draw.add_table(table)

    wb.create_sheet(PLAIN)["A1"] = "plain"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _valid_row() -> list:
    row = [None] * 26
    row[1] = "35"
    row[2] = "35.01"
    row[8] = "2026-02-28"
    row[9] = "Michael Jacobson"
    row[10] = "2026.02.28.EVR"
    row[11] = "Drywall Labor"
    row[12] = "Bill"
    row[13] = 56.25
    row[25] = NEW_KEY
    return row


def _graft_fixture(src: bytes, sheet_part: str, *, drawing_xml: bytes, png) -> bytes:
    """Post-process an openpyxl-built package so `sheet_part` carries drawing 1 (with its rels,
    optional PNG, content type and <drawing r:id/> element)."""
    members = _members(src)
    assert "xl/drawings/drawing1.xml" not in members
    _attach_drawing(members, sheet_part, 1, drawing_xml, media="image1.png" if png is not None else None, png=png)
    return _pack(members)


def _tracker_with_shape_and_picture() -> bytes:
    src = _build_tracker()
    return _graft_fixture(
        src, _sheet_part(src, DRAW), drawing_xml=DRAWING_WITH_PICTURE, png=PNG
    )


def _tracker_with_shape_only_and_table() -> bytes:
    src = _build_tracker(with_table=True)
    return _graft_fixture(
        src, _sheet_part(src, DRAW), drawing_xml=DRAWING_SHAPE_ONLY, png=None
    )


# ---------------------------------------------------------------- package helpers

def _zip(pkg: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(pkg))


def _resolve(base: str, target: str) -> str:
    if target.startswith("/"):
        return target[1:]
    return posixpath.normpath(posixpath.join(base, target))


def _rels(z: zipfile.ZipFile, relpath: str):
    root = ET.fromstring(z.read(relpath))
    return [
        (r.get("Id"), r.get("Type", ""), r.get("Target"), r.get("TargetMode"))
        for r in root.iter(f"{{{NS_PKG_REL}}}Relationship")
    ]


def _sheet_part(pkg: bytes, name: str) -> str:
    z = _zip(pkg)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rid = None
    for sheet in wb.iter(f"{{{NS_MAIN}}}sheet"):
        if sheet.get("name") == name:
            rid = sheet.get(f"{{{NS_R}}}id")
    assert rid is not None, f"no sheet named {name!r}"
    targets = {i: t for i, _, t, _ in _rels(z, "xl/_rels/workbook.xml.rels")}
    return _resolve("xl", targets[rid])


def _drawing_part(pkg: bytes, sheet_part: str):
    z = _zip(pkg)
    d, b = posixpath.split(sheet_part)
    srels = f"{d}/_rels/{b}.rels"
    if srels not in z.namelist():
        return None
    for _, rtype, target, _ in _rels(z, srels):
        if rtype.endswith("/drawing"):
            return _resolve(d, target)
    return None


def _member(pkg: bytes, name: str) -> bytes:
    return _zip(pkg).read(name)


def _image_bytes(pkg: bytes, drawing_part: str) -> bytes:
    z = _zip(pkg)
    d, b = posixpath.split(drawing_part)
    rels = f"{d}/_rels/{b}.rels"
    for _, rtype, target, _ in _rels(z, rels):
        if rtype.endswith("/image"):
            return z.read(_resolve(d, target))
    raise AssertionError(f"{drawing_part} has no image relationship")


def _assert_package_integrity(pkg: bytes) -> None:
    load_workbook(BytesIO(pkg))  # must re-load cleanly
    z = _zip(pkg)
    names = set(z.namelist())
    referenced = set()
    for rels_name in [n for n in names if n.endswith(".rels")]:
        parts = rels_name.split("/")
        base = "/".join(parts[:-2]) if parts[-2:-1] == ["_rels"] else ""
        for _, rtype, target, mode in _rels(z, rels_name):
            if mode == "External" or target.startswith("#") or rtype.endswith("/hyperlink"):
                continue  # hyperlinks are locations or URLs, not package parts (G1)
            resolved = _resolve(base, target)
            assert resolved in names, f"{rels_name}: {target} -> {resolved} is missing"
            referenced.add(resolved)

    ct = ET.fromstring(z.read("[Content_Types].xml"))
    defaults = {d.get("Extension").lower() for d in ct.iter(f"{{{NS_CT}}}Default")}
    overrides = {o.get("PartName") for o in ct.iter(f"{{{NS_CT}}}Override")}
    for n in names:
        if n == "[Content_Types].xml" or n.endswith(".rels"):
            continue
        ext = n.rsplit(".", 1)[-1].lower()
        assert f"/{n}" in overrides or ext in defaults, f"no content type for {n}"

    for n in names:
        if n.startswith(("xl/drawings/", "xl/media/", "xl/charts/")) and not n.endswith(".rels"):
            assert n in referenced, f"orphaned part {n} (no rels references it)"


def _all_drawing_xml(pkg: bytes) -> bytes:
    z = _zip(pkg)
    return b"".join(
        z.read(n) for n in z.namelist()
        if n.startswith("xl/drawings/") and n.endswith(".xml")
    )


# ---------------------------------------------------------------- tests

def test_bill_write_keeps_draw_tab_shape_and_picture_byte_for_byte():
    src = _tracker_with_shape_and_picture()
    result = apply_rows_to_details(src, SHEET, [_valid_row()])
    out = result["bytes"]
    assert out is not None and result["applied"] == 1

    dpart = _drawing_part(out, _sheet_part(out, DRAW))
    assert dpart is not None, "Draw Request 08 lost its drawing"
    assert _member(out, dpart) == DRAWING_WITH_PICTURE
    assert b"INVOICE" in _member(out, dpart)
    assert _image_bytes(out, dpart) == PNG
    # T1: the restored parts carry the ORIGINAL effective content types, not
    # merely a type that exists in the package.
    src_dpart = _drawing_part(src, _sheet_part(src, DRAW))
    assert _effective_ct(out, dpart) == _effective_ct(src, src_dpart) == DRAWING_CT
    assert _effective_ct(out, _image_part(out, dpart)) == "image/png"

    ws = load_workbook(BytesIO(out))[SHEET]
    assert any(ws.cell(row=r, column=26).value == NEW_KEY for r in range(1, ws.max_row + 1))


def test_stamp_keeps_draw_tab_shape_and_picture_byte_for_byte():
    src = _tracker_with_shape_and_picture()
    result = stamp_columns_by_key(
        src, SHEET, [(EXISTING_KEY, {DRAW_REQUEST_COL_INDEX: "2026-09-18"})]
    )
    out = result["bytes"]
    assert out is not None and result["applied"] == 1

    dpart = _drawing_part(out, _sheet_part(out, DRAW))
    assert dpart is not None, "Draw Request 08 lost its drawing"
    assert _member(out, dpart) == DRAWING_WITH_PICTURE
    assert b"INVOICE" in _member(out, dpart)
    assert _image_bytes(out, dpart) == PNG

    ws = load_workbook(BytesIO(out))[SHEET]
    assert ws.cell(row=3, column=DRAW_REQUEST_COL_INDEX + 1).value == "2026-09-18"


def test_control_openpyxl_alone_drops_the_invoice_shape(monkeypatch):
    # With the graft neutralised, the shape must be gone: this proves the
    # fixture discriminates and that openpyxl really is the thing dropping it.
    monkeypatch.setattr(we, "regraft_drawings", lambda original, edited: edited, raising=False)
    src = _tracker_with_shape_and_picture()
    out = apply_rows_to_details(src, SHEET, [_valid_row()])["bytes"]
    assert b"INVOICE" not in _all_drawing_xml(out)


def test_only_the_draw_tab_carries_a_drawing_element():
    src = _tracker_with_shape_and_picture()
    out = apply_rows_to_details(src, SHEET, [_valid_row()])["bytes"]
    expected = {SHEET: 0, DRAW: 1, PLAIN: 0}
    for name, count in expected.items():
        xml = _member(out, _sheet_part(out, name)).decode("utf-8")
        assert len(re.findall(r"<drawing[\s/>]", xml)) == count, name
    assert _drawing_part(out, _sheet_part(out, PLAIN)) is None


def test_output_package_is_internally_consistent():
    src = _tracker_with_shape_and_picture()
    out = apply_rows_to_details(src, SHEET, [_valid_row()])["bytes"]
    _assert_package_integrity(out)


def test_package_without_drawings_comes_back_byte_identical():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    original = _build_tracker()
    edited = apply_rows_to_details(original, SHEET, [_valid_row()])["bytes"]
    assert regraft_drawings(original, edited) == edited


def test_unreadable_original_fails_soft_and_logs(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    edited = apply_rows_to_details(_build_tracker(), SHEET, [_valid_row()])["bytes"]
    with caplog.at_level(logging.WARNING):
        result = regraft_drawings(b"not a zip", edited)
    assert result == edited
    matches = [r for r in caplog.records if r.getMessage() == "box.excel.drawing_regraft_failed"]
    assert matches, "expected box.excel.drawing_regraft_failed to be logged"
    assert getattr(matches[0], "error_class", None) == "BadZipFile"


def test_shape_only_drawing_restored_before_table_parts():
    src = _tracker_with_shape_only_and_table()
    out = apply_rows_to_details(src, SHEET, [_valid_row()])["bytes"]

    sheet = _sheet_part(out, DRAW)
    xml = _member(out, sheet).decode("utf-8")
    assert "<tableParts" in xml, "fixture must carry a table so ordering is tested"
    assert len(re.findall(r"<drawing[\s/>]", xml)) == 1
    assert xml.index("<drawing") < xml.index("<tableParts")

    dpart = _drawing_part(out, sheet)
    assert dpart is not None
    assert _member(out, dpart) == DRAWING_SHAPE_ONLY
    assert b"INVOICE" in _member(out, dpart)
    _assert_package_integrity(out)


# ---------------------------------------------------------------- U-786 fix round 1
# One regression per review item (R1-R13) plus the test-gap closures (T1-T5).
# Each builds the exact defect in-test; every assertion states the CORRECT result.

GRAFT_LOGGER = "integrations.box.excel.business.drawing_graft"
DRAW2 = "Draw Request 09"
DRAW_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing"
IMG_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
WS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
HL_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
OLE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"


def _png_b() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (2, 2), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _members(pkg: bytes) -> dict:
    z = _zip(pkg)
    return {i.filename: z.read(i.filename) for i in z.infolist()}


def _pack(members: dict) -> bytes:
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in members.items():
            zout.writestr(name, data)
    return out.getvalue()


def _edit(pkg: bytes, fn) -> bytes:
    members = _members(pkg)
    fn(members)
    return _pack(members)


def _resave(pkg: bytes) -> bytes:
    """A plain openpyxl round trip: the 'edited' package before any graft."""
    out = BytesIO()
    load_workbook(BytesIO(pkg)).save(out)
    return out.getvalue()


def _add_rel(members: dict, rels_name: str, rel_xml: str) -> None:
    if rels_name in members:
        text = members[rels_name].decode("utf-8").replace("</Relationships>", rel_xml + "</Relationships>")
    else:
        text = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{NS_PKG_REL}">{rel_xml}</Relationships>'
        )
    members[rels_name] = text.encode("utf-8")


def _add_override(members: dict, part: str, ct: str) -> None:
    text = members["[Content_Types].xml"].decode("utf-8")
    text = text.replace("</Types>", f'<Override PartName="/{part}" ContentType="{ct}"/></Types>')
    members["[Content_Types].xml"] = text.encode("utf-8")


def _attach_drawing(members, sheet_part, n, drawing_xml, *, media=None, png=None, override=True):
    """Give `sheet_part` drawing number n (same shape as _graft_fixture)."""
    d, b = posixpath.split(sheet_part)
    rid = f"rIdDrw{n}"
    _add_rel(members, f"{d}/_rels/{b}.rels",
             f'<Relationship Id="{rid}" Type="{DRAW_REL}" Target="../drawings/drawing{n}.xml"/>')
    sheet = members[sheet_part].decode("utf-8")
    m = re.search(r"<tableParts|<extLst|</worksheet>", sheet)
    el = f'<drawing xmlns:r="{NS_R}" r:id="{rid}"/>'
    members[sheet_part] = (sheet[: m.start()] + el + sheet[m.start():]).encode("utf-8")
    members[f"xl/drawings/drawing{n}.xml"] = drawing_xml
    if media is not None:
        members[f"xl/media/{media}"] = png
        _add_rel(members, f"xl/drawings/_rels/drawing{n}.xml.rels",
                 f'<Relationship Id="rId1" Type="{IMG_REL}" Target="../media/{media}"/>')
        ct = members["[Content_Types].xml"].decode("utf-8")
        if 'Extension="png"' not in ct:
            ct = ct.replace("</Types>", '<Default Extension="png" ContentType="image/png"/></Types>')
            members["[Content_Types].xml"] = ct.encode("utf-8")
    if override:
        _add_override(members, f"xl/drawings/drawing{n}.xml", DRAWING_CT)


def _build_two_draw_tracker() -> bytes:
    wb = Workbook()
    wb.active.title = SHEET
    wb.create_sheet(DRAW)["A1"] = "x"
    wb.create_sheet(DRAW2)["A1"] = "y"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _effective_ct(pkg: bytes, part: str):
    types = ET.fromstring(_zip(pkg).read("[Content_Types].xml"))
    for o in types.iter(f"{{{NS_CT}}}Override"):
        if o.get("PartName", "").lstrip("/").lower() == part.lower():
            return o.get("ContentType")
    ext = part.rsplit(".", 1)[-1].lower()
    for d in types.iter(f"{{{NS_CT}}}Default"):
        if d.get("Extension", "").lower() == ext:
            return d.get("ContentType")
    return None


def _image_part(pkg: bytes, drawing_part: str) -> str:
    d, b = posixpath.split(drawing_part)
    for _, rtype, target, _ in _rels(_zip(pkg), f"{d}/_rels/{b}.rels"):
        if rtype.endswith("/image"):
            return _resolve(d, target)
    raise AssertionError(f"{drawing_part} has no image relationship")


def _prefix_workbook(pkg: bytes) -> bytes:
    """Rewrite workbook.xml so the main namespace is prefixed (x:sheets/x:sheet)."""
    def fn(m):
        x = m["xl/workbook.xml"].decode("utf-8")
        x = x.replace("<workbook ", f'<x:workbook xmlns:x="{NS_MAIN}" ', 1)
        x = x.replace("</workbook>", "</x:workbook>")
        x = re.sub(r"<(/?)sheets\b", r"<\1x:sheets", x)
        x = re.sub(r"<(/?)sheet\b", r"<\1x:sheet", x)
        m["xl/workbook.xml"] = x.encode("utf-8")

    return _edit(pkg, fn)


def _cts_partnames(pkg: bytes) -> list:
    types = ET.fromstring(_zip(pkg).read("[Content_Types].xml"))
    return [o.get("PartName", "") for o in types.iter(f"{{{NS_CT}}}Override")]


# ---- R1: escape every attribute value written into [Content_Types].xml

def test_r1_ampersand_part_name_yields_well_formed_content_types():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()

    def rename(m):
        m["xl/media/logo&co.png"] = m.pop("xl/media/image1.png")
        rels = "xl/drawings/_rels/drawing1.xml.rels"
        m[rels] = m[rels].replace(b"../media/image1.png", b"../media/logo&amp;co.png")

    src1 = _edit(src, rename)
    out = regraft_drawings(src1, _resave(src1))
    ET.fromstring(_zip(out).read("[Content_Types].xml"))  # raises if malformed
    _assert_package_integrity(out)
    dpart = _drawing_part(out, _sheet_part(out, DRAW))
    assert dpart is not None and _member(out, dpart) == DRAWING_WITH_PICTURE


# ---- R2: validation backstop returns `edited` unchanged and logs invalid

def test_r2_invalid_output_returns_edited_and_logs_invalid(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()
    bad = _edit(src, lambda m: _add_rel(
        m, "xl/drawings/_rels/drawing1.xml.rels",
        f'<Relationship Id="rIdBad" Type="{OLE_REL}" Target="../embeddings/missing.bin"/>'))
    plain = _resave(bad)
    with caplog.at_level(logging.WARNING, logger=GRAFT_LOGGER):
        result = regraft_drawings(bad, plain)
    assert result == plain
    events = [r for r in caplog.records if r.getMessage() == "box.excel.drawing_regraft_invalid"]
    assert events, "expected box.excel.drawing_regraft_invalid"
    assert getattr(events[0], "event_name", None) == "box.excel.drawing_regraft_invalid"
    assert getattr(events[0], "reason", "")


# ---- R3: minted names do not grow across chained writes

def test_r3_regraft_prefix_does_not_grow_across_chained_writes():
    out = _tracker_with_shape_and_picture()
    lengths = []
    for i in range(5):
        row = _valid_row()
        row[25] = f"KEY-CHAIN-{i}"
        out = apply_rows_to_details(out, SHEET, [row])["bytes"]
        assert out is not None
        lengths.append(max(len(n) for n in _zip(out).namelist()))
    assert max(lengths[1:]) <= lengths[0], lengths


# ---- R4: Override lookup is case-insensitive; .xml needs an Override

def test_r4_override_lookup_is_case_insensitive():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()

    def upper(m):
        ct = m["[Content_Types].xml"].decode("utf-8")
        assert 'PartName="/xl/drawings/drawing1.xml"' in ct
        m["[Content_Types].xml"] = ct.replace(
            'PartName="/xl/drawings/drawing1.xml"', 'PartName="/XL/DRAWINGS/DRAWING1.XML"').encode("utf-8")

    src1 = _edit(src, upper)
    out = regraft_drawings(src1, _resave(src1))
    dpart = _drawing_part(out, _sheet_part(out, DRAW))
    assert dpart is not None and _member(out, dpart) == DRAWING_WITH_PICTURE
    assert _effective_ct(out, dpart) == DRAWING_CT


def test_r4_xml_part_without_override_is_not_grafted_with_generic_default(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()

    def drop(m):
        ct = m["[Content_Types].xml"].decode("utf-8")
        needle = f'<Override PartName="/xl/drawings/drawing1.xml" ContentType="{DRAWING_CT}"/>'
        assert needle in ct
        m["[Content_Types].xml"] = ct.replace(needle, "").encode("utf-8")

    src1 = _edit(src, drop)
    plain = _resave(src1)
    with caplog.at_level(logging.WARNING, logger=GRAFT_LOGGER):
        out = regraft_drawings(src1, plain)
    assert not [n for n in _zip(out).namelist() if "regraft" in n]
    skipped = [r for r in caplog.records if r.getMessage() == "box.excel.drawing_regraft_sheet_skipped"]
    assert skipped and getattr(skipped[0], "sheet", None) == DRAW


# ---- R5: absolute targets are normalised

def test_r5_absolute_target_is_normalised():
    from integrations.box.excel.business.drawing_graft import _resolve as graft_resolve

    assert graft_resolve("xl/drawings", "/xl/drawings/../media/a.png") == "xl/media/a.png"
    assert graft_resolve("xl/drawings", "../media/a.png") == "xl/media/a.png"


# ---- R6: rels round-trip '>' in attributes and character references

def test_r6_rels_keep_gt_and_tab_characters_in_targets():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()
    src2 = _edit(src, lambda m: _add_rel(
        m, "xl/drawings/_rels/drawing1.xml.rels",
        f'<Relationship Id="rIdOdd" Type="{HL_REL}" Target="https://e.example/?q=a>b&#9;c" TargetMode="External"/>'))
    out = regraft_drawings(src2, _resave(src2))
    dpart = _drawing_part(out, _sheet_part(out, DRAW))
    d, b = posixpath.split(dpart)
    targets = {rid: t for rid, _, t, _ in _rels(_zip(out), f"{d}/_rels/{b}.rels")}
    assert targets.get("rIdOdd") == "https://e.example/?q=a>b\tc"


# ---- R7: <drawing> goes before a DIRECT follow-set child, never inside cfRule

def test_r7_drawing_not_inserted_inside_nested_extlst():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_only_and_table()
    plain = _resave(src)
    cf = ('<conditionalFormatting sqref="A1"><cfRule type="expression" priority="1">'
          '<formula>TRUE</formula><extLst><ext uri="{B025F937-C7B1-47D3-B67F-A62EFF666E3E}"/>'
          '</extLst></cfRule></conditionalFormatting>')
    sheet = _sheet_part(plain, DRAW)

    def inject(m):
        x = m[sheet].decode("utf-8")
        m[sheet] = x.replace("<tableParts", cf + "<tableParts", 1).encode("utf-8")

    edited = _edit(plain, inject)
    out = regraft_drawings(src, edited)
    root = ET.fromstring(_member(out, _sheet_part(out, DRAW)))
    assert len(root.findall(f"{{{NS_MAIN}}}drawing")) == 1


# ---- R8: the drawing named by the sheet's own <drawing r:id>, not the first rel

def test_r8_sheet_restores_the_drawing_its_element_names_not_the_first_rel():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()
    sheet = _sheet_part(src, DRAW)
    d, b = posixpath.split(sheet)
    rels_name = f"{d}/_rels/{b}.rels"

    def decoy(m):
        m[rels_name] = m[rels_name].replace(
            b'<Relationship Id="rIdDrw1"',
            b'<Relationship Id="rIdDecoy" Type="' + DRAW_REL.encode() +
            b'" Target="../drawings/decoy.xml"/><Relationship Id="rIdDrw1"')
        m["xl/drawings/decoy.xml"] = DRAWING_SHAPE_ONLY
        _add_override(m, "xl/drawings/decoy.xml", DRAWING_CT)

    src2 = _edit(src, decoy)
    out = regraft_drawings(src2, _resave(src2))
    dpart = _drawing_part(out, _sheet_part(out, DRAW))
    assert dpart is not None
    assert _member(out, dpart) == DRAWING_WITH_PICTURE


# ---- R9: one bad sheet is skipped; the others still graft

def test_r9_bad_sheet_does_not_cancel_good_sheet(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _build_two_draw_tracker()

    def fill(m):
        _attach_drawing(m, _sheet_part(src, DRAW), 1, DRAWING_WITH_PICTURE, media="image1.png", png=PNG)
        _attach_drawing(m, _sheet_part(src, DRAW2), 2, DRAWING_SHAPE_ONLY, override=False)

    src2 = _edit(src, fill)
    plain = _resave(src2)
    with caplog.at_level(logging.WARNING, logger=GRAFT_LOGGER):
        out = regraft_drawings(src2, plain)
    good = _drawing_part(out, _sheet_part(out, DRAW))
    assert good is not None and _member(out, good) == DRAWING_WITH_PICTURE
    bad = _drawing_part(out, _sheet_part(out, DRAW2))
    assert bad is None or "regraft" not in bad
    skipped = [r for r in caplog.records if r.getMessage() == "box.excel.drawing_regraft_sheet_skipped"]
    assert skipped and getattr(skipped[0], "sheet", None) == DRAW2
    assert getattr(skipped[0], "reason", "")


# ---- R10: worksheet / table / comments relationships are never copied

def test_r10_worksheet_rel_is_not_copied_as_orphan():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()
    src2 = _edit(src, lambda m: _add_rel(
        m, "xl/drawings/_rels/drawing1.xml.rels",
        f'<Relationship Id="rIdWs" Type="{WS_REL}" Target="../worksheets/sheet3.xml"/>'))
    out = regraft_drawings(src2, _resave(src2))
    assert not [n for n in _zip(out).namelist() if n.startswith("xl/worksheets/regraft")]


# ---- R11: drawings openpyxl cannot reproduce are skipped

def test_r11_unsupported_drawing_is_skipped(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()

    def add_control(m):
        x = m["xl/drawings/drawing1.xml"]
        m["xl/drawings/drawing1.xml"] = x.replace(b"</xdr:wsDr>", b'<compatExt spid="_x0000_s1025"/></xdr:wsDr>')

    src2 = _edit(src, add_control)
    with caplog.at_level(logging.WARNING, logger=GRAFT_LOGGER):
        out = regraft_drawings(src2, _resave(src2))
    assert not [n for n in _zip(out).namelist() if "regraft" in n]
    skipped = [r for r in caplog.records if r.getMessage() == "box.excel.drawing_regraft_sheet_skipped"]
    assert skipped and "compatExt" in getattr(skipped[0], "reason", "")


# ---- R12: logging contract

def test_r12_success_logs_applied_info_with_counts(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()
    with caplog.at_level(logging.INFO, logger=GRAFT_LOGGER):
        regraft_drawings(src, _resave(src))
    applied = [r for r in caplog.records if r.getMessage() == "box.excel.drawing_regraft_applied"]
    assert applied, "expected box.excel.drawing_regraft_applied"
    assert applied[0].restored == 1 and applied[0].skipped == 0


def test_r12_failure_warning_carries_exc_info_and_reason(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    edited = _resave(_build_tracker())
    with caplog.at_level(logging.WARNING, logger=GRAFT_LOGGER):
        regraft_drawings(b"not a zip", edited)
    failed = [r for r in caplog.records if r.getMessage() == "box.excel.drawing_regraft_failed"]
    assert failed
    assert failed[0].exc_info is not None
    assert isinstance(getattr(failed[0], "reason", None), str) and failed[0].reason


def test_r12_nothing_logged_when_nothing_to_do(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    original = _build_tracker()
    with caplog.at_level(logging.INFO, logger=GRAFT_LOGGER):
        regraft_drawings(original, _resave(original))
    assert not [r for r in caplog.records if r.getMessage().startswith("box.excel.drawing_regraft")]


# ---- R13: prefixed main namespace in workbook.xml

def test_r13_prefixed_workbook_namespace_is_grafted():
    src = _prefix_workbook(_tracker_with_shape_and_picture())
    out = apply_rows_to_details(src, SHEET, [_valid_row()])["bytes"]
    dpart = _drawing_part(out, _sheet_part(out, DRAW))
    assert dpart is not None
    assert _member(out, dpart) == DRAWING_WITH_PICTURE


# ---- T2: two tabs with different, openpyxl-unreproducible images

def test_t2_two_tabs_restore_their_own_images():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _build_two_draw_tracker()
    png_b = _png_b()

    def fill(m):
        _attach_drawing(m, _sheet_part(src, DRAW), 1, DRAWING_WITH_PICTURE,
                        media="logo_rogers.png", png=PNG)
        _attach_drawing(m, _sheet_part(src, DRAW2), 2, DRAWING_WITH_PICTURE,
                        media="stamp_b.png", png=png_b)

    src2 = _edit(src, fill)
    out = regraft_drawings(src2, _resave(src2))
    for tab, want in ((DRAW, PNG), (DRAW2, png_b)):
        dpart = _drawing_part(out, _sheet_part(out, tab))
        assert dpart is not None, tab
        assert _image_bytes(out, dpart) == want, tab


# ---- T3: shape-only rel Id is unique within the sheet's rels (table holds rId1)

def test_t3_shape_only_rel_id_unique_with_table():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_only_and_table()
    out = regraft_drawings(src, _resave(src))
    sheet = _sheet_part(out, DRAW)
    d, b = posixpath.split(sheet)
    ids = [rid for rid, _, _, _ in _rels(_zip(out), f"{d}/_rels/{b}.rels")]
    assert len(ids) == len(set(ids)), ids
    assert _drawing_part(out, sheet) is not None


# ---- T4: no Override remains for a pruned part

def test_t4_no_override_remains_for_pruned_part():
    src = _tracker_with_shape_and_picture()
    out = apply_rows_to_details(src, SHEET, [_valid_row()])["bytes"]
    names = set(_zip(out).namelist())
    for pn in _cts_partnames(out):
        assert pn.lstrip("/") in names, pn


# ---- T5: no drawings in original returns the very same edited object

def test_t5_no_drawings_returns_same_object():
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    original = _build_tracker()
    plain = _resave(original)
    assert regraft_drawings(original, plain) is plain


# ---- G1: hyperlink relationships are not package parts

_HLINK_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"


def test_g1_drawing_with_hyperlink_rels_is_restored_and_not_invalid(caplog):
    from integrations.box.excel.business.drawing_graft import regraft_drawings

    src = _tracker_with_shape_and_picture()

    def add_hyperlinks(m):
        _add_rel(m, "xl/drawings/_rels/drawing1.xml.rels",
                 f'<Relationship Id="rIdH1" Type="{_HLINK_REL}" Target="#\'DETAILS\'!A1"/>')
        _add_rel(m, "xl/drawings/_rels/drawing1.xml.rels",
                 f'<Relationship Id="rIdH2" Type="{_HLINK_REL}" Target="https://example.com/x" TargetMode="External"/>')

    src2 = _edit(src, add_hyperlinks)
    with caplog.at_level(logging.INFO, logger=GRAFT_LOGGER):
        out = regraft_drawings(src2, _resave(src2))

    orig_dp = _drawing_part(src2, _sheet_part(src2, DRAW))
    new_dp = _drawing_part(out, _sheet_part(out, DRAW))
    assert new_dp != orig_dp
    assert _member(out, new_dp) == _member(src2, orig_dp)
    d, b = posixpath.split(new_dp)
    rels = _rels(_zip(out), f"{d}/_rels/{b}.rels")
    assert ("rIdH1", _HLINK_REL, "#'DETAILS'!A1", None) in rels
    assert ("rIdH2", _HLINK_REL, "https://example.com/x", "External") in rels
    msgs = [r.getMessage() for r in caplog.records]
    assert "box.excel.drawing_regraft_applied" in msgs
    assert "box.excel.drawing_regraft_invalid" not in msgs
    _assert_package_integrity(out)


# ------------------------------------------- U-786 security fix round (S2: graft cost and size caps)

import time as _u786_time  # noqa: E402

from integrations.box.excel.business import drawing_graft as _u786_dg  # noqa: E402

_U786_CT_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
)
_U786_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_U786_ROOT = (
    f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_REL_NS}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
)
_U786_WB = (
    '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
    '<sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>'
)
_U786_WB_RELS = (
    f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_REL_NS}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
)


def _u786_sheet(drawing: bool) -> str:
    d = '<drawing xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId1"/>' if drawing else ""
    return ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData/>{d}</worksheet>")


_U786_SHEET_RELS = (
    f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_REL_NS}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/></Relationships>'
)


def _u786_zip(members) -> bytes:
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members:
            z.writestr(name, body)
    return out.getvalue()


def test_u786_s2_four_thousand_reachable_parts_graft_in_under_two_seconds():
    n = 4000
    extra = "".join(f'<Override PartName="/xl/drawings/p{i}.xml" ContentType="application/x-p"/>' for i in range(n))
    drels = (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_REL_NS}">'
             + "".join(f'<Relationship Id="rId{i}" Type="urn:x/other" Target="p{i}.xml"/>' for i in range(n))
             + "</Relationships>")
    members = [("[Content_Types].xml", _U786_CT_HEAD + extra + "</Types>"), ("_rels/.rels", _U786_ROOT),
               ("xl/workbook.xml", _U786_WB), ("xl/_rels/workbook.xml.rels", _U786_WB_RELS),
               ("xl/worksheets/sheet1.xml", _u786_sheet(True)), ("xl/worksheets/_rels/sheet1.xml.rels", _U786_SHEET_RELS),
               ("xl/drawings/drawing1.xml", "<x/>"), ("xl/drawings/_rels/drawing1.xml.rels", drels)]
    members += [(f"xl/drawings/p{i}.xml", "<p/>") for i in range(n)]
    orig = _u786_zip(members)
    edited = _u786_zip([("[Content_Types].xml", _U786_CT_HEAD + "</Types>"), ("_rels/.rels", _U786_ROOT),
                        ("xl/workbook.xml", _U786_WB), ("xl/_rels/workbook.xml.rels", _U786_WB_RELS),
                        ("xl/worksheets/sheet1.xml", _u786_sheet(False))])
    t0 = _u786_time.monotonic()
    out = _u786_dg.regraft_drawings(orig, edited)
    assert _u786_time.monotonic() - t0 < 2.0
    assert out is edited  # over the 500-part closure cap: this sheet is skipped, nothing is grafted


def test_u786_s2_unreferenced_huge_member_is_never_inflated_without_a_drawing(monkeypatch):
    bomb = ("customXml/unreferenced.bin", b"\0" * (100 * 1024 * 1024))
    orig = _u786_zip([("[Content_Types].xml", _U786_CT_HEAD.replace('<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>', "") + "</Types>"),
                      ("_rels/.rels", _U786_ROOT), ("xl/workbook.xml", _U786_WB), ("xl/_rels/workbook.xml.rels", _U786_WB_RELS),
                      ("xl/worksheets/sheet1.xml", _u786_sheet(False)), bomb])
    edited = _u786_zip([("[Content_Types].xml", _U786_CT_HEAD + "</Types>"), ("_rels/.rels", _U786_ROOT),
                        ("xl/workbook.xml", _U786_WB), ("xl/_rels/workbook.xml.rels", _U786_WB_RELS),
                        ("xl/worksheets/sheet1.xml", _u786_sheet(False))])
    reads = []
    real_read = zipfile.ZipFile.read

    def spy(self, name, *a, **k):
        reads.append(name)
        return real_read(self, name, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "read", spy)
    assert _u786_dg.regraft_drawings(orig, edited) is edited
    assert "customXml/unreferenced.bin" not in reads


def test_u786_s2_oversize_referenced_drawing_fails_soft_with_the_failed_event(caplog):
    big = ("xl/drawings/drawing1.xml", b"\0" * (65 * 1024 * 1024))
    orig = _u786_zip([("[Content_Types].xml", _U786_CT_HEAD + "</Types>"), ("_rels/.rels", _U786_ROOT),
                      ("xl/workbook.xml", _U786_WB), ("xl/_rels/workbook.xml.rels", _U786_WB_RELS),
                      ("xl/worksheets/sheet1.xml", _u786_sheet(True)),
                      ("xl/worksheets/_rels/sheet1.xml.rels", _U786_SHEET_RELS), big])
    edited = _u786_zip([("[Content_Types].xml", _U786_CT_HEAD + "</Types>"), ("_rels/.rels", _U786_ROOT),
                        ("xl/workbook.xml", _U786_WB), ("xl/_rels/workbook.xml.rels", _U786_WB_RELS),
                        ("xl/worksheets/sheet1.xml", _u786_sheet(False))])
    with caplog.at_level("WARNING"):
        out = _u786_dg.regraft_drawings(orig, edited)
    assert out is edited
    assert any(getattr(r, "event_name", "") == "box.excel.drawing_regraft_failed" for r in caplog.records)


# ------------------------- U-786 security follow-up round (T4 graft memo + budget, T6 total-size cap)

_T4_CT_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
)
_T4_WS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
_T4_DRAWING = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing"
_T4_DRAWING_CT = "application/vnd.openxmlformats-officedocument.drawing+xml"
_T4_SHEET_DRAWING = (
    '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData/><drawing r:id="rId1"/></worksheet>'
)
_T4_SHEET_PLAIN = (
    '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    "<sheetData/></worksheet>"
)


def _t4_book(n_sheets, drawing_of, parts_of, *, extra_overrides=0, body="<p/>", edited=False):
    """Sheet i uses drawing drawing_of(i); drawing d's rels reach parts_of(d) (full member names)."""
    members, ovr, wb_sheets, wb_rels = {}, [], [], []
    for i in range(n_sheets):
        wb_sheets.append(f'<sheet name="S{i}" sheetId="{i + 1}" r:id="rId{i + 1}"/>')
        wb_rels.append(f'<Relationship Id="rId{i + 1}" Type="{_T4_WS}" Target="worksheets/sheet{i + 1}.xml"/>')
        if edited:
            members[f"xl/worksheets/sheet{i + 1}.xml"] = _T4_SHEET_PLAIN
        else:
            members[f"xl/worksheets/sheet{i + 1}.xml"] = _T4_SHEET_DRAWING
            members[f"xl/worksheets/_rels/sheet{i + 1}.xml.rels"] = (
                f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_REL_NS}">'
                f'<Relationship Id="rId1" Type="{_T4_DRAWING}" Target="../drawings/drawing{drawing_of(i)}.xml"/>'
                "</Relationships>"
            )
    if not edited:
        for d in sorted({drawing_of(i) for i in range(n_sheets)}):
            names = parts_of(d)
            members[f"xl/drawings/drawing{d}.xml"] = "<x/>"
            ovr.append(f'<Override PartName="/xl/drawings/drawing{d}.xml" ContentType="{_T4_DRAWING_CT}"/>')
            members[f"xl/drawings/_rels/drawing{d}.xml.rels"] = (
                f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_REL_NS}">'
                + "".join(
                    f'<Relationship Id="rId{k + 1}" Type="urn:x/other" Target="{n.rsplit("/", 1)[1]}"/>'
                    for k, n in enumerate(names)
                )
                + "</Relationships>"
            )
            for n in names:
                members[n] = body
                ovr.append(f'<Override PartName="/{n}" ContentType="application/x-p"/>')
        ovr.append("".join(
            f'<Override PartName="/xl/junk{k}.xml" ContentType="application/x-j"/>' for k in range(extra_overrides)
        ))
    ct = _T4_CT_HEAD + "".join(ovr) + "</Types>" if not edited else _T4_CT_HEAD + "</Types>"
    head = [("[Content_Types].xml", ct), ("_rels/.rels", _U786_ROOT),
            ("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(wb_sheets) + "</sheets></workbook>"),
            ("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="%s">' % _U786_REL_NS + "".join(wb_rels) + "</Relationships>")]
    return _u786_zip(head + sorted(members.items()))


def test_t4_200_sheets_sharing_a_499_part_closure_with_5000_overrides_graft_in_under_a_second():
    shared = [f"xl/drawings/p{k}.xml" for k in range(499)]
    orig = _t4_book(200, lambda i: 1, lambda d: shared, extra_overrides=5000 - 499)
    edited = _t4_book(200, lambda i: 1, lambda d: shared, edited=True)
    t0 = _u786_time.monotonic()
    out = _u786_dg.regraft_drawings(orig, edited)
    assert _u786_time.monotonic() - t0 < 1.0
    assert out is not edited


def test_t4_graft_budget_skips_the_sheets_past_it_with_the_sheet_skipped_warning(monkeypatch, caplog):
    monkeypatch.setattr(_u786_dg, "_MAX_GRAFT_VISITS", 500)
    parts_of = lambda d: [f"xl/drawings/d{d}_{k}.xml" for k in range(200)]  # noqa: E731
    orig = _t4_book(5, lambda i: i + 1, parts_of)
    edited = _t4_book(5, lambda i: i + 1, parts_of, edited=True)
    with caplog.at_level(logging.WARNING):
        out = _u786_dg.regraft_drawings(orig, edited)
    skipped = [r for r in caplog.records if "drawing_regraft_sheet_skipped" in r.getMessage()]
    assert len(skipped) == 3  # visits 201 + 201 restore sheets 1-2; sheet 3 crosses 500 and 4-5 follow
    assert out is not edited


def test_t6_graft_total_size_cap_fails_soft_when_members_sum_over_it(monkeypatch):
    monkeypatch.setattr(_u786_dg, "_MAX_READ_BYTES", 2000)
    parts_of = lambda d: [f"xl/drawings/big{k}.xml" for k in range(3)]  # noqa: E731
    orig = _t4_book(1, lambda i: 1, parts_of, body="x" * 1000)
    edited = _t4_book(1, lambda i: 1, parts_of, edited=True)
    assert _u786_dg.regraft_drawings(orig, edited) is edited


def _u786_count_walks(monkeypatch):
    calls = {"n": 0}
    real = _u786_dg._reachable

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(_u786_dg, "_reachable", counted)
    return calls


def test_u786_walks_over_the_part_limit_are_charged_to_the_graft_budget(monkeypatch):
    """Each sheet with its own drawing over an oversized shared closure used to walk it in full
    (limit + 1 parts) for free: N sheets cost N walks inside the global drain lock. Over-limit
    walks are now charged, so the budget runs out after a handful."""
    over = [f"xl/drawings/p{k}.xml" for k in range(_u786_dg._MAX_DRAWING_PARTS + 1)]
    orig = _t4_book(30, lambda i: i + 1, lambda d: over)
    edited = _t4_book(30, lambda i: i + 1, lambda d: over, edited=True)
    calls = _u786_count_walks(monkeypatch)
    out = _u786_dg.regraft_drawings(orig, edited)
    assert out == edited
    assert calls["n"] <= _u786_dg._MAX_GRAFT_VISITS // (_u786_dg._MAX_DRAWING_PARTS + 1) + 1, calls


def test_u786_one_oversized_drawing_shared_by_many_sheets_is_walked_once(monkeypatch):
    over = [f"xl/drawings/p{k}.xml" for k in range(_u786_dg._MAX_DRAWING_PARTS + 1)]
    orig = _t4_book(30, lambda i: 1, lambda d: over)
    edited = _t4_book(30, lambda i: 1, lambda d: over, edited=True)
    calls = _u786_count_walks(monkeypatch)
    assert _u786_dg.regraft_drawings(orig, edited) == edited
    assert calls["n"] == 1, calls


# ------------------------- U-786 hardening round (H): failed walks memoised + charged, graft deadline

import pytest  # noqa: E402

_H_BAD_RELS = b'<?xml version="1.0"?><Relationships xmlns="' + _U786_REL_NS.encode() + b'">' + b" " * 200_000 + b"<"


def _h_with_member(pkg: bytes, name: str, body: bytes) -> bytes:
    z = zipfile.ZipFile(BytesIO(pkg))
    return _u786_zip([(i.filename, z.read(i.filename)) for i in z.infolist()] + [(name, body)])


def _h_count_bad_rels_parses(monkeypatch):
    calls = {"n": 0}
    real = _u786_dg._parse_rels

    def counted(data):
        if data == _H_BAD_RELS:
            calls["n"] += 1
        return real(data)

    monkeypatch.setattr(_u786_dg, "_parse_rels", counted)
    return calls


def test_h_a_malformed_rels_shared_by_many_drawings_is_parsed_once(monkeypatch):
    """The reviewer's DoS: 20 sheets, each with its own drawing, all reaching one part whose .rels is
    malformed. Each sheet re-parsed it (ET.ParseError was not memoised), ~18 s in the drain lock."""
    shared = ["xl/drawings/shared.xml"]
    orig = _h_with_member(_t4_book(20, lambda i: i + 1, lambda d: shared),
                          "xl/drawings/_rels/shared.xml.rels", _H_BAD_RELS)
    edited = _t4_book(20, lambda i: i + 1, lambda d: shared, edited=True)
    calls = _h_count_bad_rels_parses(monkeypatch)
    assert _u786_dg.regraft_drawings(orig, edited) == edited
    assert calls["n"] == 1, calls


def test_h_failed_walks_from_distinct_roots_are_charged_to_the_budget(monkeypatch):
    shared = ["xl/drawings/shared.xml"]
    orig = _h_with_member(_t4_book(30, lambda i: i + 1, lambda d: shared),
                          "xl/drawings/_rels/shared.xml.rels", _H_BAD_RELS)
    edited = _t4_book(30, lambda i: i + 1, lambda d: shared, edited=True)
    calls = _u786_count_walks(monkeypatch)
    assert _u786_dg.regraft_drawings(orig, edited) == edited
    assert calls["n"] <= _u786_dg._MAX_GRAFT_VISITS // (_u786_dg._MAX_DRAWING_PARTS + 1) + 1, calls


def test_h_a_root_whose_walk_raised_is_never_walked_again(monkeypatch):
    shared = ["xl/drawings/shared.xml"]
    orig = _h_with_member(_t4_book(30, lambda i: 1, lambda d: shared),
                          "xl/drawings/_rels/shared.xml.rels", _H_BAD_RELS)
    edited = _t4_book(30, lambda i: 1, lambda d: shared, edited=True)
    calls = _u786_count_walks(monkeypatch)
    assert _u786_dg.regraft_drawings(orig, edited) == edited
    assert calls["n"] == 1, calls


def test_h_a_drawing_shared_by_many_sheets_is_checked_once(monkeypatch):
    """The restorable check (marker scan + content types) runs once per drawing, not once per sheet:
    the work done for drawing1 is the same with 2 sheets sharing it as with 30."""
    calls = {"n": 0}
    real = _u786_dg._Types.effective

    def counted(self, part):
        if part == "xl/drawings/drawing1.xml":
            calls["n"] += 1
        return real(self, part)

    monkeypatch.setattr(_u786_dg._Types, "effective", counted)
    seen = []
    for n in (2, 30):
        calls["n"] = 0
        orig = _t4_book(n, lambda i: 1, lambda d: ["xl/drawings/p0.xml"])
        edited = _t4_book(n, lambda i: 1, lambda d: ["xl/drawings/p0.xml"], edited=True)
        assert _u786_dg.regraft_drawings(orig, edited) is not edited
        seen.append(calls["n"])
    assert seen[0] == seen[1], seen


def test_h_graft_past_its_deadline_fails_soft_with_the_edited_package(monkeypatch, caplog):
    orig = _t4_book(3, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"])
    edited = _t4_book(3, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"], edited=True)
    assert _u786_dg.regraft_drawings(orig, edited) is not edited  # restores within the deadline
    monkeypatch.setattr(_u786_dg, "_GRAFT_DEADLINE_SECONDS", -1.0)
    with caplog.at_level(logging.WARNING):
        out = _u786_dg.regraft_drawings(orig, edited)
    assert out is edited
    failed = [r for r in caplog.records if getattr(r, "event_name", "") == "box.excel.drawing_regraft_failed"]
    assert failed and "second" in failed[0].reason


def test_h_graft_checks_its_deadline_while_walking(monkeypatch):
    """A clock that passes the deadline mid-walk stops the walk there: the remaining parts' rels are
    never read (400 parts; the clock runs out after ~50 reads)."""
    over = [f"xl/drawings/p{k}.xml" for k in range(400)]
    orig = _t4_book(1, lambda i: 1, lambda d: over)
    edited = _t4_book(1, lambda i: 1, lambda d: over, edited=True)
    ticks, reads = {"n": 0}, {"n": 0}
    real = _u786_dg._rels

    def clock():
        ticks["n"] += 1
        return 0.0 if ticks["n"] < 50 else 1e9

    def counted(parts, part):
        reads["n"] += 1
        return real(parts, part)

    monkeypatch.setattr(_u786_dg, "_now", clock)
    monkeypatch.setattr(_u786_dg, "_rels", counted)
    assert _u786_dg.regraft_drawings(orig, edited) is edited
    assert reads["n"] < 60, reads


def test_h_deadline_fails_the_whole_graft_not_just_the_sheets_after_it(monkeypatch, caplog):
    """Sheets 1-2 are planned before the clock runs out inside sheet 3 (the last one, in its rels read):
    the graft still returns the edited package unchanged (no partial restore of sheets 1-2) and logs
    drawing_regraft_failed, not a per-sheet skip. The deadline must never be caught as one sheet's error."""
    orig = _t4_book(3, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"])
    edited = _t4_book(3, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"], edited=True)
    now, parses = {"t": 0.0}, {"n": 0}
    real = _u786_dg._sheet_drawing_rid

    def parse(xml):
        parses["n"] += 1
        if parses["n"] == 3:
            now["t"] = 1e9
        return real(xml)

    monkeypatch.setattr(_u786_dg, "_now", lambda: now["t"])
    monkeypatch.setattr(_u786_dg, "_sheet_drawing_rid", parse)
    with caplog.at_level(logging.WARNING):
        assert _u786_dg.regraft_drawings(orig, edited) is edited
    events = [getattr(r, "event_name", "") for r in caplog.records]
    assert "box.excel.drawing_regraft_failed" in events
    assert "box.excel.drawing_regraft_sheet_skipped" not in events


def test_h_any_error_inside_a_walk_is_memoised_and_charged_like_a_skip(monkeypatch):
    """Not only _Skip and parse errors: whatever a walk raises is remembered per root and charged,
    so 30 sheets over one root walk it once."""
    orig = _t4_book(30, lambda i: 1, lambda d: ["xl/drawings/p0.xml"])
    edited = _t4_book(30, lambda i: 1, lambda d: ["xl/drawings/p0.xml"], edited=True)
    real = _u786_dg._is_package_rel

    def flaky(rel):
        if rel.get("Target", "").endswith("p0.xml"):
            raise RuntimeError("crafted")
        return real(rel)

    monkeypatch.setattr(_u786_dg, "_is_package_rel", flaky)
    calls = _u786_count_walks(monkeypatch)
    assert _u786_dg.regraft_drawings(orig, edited) == edited
    assert calls["n"] == 1, calls


def test_h_graft_checks_its_deadline_before_each_sheet(monkeypatch):
    """Sheets without a <drawing> never reach rels_of in the planning loop, so their parses need the
    per-sheet check: once the clock passes the deadline, no further sheet XML is parsed."""
    orig = _t4_book(30, lambda i: 1, lambda d: ["xl/drawings/p0.xml"])
    z = zipfile.ZipFile(BytesIO(orig))
    members = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    members = [(n, _T4_SHEET_PLAIN if n.startswith("xl/worksheets/sheet") else b) for n, b in members]
    orig = _u786_zip(members)  # every sheet keeps its drawing rel, none has a <drawing> element
    edited = _t4_book(30, lambda i: 1, lambda d: ["xl/drawings/p0.xml"], edited=True)
    now = {"t": 0.0}
    parses = {"n": 0}
    real = _u786_dg._sheet_drawing_rid

    def parse(xml):
        parses["n"] += 1
        now["t"] = 1e9  # the first sheet's parse runs past the deadline
        return real(xml)

    monkeypatch.setattr(_u786_dg, "_now", lambda: now["t"])
    monkeypatch.setattr(_u786_dg, "_sheet_drawing_rid", parse)
    assert _u786_dg.regraft_drawings(orig, edited) is edited
    assert parses["n"] == 1, parses


def test_h_walk_never_redoes_a_failed_walk_for_the_same_root(monkeypatch):
    """The _Walk primitive itself (its callers also memoise per drawing): a root whose walk raised is
    answered from the memo with the same skip reason, and charged once."""
    shared = ["xl/drawings/shared.xml"]
    orig = _h_with_member(_t4_book(1, lambda i: 1, lambda d: shared), "xl/drawings/_rels/shared.xml.rels", _H_BAD_RELS)
    z = zipfile.ZipFile(BytesIO(orig))
    walk = _u786_dg._Walk(_u786_dg._Members(z, _u786_dg._Budget()))
    calls = _u786_count_walks(monkeypatch)
    reasons = []
    for _ in range(3):
        with pytest.raises(_u786_dg._Skip) as exc:
            walk.reach("xl/drawings/drawing1.xml")
        reasons.append(exc.value.args[0])
    assert calls["n"] == 1, calls
    assert len(set(reasons)) == 1 and "cannot be read" in reasons[0], reasons
    assert walk.visits == _u786_dg._MAX_DRAWING_PARTS + 1


def _h_book_with_big_rels_on_drawing3() -> tuple:
    """3 sheets, own drawings d1-d3; d3's closure part has a 5000-byte (well-formed) .rels."""
    orig = _t4_book(3, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"])
    big = (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_REL_NS}">'.encode()
           + b" " * 5000 + b"</Relationships>")
    orig = _h_with_member(orig, "xl/drawings/_rels/d3.xml.rels", big)
    edited = _t4_book(3, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"], edited=True)
    return orig, edited


@pytest.mark.parametrize("trigger", ["size-cap", "deadline"])
def test_h_a_cap_or_deadline_inside_the_last_sheets_walk_fails_the_whole_graft(monkeypatch, caplog, trigger):
    """Sheets 1-2 are planned; the size cap / deadline trips while WALKING sheet 3's drawing. Neither may be
    caught as that walk's (or that .rels's) own failure: the graft returns the edited package unchanged,
    with drawing_regraft_failed and no per-sheet skip, never a partial restore of sheets 1-2."""
    orig, edited = _h_book_with_big_rels_on_drawing3()
    if trigger == "size-cap":
        monkeypatch.setattr(_u786_dg, "_MAX_MEMBER_BYTES", 4000)
    else:
        now = {"t": 0.0}
        real = _u786_dg._rels

        def rels(parts, part):
            if part == "xl/drawings/drawing3.xml":
                now["t"] = 1e9  # the next rels read (d3.xml's, inside the walk) is past the deadline
            return real(parts, part)

        monkeypatch.setattr(_u786_dg, "_now", lambda: now["t"])
        monkeypatch.setattr(_u786_dg, "_rels", rels)
    with caplog.at_level(logging.WARNING):
        assert _u786_dg.regraft_drawings(orig, edited) is edited
    events = [getattr(r, "event_name", "") for r in caplog.records]
    assert "box.excel.drawing_regraft_failed" in events
    assert "box.excel.drawing_regraft_sheet_skipped" not in events


def _h_rels_with_external_links(n: int) -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_U786_REL_NS}">'.encode()
            + b"".join(f'<Relationship Id="x{k}" Type="urn:x/link" Target="https://example.invalid/{k}" '
                       f'TargetMode="External"/>'.encode() for k in range(n))
            + b"</Relationships>")


def test_h_a_drawing_rels_over_the_cap_is_skipped_without_inflating_it(monkeypatch, caplog):
    """The reviewer's P2 input: a drawing whose .rels holds a huge number of External relationships.
    Over _MAX_CLOSURE_RELS_BYTES it is refused from the zip directory, never inflated or parsed."""
    orig = _t4_book(2, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"])
    big = _h_rels_with_external_links(20_000)
    assert len(big) > _u786_dg._MAX_CLOSURE_RELS_BYTES
    orig = _h_with_member(orig, "xl/drawings/_rels/d2.xml.rels", big)
    edited = _t4_book(2, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"], edited=True)
    reads = []
    real = _u786_dg._Budget.read
    monkeypatch.setattr(_u786_dg._Budget, "read", lambda self, zf, name: reads.append(name) or real(self, zf, name))
    with caplog.at_level(logging.WARNING):
        out = _u786_dg.regraft_drawings(orig, edited)
    assert out is not edited  # sheet S0 (drawing 1) still restores
    assert "xl/drawings/_rels/d2.xml.rels" not in reads
    skipped = [r for r in caplog.records if getattr(r, "event_name", "") == "box.excel.drawing_regraft_sheet_skipped"]
    assert len(skipped) == 1 and "cap" in skipped[0].reason


def test_h_a_large_rels_under_the_cap_is_parsed_once_end_to_end(monkeypatch):
    """Under the cap, the graft's copy of a large drawing .rels is not re-parsed by the prune step: the
    original is parsed once by the walk, and only the validation backstop parses the copy."""
    orig = _t4_book(1, lambda i: 1, lambda d: ["xl/drawings/d1.xml"])
    big = _h_rels_with_external_links(4_000)
    assert 200_000 < len(big) <= _u786_dg._MAX_CLOSURE_RELS_BYTES
    orig = _h_with_member(orig, "xl/drawings/_rels/d1.xml.rels", big)
    edited = _t4_book(1, lambda i: 1, lambda d: ["xl/drawings/d1.xml"], edited=True)
    big_parses = {"n": 0}
    real = _u786_dg._parse_rels

    def counted(data):
        if data is not None and len(data) > 200_000:
            big_parses["n"] += 1
        return real(data)

    monkeypatch.setattr(_u786_dg, "_parse_rels", counted)
    assert _u786_dg.regraft_drawings(orig, edited) is not edited
    assert big_parses["n"] == 1, big_parses


def _h_trip_on_first_call(monkeypatch, name):
    """Patch drawing_graft.<name> so its first call moves the graft's clock past the deadline; return its call count."""
    now, calls = {"t": 0.0}, {"n": 0}
    real = getattr(_u786_dg, name)

    def tripping(*a, **k):
        calls["n"] += 1
        now["t"] = 1e9
        return real(*a, **k)

    monkeypatch.setattr(_u786_dg, "_now", lambda: now["t"])
    monkeypatch.setattr(_u786_dg, name, tripping)
    return calls


@pytest.mark.parametrize("phase, hook", [
    ("copy", "_ser_rels"),         # step 1: the first copied drawing's rels are serialised
    ("repoint", "_insert_drawing"),  # step 2: the first sheet gets its <drawing> element
    ("prune", "_rels_base"),       # step 3: the first .rels in the package is read for references
])
def test_h_every_phase_that_grows_with_the_input_stops_at_the_deadline(monkeypatch, caplog, phase, hook):
    """The reviewer's P2: the deadline was checked only while planning, so the copy, repoint, prune and
    validation phases ran unbounded. The clock passes the deadline inside the FIRST item of a phase:
    that phase does no second item, and the whole graft fails soft."""
    orig = _t4_book(3, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"])
    edited = _t4_book(3, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"], edited=True)
    calls = _h_trip_on_first_call(monkeypatch, hook)
    with caplog.at_level(logging.WARNING):
        assert _u786_dg.regraft_drawings(orig, edited) is edited
    assert calls["n"] == 1, (phase, calls)
    failed = [r for r in caplog.records if getattr(r, "event_name", "") == "box.excel.drawing_regraft_failed"]
    assert failed and "deadline" in failed[0].reason


def test_h_the_validation_backstop_stops_at_the_deadline(monkeypatch, caplog):
    """The clock passes the deadline in step 4 (content types), so the backstop's first parse must not run."""
    orig = _t4_book(2, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"])
    edited = _t4_book(2, lambda i: i + 1, lambda d: [f"xl/drawings/d{d}.xml"], edited=True)
    now = {"t": 0.0}
    real = _u786_dg._Types.to_bytes

    def to_bytes(self):
        now["t"] = 1e9
        return real(self)

    monkeypatch.setattr(_u786_dg, "_now", lambda: now["t"])
    monkeypatch.setattr(_u786_dg._Types, "to_bytes", to_bytes)
    with caplog.at_level(logging.WARNING):
        assert _u786_dg.regraft_drawings(orig, edited) is edited
    failed = [r for r in caplog.records if getattr(r, "event_name", "") == "box.excel.drawing_regraft_failed"]
    assert failed and "deadline" in failed[0].reason
