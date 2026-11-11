"""Clone the next draw tab into a Box project tracker workbook (U-786).

WHY: each construction project keeps a "tracker" .xlsx in Box. Every client draw
request is a "Draw Request NN" tab plus one column on the "Draw Request Trend"
tab. A human adds both by hand each draw. This module does it at the zip/XML
level, because openpyxl's save drops the DrawingML shapes (logo, INVOICE box).

WHAT: pure functions over xlsx bytes, no I/O:
  plan_next_draw      validate the source tab, the new tab name, the trend tab
  clone_draw_tab      copy the source tab (sheet XML, drawing, defined names)
  extend_trend        add one draw column to the right of the last draw column
  refresh_cached_values  recompute cached <v> values + category row visibility
  one_tab_render_package a one-sheet, formula-free workbook for PDF rendering
  finish_draw_tabs    plan -> clone -> extend, returning totals + render packages
  render_existing     re-derive totals for a draw already finished (in memory)

SUPPORTED LAYOUT (v1): the Draw Request tab's G cost-code rows hold exactly
  SUMIFS(<ledger>!N:N,<ledger>!B:B,'<tab>'!A<r>,<ledger>!H:H,$E$10|$D$4)
  and SUM(<range>) / VLOOKUP(A<r>,'<tab>'!A:G,7,FALSE) on the Trend. Shared and
  array formulas are refused. Any other formula shape, a G702/G703 tab for the
  source draw, a Trend with conditional formatting or data validation, or a
  Trend whose last draw column reads another tab, raises UnsupportedTrackerLayout.
  The module refuses rather than guesses.

PRINTER SETTINGS: the cloned tab's printerSettings relationship is dropped
(with the pageSetup r:id attribute), so the copy uses default printer settings.

UNTRUSTED INPUT (U-786 H): the tracker is whatever a Box editor saved. Every public
function is wrapped by _tracker_boundary: this module's own refusals (DrawTabsError)
pass through, and any other exception a crafted package provokes becomes
UnsupportedTrackerLayout, logged ERROR as box.excel.draw_tabs_unexpected_error with
its traceback. Refusal text is printable only (DrawTabsError escapes control
characters and caps at 600 characters); tracker values in it go through echo_text.

Money is Decimal only; no float arithmetic.
"""

import bisect
import datetime
import functools
import html
import logging
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from shared.api.money import round_money
from integrations.box.excel.business.ooxml import write_package

try:
    from lzma import LZMAError as _LZMAError
except ImportError:  # pragma: no cover
    class _LZMAError(Exception):
        pass

logger = logging.getLogger(__name__)

TREND_TAB = "Draw Request Trend"

_CT = "[Content_Types].xml"
_WB = "xl/workbook.xml"
_WB_RELS = "xl/_rels/workbook.xml.rels"
_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_EPOCH = datetime.date(1899, 12, 30)

_ATTR = re.compile(r'([\w:.-]+)="([^"]*)"')
_ROW = re.compile(r"<row\b([^>]*?)\s*(?:/>|>(.*?)</row>)", re.S)
_CELL = re.compile(r"<c\b([^>]*?)\s*(?:/>|>(.*?)</c>)", re.S)
_F = re.compile(r"<f\b([^>]*?)\s*(?:/>|>(.*?)</f>)", re.S)
_V = re.compile(r"<v(?:\s*/>|>(.*?)</v>)", re.S)
_IS = re.compile(r"<is>.*?</is>", re.S)
_SHEETDATA = re.compile(r"<sheetData\b[^>]*?(?:/>|>(.*?)</sheetData>)", re.S)
_DEFNAME = re.compile(r"<definedName\b([^>]*)>(.*?)</definedName>", re.S)
_NUMTXT = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_ROWNUM = r"[1-9][0-9]{0,6}"  # U-786 H: an Excel row number (max 1048576): ASCII, no leading zero, never int() of more

_LED = r"(?P<led>DETAILS|'(?:[^']|'')+')"
_SUMIFS = re.compile(
    rf"SUMIFS\({_LED}!N:N,(?P=led)!B:B,"
    r"(?P<tab>'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!\$?A\$?(?P<row>" + _ROWNUM + r"),"
    r"(?P=led)!H:H,(?P<hc>\$E\$10|\$D\$4)\)$"
)
_SUM = re.compile(
    r"SUM\(\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>" + _ROWNUM + r"):\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>" + _ROWNUM + r")\)$"
)
_VLOOKUP = re.compile(
    r"VLOOKUP\(\$?A\$?(?P<kr>" + _ROWNUM + r"),(?P<tab>'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!\$?A:\$?G,7,FALSE\)$"
)
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.$])"
    r"(?P<sheet>'(?:[^']|'')+'!|[A-Za-z_][A-Za-z0-9_.]*!)?"
    r"(?P<ref>\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?|\$?[A-Z]{1,3}:\$?[A-Z]{1,3})"
    r"(?![A-Za-z0-9_(!])"
)
_CELLREF = re.compile(r"(\$?)([A-Z]{1,3})(?=\$?\d|:|$)")


# ------------------------------------------------------------------ errors

def _printable(text: str) -> str:
    """U-786 H: control and separator characters escaped (a newline becomes the two characters \\n)."""
    if text.isprintable():
        return text
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii") for ch in text)


class DrawTabsError(ValueError):
    """Base error. Messages name the sheet and cell so an operator can act."""

    def __init__(self, *args):
        # U-786 T2/H (defence in depth): a refusal is printable text of at most 600 characters,
        # however it was built.
        def clean(a):
            if not isinstance(a, str):
                return a
            a = _printable(a)
            return a[:597] + "..." if len(a) > 600 else a

        super().__init__(*(clean(a) for a in args))


class UnsupportedTrackerLayout(DrawTabsError):
    """The tracker is outside the v1 layout. Nothing was written."""


class _CellError(UnsupportedTrackerLayout):
    """A worksheet cell this module cannot read; callers name the tab and cell."""


class _XlErr:
    """An Excel error value (#N/A, #DIV/0!, ...). Never computed around (D7)."""

    def __init__(self, text):
        self.text = text


class DrawAlreadyFinished(DrawTabsError):
    def __init__(self, tab_name):
        self.tab_name = tab_name
        super().__init__(f"draw already finished: tab {echo_text(tab_name)} already holds this invoice number")


class PreviousDrawMissing(DrawTabsError):
    def __init__(self, previous_label):
        self.previous_label = previous_label
        super().__init__(f"previous draw {echo_text(previous_label)} has no Draw Request tab (E10); finish it first")


def _tracker_boundary(fn):
    """U-786 H: every public entry point reads untrusted tracker bytes. DrawTabsError is this module's
    own refusal and passes through unchanged. Anything else a crafted package provokes deep in a
    helper (an int() over a digit run, a Decimal signal, a missing attribute, a decode error) becomes
    UnsupportedTrackerLayout here, logged ERROR with its traceback, so the caller refuses (409)
    rather than failing with a 500. The entry points are pure: nothing was written either way.
    The refusal names only the exception class, because its text may carry tracker content."""

    @functools.wraps(fn)
    def guarded(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DrawTabsError:
            raise
        except Exception as error:
            logger.error(
                "box.excel.draw_tabs_unexpected_error",
                exc_info=True,
                extra={
                    "event_name": "box.excel.draw_tabs_unexpected_error",
                    "entry_point": fn.__name__,
                    "error_class": type(error).__name__,
                },
            )
            raise UnsupportedTrackerLayout(
                f"the tracker could not be evaluated ({type(error).__name__} in {fn.__name__}); nothing was written"
            ) from None

    return guarded


# ------------------------------------------------------------- public types

@dataclass(frozen=True)
class DrawTabPlan:
    invoice_number: str
    draw_no: int
    previous_label: str
    source_tab: str
    new_tab: str
    trend_tab: str


@dataclass(frozen=True)
class FinishedDrawTabs:
    workbook: bytes
    plan: DrawTabPlan
    tab_total: Decimal
    trend_total: Decimal
    render_invoice: bytes
    render_trend: bytes


# ------------------------------------------------------------ small helpers

def _c2n(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def _n2c(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cref(col, row):
    return f"{_n2c(col)}{row}"


def echo_text(text) -> str:
    """Tracker-derived text as it may appear in a refusal: repr(), at most 80 characters (U-786 S4)."""
    r = repr(str(text))
    return r if len(r) <= 80 else r[:77] + "..."


def _split_ref(ref):
    # A1 references only, bounded like Excel's own (3 letters, 7 digits): a crafted `r` attribute
    # must be a refusal, never an AttributeError or an int() of thousands of digits (a 500).
    m = re.fullmatch(r"([A-Z]{1,3})(" + _ROWNUM + ")", ref or "")
    if m is None:
        raise _CellError(f"cell reference {echo_text(ref)} is not an A1 reference")
    return _c2n(m.group(1)), int(m.group(2))


def _qname(name):
    return "'" + name.replace("'", "''") + "'"


def _unquote(tok):
    if tok.startswith("'"):
        return tok[1:-1].replace("''", "'")
    return tok


def _attrs(text):
    return {k: html.unescape(v) for k, v in _ATTR.findall(text)}


def _pairs(text):
    return _ATTR.findall(text)


def _with_attr(tag, name, value):
    pat = re.compile(rf'(\s{re.escape(name)}=")[^"]*(")')
    if pat.search(tag):
        return pat.sub(lambda m: m.group(1) + value + m.group(2), tag, count=1)
    m = re.search(r"\s*/?>$", tag)
    return tag[: m.start()] + f' {name}="{value}"' + tag[m.start():]


def _map_ref(ref, fn):
    return _CELLREF.sub(lambda m: m.group(1) + _n2c(fn(_c2n(m.group(2)))), ref)


def _map_formula(text, *, col_fn, own, rename=None):
    """Shift own-sheet column refs with col_fn; rename a qualified sheet if asked."""

    def repl(m):
        tok = m.group("sheet")
        ref = m.group("ref")
        if tok:
            name = _unquote(tok[:-1])
            prefix = tok
            if rename and name == rename[0]:
                prefix = _qname(rename[1]) + "!"
            if name == own:
                ref = _map_ref(ref, col_fn)
            return prefix + ref
        return _map_ref(ref, col_fn)

    return _TOKEN.sub(repl, text)


def _fmt(d):
    if d == 0:
        return "0"
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _serial(d):
    if isinstance(d, datetime.datetime):  # D17: a datetime is accepted as its date
        d = d.date()
    return (d - _EPOCH).days


def _inline(text):
    return f"<is><t>{escape(text)}</t></is>"


def _is_text(is_raw):
    return "".join(html.unescape(x) for x in re.findall(r"<t\b[^>]*>(.*?)</t>", is_raw or "", re.S))


def _parse_sst(xml):
    out = []
    for si in re.findall(r"<si\b[^>]*>(.*?)</si>", xml, re.S):
        si = re.sub(r"<rPh\b[^>]*/>|<rPh\b[^>]*>.*?</rPh>", "", si, flags=re.S)  # D20: phonetic runs are not text
        out.append("".join(html.unescape(x) for x in re.findall(r"<t\b[^>]*>(.*?)</t>", si, re.S)))
    return out


def _resolve(base, target):
    if target.startswith("/"):
        return target[1:]
    return posixpath.normpath(posixpath.join(base, target))


def _dir(part):
    return posixpath.dirname(part)


def _rels_path(part):
    d, b = posixpath.split(part)
    return f"{d}/_rels/{b}.rels"


def _rel_kind(rel):
    return rel["Type"].rsplit("/", 1)[-1]


def _ser_rels(rels):
    items = ""
    for r in rels:
        mode = f' TargetMode="{_xml_attr(r["TargetMode"])}"' if r.get("TargetMode") else ""
        items += (f'<Relationship Id="{_xml_attr(r["Id"])}" Type="{_xml_attr(r["Type"])}" '
                  f'Target="{_xml_attr(r["Target"])}"{mode}/>')
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{_NS_PKG}">{items}</Relationships>'


def _next_rid(rels_text):
    # U-786 H: ids longer than nine digits are never ours to continue (and int() of thousands of
    # digits raises); the candidate still steps past any existing Id with the same text.
    taken = set(re.findall(r'Id="([^"]*)"', rels_text))
    n = 1 + max((int(t[3:]) for t in taken if re.fullmatch(r"rId[0-9]{1,9}", t)), default=0)
    while f"rId{n}" in taken:
        n += 1
    return f"rId{n}"


def _label_split(invoice):
    m = re.search(r"(\d+)$", invoice)
    if m is None:
        return None
    return invoice[: m.start()], m.group(1)


# -------------------------------------------------------------- zip package

_MAX_MEMBER_BYTES = 64 * 1024 * 1024       # U-786 S2: per-member inflate cap
_MAX_PACKAGE_READ_BYTES = 256 * 1024 * 1024  # U-786 S2: total inflated on load


def _charge_member(info, total: int) -> int:
    if info.file_size > _MAX_MEMBER_BYTES:
        raise UnsupportedTrackerLayout(
            f"tracker part {echo_text(info.filename)} is too large ({info.file_size} bytes; cap {_MAX_MEMBER_BYTES})"
        )
    total += info.file_size
    if total > _MAX_PACKAGE_READ_BYTES:
        raise UnsupportedTrackerLayout(f"tracker package is too large (over {_MAX_PACKAGE_READ_BYTES} bytes of parts)")
    return total


def _check_wellformed(p, before: dict) -> None:
    """U-786 S3: every XML part this module rewrote or added must parse before it becomes a tracker version."""
    for name, data in p.parts.items():
        if before.get(name) == data or not name.endswith((".xml", ".rels")):  # U-786 T3: content, not identity
            continue
        try:
            ET.fromstring(data)
        except ET.ParseError as ex:
            raise DrawTabsError(f"part {echo_text(name)} is not well-formed after the draw-tab rewrite: {ex}") from None


class _Pkg:
    def __init__(self, data):
        self.parts = {}
        self.infos = {}
        total = 0
        try:
            with zipfile.ZipFile(BytesIO(data)) as zf:
                for info in zf.infolist():
                    if not info.is_dir():
                        total = _charge_member(info, total)
                        self.parts[info.filename] = zf.read(info.filename)
                        self.infos[info.filename] = info
        # U-786 T5/H: a corrupt or crafted package is a refusal, not a 500 — bad zip / deflate data,
        # an encrypted member (RuntimeError), an unsupported compression method
        # (NotImplementedError), bad bzip2 data (OSError), bad lzma data (LZMAError) or a
        # truncated stream (EOFError).
        except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, OSError, EOFError, _LZMAError):
            raise UnsupportedTrackerLayout("tracker is not a readable .xlsx package") from None
        self.order = list(self.parts)

    def get(self, name):
        return self.parts.get(name)

    def require(self, name):
        if name not in self.parts:
            raise DrawTabsError(f"package has no part {echo_text(name)}")
        return self.parts[name]

    def text(self, name):
        try:
            return self.require(name).decode("utf-8")
        except UnicodeDecodeError:
            raise UnsupportedTrackerLayout(f"part {echo_text(name)} is not UTF-8; v1 reads UTF-8 parts only") from None

    def put(self, name, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if name not in self.parts:
            self.order.append(name)
        self.parts[name] = data

    def next_name(self, pattern):
        n = 1
        while pattern.format(n) in self.parts:
            n += 1
        return pattern.format(n)

    def content_type(self, part):
        ct = self.text(_CT)
        for tag in re.findall(r"<Override\b[^>]*/>", ct):
            a = _attrs(tag)
            if a.get("PartName") == "/" + part:
                return a["ContentType"]
        ext = part.rsplit(".", 1)[-1]
        for tag in re.findall(r"<Default\b[^>]*/>", ct):
            a = _attrs(tag)
            if a.get("Extension") == ext:
                return a["ContentType"]
        raise DrawTabsError(f"package has no content type for {echo_text(part)}")

    def add_override(self, part_name, content_type):
        ct = self.text(_CT)
        if f'PartName="{part_name}"' in ct:
            return
        self.put(_CT, ct.replace("</Types>", f'<Override PartName="{part_name}" ContentType="{content_type}"/></Types>'))

    def to_bytes(self, names=None):
        names = self.order if names is None else names
        return write_package(((n, self.parts[n]) for n in names), self.infos)


def _load_rels(pkg, part):
    path = _rels_path(part)
    if pkg.get(path) is None:
        return []
    return [_attrs(t) for t in re.findall(r"<Relationship\b[^>]*/>", pkg.text(path))]


# -------------------------------------------------------- sheet XML model

class _Cell:
    def __init__(self, col, row, attrs, f_attrs=None, f_text=None, v=None, is_raw=None, raw=None):
        self.col = col
        self.row = row
        self.attrs = attrs  # [(k, raw_v)] excluding r
        self.f_attrs = f_attrs  # None = no formula element
        self.f_text = f_text
        self.v = v
        self.is_raw = is_raw
        self.raw = raw
        self.dirty = raw is None

    @classmethod
    def parse(cls, m):
        attrs = _pairs(m.group(1))
        a = dict(attrs)
        if "r" not in a:
            raise _CellError("has a cell without an r attribute")
        if a.get("t") == "d":
            raise _CellError(f"cell {echo_text(a['r'])} is a date-typed value (t=d); v1 refuses it")
        col, row = _split_ref(a["r"])
        attrs = [(k, v) for k, v in attrs if k != "r"]
        inner = m.group(2) or ""
        f_attrs = f_text = None
        fm = _F.search(inner)
        if fm:
            f_attrs = fm.group(1)
            f_text = html.unescape(fm.group(2)) if fm.group(2) is not None else None
            inner = inner[: fm.start()] + inner[fm.end():]
        v = None
        vm = _V.search(inner)
        if vm:
            v = html.unescape(vm.group(1)) if vm.group(1) else None
            inner = inner[: vm.start()] + inner[vm.end():]
        is_raw = None
        im = _IS.search(inner)
        if im:
            is_raw = im.group(0)
            inner = inner[: im.start()] + inner[im.end():]
        if inner.strip():
            raise DrawTabsError(f"cell {_cref(col, row)} carries content this module does not handle")
        return cls(col, row, attrs, f_attrs, f_text, v, is_raw, raw=m.group(0))

    @classmethod
    def new(cls, col, row, attrs, f_attrs=None, f_text=None, v=None, is_raw=None, t=None):
        attrs = [(k, val) for k, val in attrs if k != "t"]
        if t:
            attrs.append(("t", t))
        return cls(col, row, attrs, f_attrs, f_text, v, is_raw, raw=None)

    @property
    def ref(self):
        return _cref(self.col, self.row)

    def get(self, k):
        return dict(self.attrs).get(k)

    def set_attr(self, k, val):
        if any(x == k for x, _ in self.attrs):
            self.attrs = [(x, val if x == k else y) for x, y in self.attrs]
        else:
            self.attrs.append((k, val))
        self.dirty = True

    def del_attr(self, k):
        self.attrs = [(x, y) for x, y in self.attrs if x != k]
        self.dirty = True

    def xml(self):
        if not self.dirty and self.raw is not None:
            return self.raw
        a = "".join(f' {k}="{val}"' for k, val in [("r", self.ref)] + self.attrs)
        inner = ""
        if self.f_attrs is not None:
            if self.f_text is not None:
                inner += f"<f{self.f_attrs}>{escape(self.f_text)}</f>"
            else:
                inner += f"<f{self.f_attrs}/>"
        if self.v is not None:
            inner += f"<v>{escape(self.v)}</v>"
        if self.is_raw:
            inner += self.is_raw
        return f"<c{a}>{inner}</c>" if inner else f"<c{a}/>"


class _Row:
    def __init__(self, num, attrs, cells, raw=None):
        self.num = num
        self.attrs = attrs  # [(k, raw_v)] excluding r
        self.cells = cells  # {col: _Cell}
        self.raw = raw
        self.dirty = raw is None

    @classmethod
    def parse(cls, m):
        attrs = _pairs(m.group(1))
        if "r" not in dict(attrs):
            raise _CellError("has a row without an r attribute")
        r = dict(attrs)["r"]
        if not re.fullmatch(_ROWNUM, r):
            raise _CellError(f"has a row r={echo_text(r)} that is not a row number")
        num = int(r)
        attrs = [(k, v) for k, v in attrs if k != "r"]
        cells = {}
        for cm in _CELL.finditer(m.group(2) or ""):
            c = _Cell.parse(cm)
            cells[c.col] = c
        return cls(num, attrs, cells, raw=m.group(0))

    def get(self, k):
        return dict(self.attrs).get(k)

    def set_attr(self, k, val):
        if any(x == k for x, _ in self.attrs):
            self.attrs = [(x, val if x == k else y) for x, y in self.attrs]
        else:
            self.attrs.append((k, val))
        self.dirty = True

    def set_hidden(self, hidden):
        if (self.get("hidden") in ("1", "true")) == hidden:
            return
        if hidden:
            self.set_attr("hidden", "1")
        else:
            self.attrs = [(x, y) for x, y in self.attrs if x != "hidden"]
        self.dirty = True

    def xml(self):
        if not self.dirty and self.raw is not None and not any(c.dirty for c in self.cells.values()):
            return self.raw
        a = "".join(f' {k}="{v}"' for k, v in [("r", str(self.num))] + self.attrs)
        cells = "".join(self.cells[c].xml() for c in sorted(self.cells))
        return f"<row{a}>{cells}</row>" if cells else f"<row{a}/>"


class _SheetDoc:
    def __init__(self, xml, name=""):
        m = _SHEETDATA.search(xml)
        if m is None:
            raise DrawTabsError("worksheet has no sheetData")
        self.head = xml[: m.start()]
        self.tail = xml[m.end():]
        self.rows = {}
        try:
            for rm in _ROW.finditer(m.group(1) or ""):
                r = _Row.parse(rm)
                self.rows[r.num] = r
        except _CellError as e:
            raise UnsupportedTrackerLayout(f"tab {echo_text(name)} {e}") from None

    def cell(self, col, row):
        r = self.rows.get(row)
        return r.cells.get(col) if r else None

    def row(self, num, create=False):
        r = self.rows.get(num)
        if r is None and create:
            r = _Row(num, [], {})
            self.rows[num] = r
        return r

    def set_cell(self, cell):
        r = self.row(cell.row, create=True)
        r.cells[cell.col] = cell
        r.dirty = True

    def to_xml(self):
        body = "".join(self.rows[n].xml() for n in sorted(self.rows))
        return self.head + (f"<sheetData>{body}</sheetData>" if body else "<sheetData/>") + self.tail


def _style(cell):
    return [(k, v) for k, v in cell.attrs if k == "s"] if cell is not None else []


def _put_number(doc, col, row, value):
    doc.set_cell(_Cell.new(col, row, _style(doc.cell(col, row)), v=str(value)))


def _put_inline(doc, col, row, text):
    doc.set_cell(_Cell.new(col, row, _style(doc.cell(col, row)), is_raw=_inline(text), t="inlineStr"))


def _is_vlookup(cell):
    return cell.f_attrs is not None and cell.f_text is not None and _VLOOKUP.match(cell.f_text) is not None


def _is_sumifs(cell):
    return cell.f_attrs is not None and cell.f_text is not None and _SUMIFS.match(cell.f_text) is not None


def _is_zero(v):
    return v is None or (isinstance(v, Decimal) and v == 0)


def _set_row_hidden(doc, num, hidden):
    row = doc.row(num, create=hidden)
    if row is not None:
        row.set_hidden(hidden)


def _category_header(doc, sst):
    for rnum in sorted(doc.rows):
        a = doc.rows[rnum].cells.get(1)
        if a is not None and a.f_attrs is None:
            v = _const(a, sst)
            if isinstance(v, str) and v.strip().casefold() == "category":
                return rnum
    return None


# --------------------------------------------------------- workbook model

class _Sheet:
    def __init__(self, index, name, sheet_id, rid, part, rel_type, tag):
        self.index = index
        self.name = name
        self.sheet_id = sheet_id
        self.rid = rid
        self.part = part
        self.rel_type = rel_type
        self.tag = tag


class _Workbook:
    def __init__(self, pkg):
        self.pkg = pkg
        if re.search(r'<workbookPr\b[^>]*\bdate1904="(?:1|true)"', pkg.text(_WB)):
            raise UnsupportedTrackerLayout("workbook uses the 1904 date system; v1 refuses it (D12)")
        self.rels = [_attrs(t) for t in re.findall(r"<Relationship\b[^>]*/>", pkg.text(_WB_RELS))]
        by_id = {r["Id"]: r for r in self.rels}
        self.sheets = []
        for tag in re.findall(r"<sheet\b[^>]*/>", pkg.text(_WB)):
            a = _attrs(tag)
            rel = by_id[a["r:id"]]
            self.sheets.append(
                _Sheet(len(self.sheets), a["name"], a["sheetId"], a["r:id"],
                       _resolve("xl", rel["Target"]), rel["Type"], tag)
            )
        self._by_name = {s.name: s for s in self.sheets}
        sst_rel = next((r for r in self.rels if r["Type"].endswith("/sharedStrings")), None)
        self.sst = []
        if sst_rel is not None and pkg.get(_resolve("xl", sst_rel["Target"])) is not None:
            self.sst = _parse_sst(pkg.text(_resolve("xl", sst_rel["Target"])))

    def get(self, name):
        return self._by_name.get(name)

    def sheet(self, name):
        s = self._by_name.get(name)
        if s is None:
            raise UnsupportedTrackerLayout(f"tracker has no tab {echo_text(name)}")
        return s


def _money_num(text):
    """Stored doubles carry 17 significant digits; the shortest round-trip form is the
    value the author typed (D24). Integers stay exact."""
    s = text.strip()
    if re.fullmatch(r"[+-]?\d+", s):
        return Decimal(s)
    if not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", s):
        raise _CellError(f"holds {echo_text(text)}, which is not a number")
    d = Decimal(repr(float(s)))
    if not d.is_finite():  # U-786 H: 1e999 reads as Infinity; Infinity - Infinity has no value
        raise _CellError(f"holds {echo_text(text)}, which is not a finite number")
    return d


def _const(cell, sst):
    if cell is None:
        return None
    t = cell.get("t")
    if t == "s":
        # U-786 H: ASCII digits only; str.isdigit() accepts '\u00b2', which int() rejects (a 500)
        if cell.v is None or not re.fullmatch(r"[0-9]{1,9}", cell.v) or int(cell.v) >= len(sst):
            raise _CellError(f"shared-string index {echo_text(cell.v)} is out of range")
        return sst[int(cell.v)]
    if t == "inlineStr":
        return _is_text(cell.is_raw)
    if t == "str":
        return cell.v
    if t == "e":
        return _XlErr(cell.v or "#VALUE!")
    if t == "b":
        return cell.v == "1"
    if cell.v in (None, ""):
        return None
    return _money_num(cell.v)


def _const_at(sheet, cell, sst):
    try:
        return _const(cell, sst)
    except _CellError as e:
        raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {cell.ref}: {e}") from None


def _numkey(b):
    if isinstance(b, Decimal):
        return b
    if isinstance(b, str) and _NUMTXT.match(b.strip()):
        return Decimal(b.strip())
    return None


_EVAL_VISIT_BUDGET = 5_000_000  # U-786 T1: SUM visits per _Model, across all evaluation


def _span(keys, lo, hi):
    """Ascending positions in [lo, hi] worth visiting: the range itself when it is smaller than the
    present keys in it, else those keys (U-786 T1). Callers skip any position that is absent."""
    i = bisect.bisect_left(keys, lo)
    j = bisect.bisect_right(keys, hi)
    if hi - lo + 1 < j - i:
        return range(lo, hi + 1)
    return keys[i:j]


class _Model:
    """Evaluates the v1 formula shapes against the package's sheets."""

    def __init__(self, wb, peer=None):
        self.wb = wb
        self._peer = peer  # another _Model over a package whose ledger tabs may be identical
        self._docs = {}
        self._memo = {}
        self._busy = set()
        self._ledgers = {}
        self._aidx = {}
        self._lidx = {}
        self._visits = 0
        self._rowkeys = {}
        self._colkeys = {}
        self._sumifs_totals = {}  # (ledger, mode, cost code, draw key) -> Decimal: one ledger walk per key
        self._total_cols = {}  # trend tab -> its Total column (found once, not once per row)

    def doc(self, name):
        if name not in self._docs:
            self._docs[name] = _SheetDoc(self.wb.pkg.text(self.wb.sheet(name).part), name)
        return self._docs[name]

    def value(self, sheet, col, row):
        key = (sheet, col, row)
        if key in self._memo:
            return self._memo[key]
        if key in self._busy:
            raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {_cref(col, row)} is circular")
        cell = self.doc(sheet).cell(col, row)
        self._busy.add(key)
        try:
            if cell is None:
                res = None
            elif cell.f_attrs is not None:
                res = self._formula(sheet, cell)
            else:
                res = _const(cell, self.wb.sst)
        except _CellError as e:
            raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {_cref(col, row)}: {e}") from None
        except RecursionError:  # U-786 T5: a deep SUM chain is a refusal, not a 500
            raise UnsupportedTrackerLayout("tracker formulas nest too deeply to evaluate") from None
        finally:
            self._busy.discard(key)
        self._memo[key] = res
        return res

    def _formula(self, sheet, cell):
        ref = cell.ref
        if cell.f_text is None or 't="shared"' in cell.f_attrs or 't="array"' in cell.f_attrs:
            raise UnsupportedTrackerLayout(
                f"sheet {echo_text(sheet)} cell {ref} uses a shared or array formula; v1 refuses those"
            )
        t = cell.f_text
        m = _SUMIFS.match(t)
        if m:
            return self._sumifs(sheet, cell, m)
        m = _SUM.match(t)
        if m:
            return self._sum(sheet, cell, m)
        m = _VLOOKUP.match(t)
        if m:
            return self._vlookup(sheet, cell, m)
        raise UnsupportedTrackerLayout(
            f"sheet {echo_text(sheet)} cell {ref} formula {echo_text(t)} is outside the v1 layout (SUMIFS, SUM, VLOOKUP only)"
        )

    def _same_ledger_as_peer(self, name):
        """True when the peer holds the same ledger XML and shared strings, so its parse is reusable."""
        mine = self.wb.get(name)
        theirs = self._peer.wb.get(name)
        if mine is None or theirs is None:
            return False
        return self.wb.pkg.get(mine.part) == self._peer.wb.pkg.get(theirs.part) and self.wb.sst == self._peer.wb.sst

    def _ledger(self, name):
        if name not in self._ledgers and self._peer is not None and self._same_ledger_as_peer(name):
            self._ledgers[name] = self._peer._ledger(name)
        if name not in self._ledgers:
            out = []
            sst = self.wb.sst
            for row in sorted(self.doc(name).rows.values(), key=lambda r: r.num):
                b = row.cells.get(2)
                h = row.cells.get(8)
                n = row.cells.get(14)
                for c in (b, h):
                    if c is not None and c.f_attrs is not None:
                        raise UnsupportedTrackerLayout(
                            f"sheet {echo_text(name)} cell {c.ref}: formula in a ledger key column; v1 refuses it"
                        )
                bv, hv = _const_at(name, b, sst), _const_at(name, h, sst)
                if bv is None and hv is None and n is None:
                    continue
                out.append((row.num, bv, hv, n))
            self._ledgers[name] = out
        return self._ledgers[name]

    def _sumifs(self, sheet, cell, m):
        ref = cell.ref
        led = _unquote(m.group("led"))
        tab = _unquote(m.group("tab"))
        if tab != sheet:
            raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {ref}: criterion reads {echo_text(tab)}, not this tab")
        crow = int(m.group("row"))
        crit = self.value(sheet, 1, crow)
        if not isinstance(crit, Decimal):
            raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {ref}: cost code A{crow} is not a number")
        if m.group("hc") == "$E$10":
            hv = self.value(sheet, 5, 10)
            if not isinstance(hv, str):
                raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {ref}: draw label E10 is not text")
            mode, hkey = "E10", hv.casefold()
        else:
            hv = self.value(sheet, 4, 4)
            if not isinstance(hv, Decimal):
                raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {ref}: draw date D4 is not a date serial")
            mode, hkey = "D4", hv
        # U-786: many SUMIFS cells keyed on the same (cost code, draw) share one total. Without the
        # memo a padded tracker costs (SUMIFS cells x matching rows); with it, one walk per key,
        # charged to the evaluation budget like SUM's visits.
        skey = (led, mode, crit, hkey)
        if skey in self._sumifs_totals:
            return self._sumifs_totals[skey]
        matches = self._index(led, mode).get((crit, hkey), [])
        self._charge(len(matches))
        total = Decimal(0)
        for ncell in matches:
            if ncell.f_attrs is not None:
                raise UnsupportedTrackerLayout(
                    f"sheet {echo_text(led)} cell {ncell.ref}: formula in a DETAILS amount on a matching row"
                )
            v = _const_at(led, ncell, self.wb.sst)
            if isinstance(v, _XlErr):
                raise DrawTabsError(f"sheet {echo_text(led)} cell {ncell.ref} holds {echo_text(v.text)} on a row for this draw")
            if isinstance(v, Decimal):
                total += v
        self._sumifs_totals[skey] = total
        return total

    def _index(self, led, mode):
        """(cost code, draw key) -> amount cells, built once per ledger (D22)."""
        if (led, mode) not in self._lidx:
            idx = {}
            for _rnum, b, h, ncell in self._ledger(led):
                bk = _numkey(b)
                if bk is None or ncell is None:
                    continue
                if mode == "E10":
                    if not isinstance(h, str):
                        continue
                    hk = h.casefold()
                else:
                    if not isinstance(h, Decimal):
                        continue
                    hk = h
                idx.setdefault((bk, hk), []).append(ncell)
            self._lidx[(led, mode)] = idx
        return self._lidx[(led, mode)]

    def _charge(self, n):
        self._visits += n
        if self._visits > _EVAL_VISIT_BUDGET:
            raise UnsupportedTrackerLayout("tracker formulas exceed the evaluation budget")

    def _sum(self, sheet, cell, m):
        c1, r1 = _c2n(m.group("c1")), int(m.group("r1"))
        c2, r2 = _c2n(m.group("c2")), int(m.group("r2"))
        lo_c, hi_c = min(c1, c2), max(c1, c2)
        lo_r, hi_r = min(r1, r2), max(r1, r2)
        doc = self.doc(sheet)
        if sheet not in self._rowkeys:
            self._rowkeys[sheet] = sorted(doc.rows)
        total = Decimal(0)
        # U-786 S1 / T1: visit only the cells that EXIST inside the rectangle, choosing per axis the
        # smaller of the rectangle and the existing keys. An absent cell adds nothing.
        rows = _span(self._rowkeys[sheet], lo_r, hi_r)
        self._charge(len(rows))
        for r in rows:
            row = doc.rows.get(r)
            if row is None:
                continue
            if (sheet, r) not in self._colkeys:
                self._colkeys[(sheet, r)] = sorted(row.cells)
            cols = _span(self._colkeys[(sheet, r)], lo_c, hi_c)
            self._charge(len(cols))
            for c in cols:
                if c not in row.cells:
                    continue
                v = self.value(sheet, c, r)
                if isinstance(v, _XlErr):
                    raise DrawTabsError(f"sheet {echo_text(sheet)} cell {_cref(c, r)} holds {echo_text(v.text)}; refusing to sum around it")
                if isinstance(v, Decimal):
                    total += v
        return total

    def _a_index(self, tab):
        if tab not in self._aidx:
            idx = {}
            for row in sorted(self.doc(tab).rows.values(), key=lambda r: r.num):
                if 1 in row.cells:
                    v = self.value(tab, 1, row.num)
                    if isinstance(v, Decimal) and v not in idx:
                        idx[v] = row.num
            self._aidx[tab] = idx
        return self._aidx[tab]

    def _vlookup(self, sheet, cell, m):
        kr = int(m.group("kr"))
        tab = _unquote(m.group("tab"))
        key = self.value(sheet, 1, kr)
        if not isinstance(key, Decimal):
            raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {cell.ref}: lookup key A{kr} is not a number")
        if self.wb.get(tab) is None:
            raise UnsupportedTrackerLayout(f"sheet {echo_text(sheet)} cell {cell.ref}: VLOOKUP reads missing tab {echo_text(tab)}")
        row = self._a_index(tab).get(key)
        if row is None:
            raise UnsupportedTrackerLayout(
                f"sheet {echo_text(sheet)} cell {cell.ref}: VLOOKUP key {key} not found in {echo_text(tab)} column A"
            )
        v = self.value(tab, 7, row)
        if isinstance(v, _XlErr):
            raise DrawTabsError(f"sheet {echo_text(sheet)} cell {cell.ref}: VLOOKUP reads {echo_text(v.text)} in {echo_text(tab)} G{row}")
        if v is None:
            return Decimal(0)
        if not isinstance(v, Decimal):  # D21
            raise UnsupportedTrackerLayout(
                f"sheet {echo_text(sheet)} cell {cell.ref}: VLOOKUP result {echo_text(tab)} G{row} is not a number"
            )
        return v


def _text(m, sheet, col, row):
    v = m.value(sheet, col, row)
    return v.strip() if isinstance(v, str) else None


# ---------------------------------------------------------------- planning

@_tracker_boundary
def plan_next_draw(pkg: bytes, *, invoice_number: str) -> DrawTabPlan:
    wb = _Workbook(_Pkg(pkg))
    m = _Model(wb)
    split = _label_split(invoice_number)
    if split is None:
        raise UnsupportedTrackerLayout(f"invoice number {echo_text(invoice_number)} has no trailing draw number")
    prefix, digits = split
    draw_no = int(digits)
    if draw_no <= 1:
        raise UnsupportedTrackerLayout(f"{echo_text(invoice_number)} is draw {draw_no}; the first draw is created by hand")
    candidates = list(dict.fromkeys([
        prefix + str(draw_no - 1).zfill(len(digits)),  # padded to the invoice's width
        prefix + str(draw_no - 1),                     # unpadded across a digit boundary (D15)
    ]))
    trend = wb.get(TREND_TAB)
    if trend is None:
        raise UnsupportedTrackerLayout(f"tracker has no {echo_text(TREND_TAB)} sheet")

    draw_tabs = [s for s in wb.sheets if s.name.startswith("Draw Request") and s.name != TREND_TAB]
    for s in draw_tabs + [trend]:
        if _text(m, s.name, 5, 10) == invoice_number:
            raise DrawAlreadyFinished(s.name)

    prev_tabs = [s.name for s in draw_tabs if _text(m, s.name, 5, 10) in candidates]
    if not prev_tabs:
        raise PreviousDrawMissing(candidates[0])
    if len(prev_tabs) > 1:
        raise UnsupportedTrackerLayout(
            f"more than one draw tab has E10 in {', '.join(echo_text(c) for c in candidates)}: "
            f"{', '.join(echo_text(t) for t in prev_tabs)}"
        )
    source = prev_tabs[0]
    previous = _text(m, source, 5, 10)

    sm = re.search(r"(\d+)$", source)
    if sm is None:
        raise UnsupportedTrackerLayout(f"previous draw tab {echo_text(source)} has no trailing number")
    suffix = sm.group(1)
    new_tab = source[: sm.start()] + str(draw_no).zfill(len(suffix))  # D15: width of the source token
    _check_sheet_name(new_tab)
    _check_new_tab_free(wb, new_tab)
    for s in wb.sheets:  # D13: any G702/G703 tab whose trailing integers include this draw
        if s.name.casefold().startswith(("g702", "g703")):
            if int(suffix) in {int(x) for x in re.findall(r"\d+", s.name[4:])}:
                raise UnsupportedTrackerLayout(
                    f"{echo_text(source)} has a G702/G703 tab for draw {suffix} ({echo_text(s.name)}); v1 copies only the Draw Request tab"
                )
    return DrawTabPlan(
        invoice_number=invoice_number,
        draw_no=draw_no,
        previous_label=previous,
        source_tab=source,
        new_tab=new_tab,
        trend_tab=TREND_TAB,
    )


# ------------------------------------------------------- cached values

def _store_cached(cell, val):
    if isinstance(val, str):
        cell.set_attr("t", "str")
        cell.v = val
    else:
        cell.del_attr("t")
        cell.v = _fmt(Decimal(0) if val is None else val)


@_tracker_boundary
def refresh_cached_values(pkg: bytes, sheet_names: list) -> bytes:
    return _refresh(_Pkg(pkg), sheet_names).to_bytes()


def _refresh(p: "_Pkg", sheet_names: list) -> "_Pkg":
    """refresh_cached_values in place: updates p's sheets and returns p."""
    before = dict(p.parts)
    m = _Model(_Workbook(p))
    for name in sheet_names:
        doc = m.doc(name)
        trend = name == TREND_TAB
        trend_last = max(
            (c for r in doc.rows.values() for c, cell in r.cells.items() if _is_vlookup(cell)), default=0
        )
        for rnum in sorted(doc.rows):
            row = doc.rows[rnum]
            for col in sorted(row.cells):
                cell = row.cells[col]
                if cell.f_attrs is not None:
                    _store_cached(cell, m.value(name, col, rnum))
        for rnum in sorted(doc.rows):
            row = doc.rows[rnum]
            a = m.value(name, 1, rnum)
            if not isinstance(a, Decimal):
                continue
            if trend:
                lk = [c for c, cell in row.cells.items() if _is_vlookup(cell)]
                if not lk:
                    continue
                # D6: only NUMERIC values in the draw columns and the Total column keep a
                # row visible. Text, and anything right of the Total column, never does.
                cols = sorted({c for c in row.cells if 4 <= c <= trend_last} | {_total_col(m, name)})  # U-786 S1: present cells only
                hidden = not any(
                    isinstance(v, Decimal) and v != 0
                    for v in (m.value(name, c, rnum) for c in cols)
                )
            else:
                g = row.cells.get(7)
                if g is None or not (_is_sumifs(g) or _is_vlookup(g)):
                    continue
                hidden = _is_zero(m.value(name, 7, rnum))
            _set_row_hidden(doc, rnum, hidden)
            if _is_spacer(doc.rows.get(rnum + 1)):
                if hidden or doc.rows.get(rnum + 1) is not None:
                    _set_row_hidden(doc, rnum + 1, hidden)
        p.put(m.wb.get(name).part, doc.to_xml())
    _check_wellformed(p, before)
    return p


def _is_spacer(row):
    """D2: a spacer row holds no value, no formula and no string at all."""
    if row is None:
        return True
    return not any(
        c.f_attrs is not None or c.is_raw is not None or c.v not in (None, "")
        for c in row.cells.values()
    )


_BAD_SHEET_CHARS = set(":\\/?*[]")


def _check_sheet_name(name):
    """D9: Excel's sheet-name rules."""
    if len(name) > 31:
        raise UnsupportedTrackerLayout(f"sheet name {echo_text(name)} is longer than 31 characters")
    bad = "".join(sorted({ch for ch in name if ch in _BAD_SHEET_CHARS}))
    if bad:
        raise UnsupportedTrackerLayout(f"sheet name {echo_text(name)} contains {echo_text(bad)}, which Excel forbids")


def _check_new_tab_free(wb, name):
    """D9: sheet names are compared case-insensitively."""
    for s in wb.sheets:
        if s.name.casefold() == name.casefold():
            raise UnsupportedTrackerLayout(f"tab {echo_text(name)} already exists (Excel ignores case: {echo_text(s.name)})")


def _xml_attr(text):
    """Escape an attribute value; \\t \\n \\r must survive as character references."""
    return escape(text, {'"': "&quot;", "'": "&apos;", "\t": "&#9;", "\n": "&#10;", "\r": "&#13;"})


_MARKUP_REFUSED = re.compile(r"<(extLst|colBreaks|hyperlinks|legacyDrawing|legacyDrawingHF|tableParts)\b")


def _check_markup(xml, tab):
    """D11: markup the copy cannot shift or keep consistent."""
    m = _MARKUP_REFUSED.search(xml)
    if m is not None:
        raise UnsupportedTrackerLayout(f"tab {echo_text(tab)} has <{m.group(1)}>; v1 cannot widen it")
    for af in re.finditer(r"<autoFilter\b[^>]*?(?<!/)>(.*?)</autoFilter>", xml, re.S):
        if "<filterColumn" in af.group(1):
            raise UnsupportedTrackerLayout(f"tab {echo_text(tab)} has an autoFilter with filterColumn; v1 cannot widen it")


def _check_drawing_rels(p, dpart, tab):
    """D11: a drawing copied or shifted may hold images only (a shared chart is invalid)."""
    for r in _load_rels(p, dpart):
        if not r.get("Type", "").endswith("/image"):
            raise UnsupportedTrackerLayout(
                f"tab {echo_text(tab)} drawing {echo_text(dpart)} has a {echo_text(_rel_kind(r))} relationship; v1 copies only images"
            )


def _check_no_cross_sheet_formulas(xml, tab):
    """D14: the source draw may hold only its own SUMIFS and own-column formulas."""
    doc = _SheetDoc(xml, tab)
    for n in sorted(doc.rows):
        for cell in doc.rows[n].cells.values():
            t = cell.f_text
            if cell.f_attrs is not None and t and "!" in t and _SUMIFS.match(t) is None:
                raise UnsupportedTrackerLayout(
                    f"tab {echo_text(tab)} cell {cell.ref} reads another sheet (={echo_text(t)}); v1 copies only the draw's own formulas"
                )


def _drop_calc_chain(p):
    """D4: calcChain entries point at cells whose formulas moved; fullCalcOnLoad rebuilds it."""
    if p.get("xl/calcChain.xml") is None:
        return
    del p.parts["xl/calcChain.xml"]
    p.order.remove("xl/calcChain.xml")
    p.put(_WB_RELS, re.sub(r'<Relationship\b[^>]*?/calcChain"[^>]*/>', "", p.text(_WB_RELS)))
    p.put(_CT, re.sub(r'<Override\b[^>]*PartName="/xl/calcChain\.xml"[^>]*/>', "", p.text(_CT)))


# ------------------------------------------------------------------ clone

def _shift_defined_names(wbx, pos, src_name, new_name):
    old = escape(_qname(src_name)) + "!"
    new = escape(_qname(new_name)) + "!"

    def lsid(attrs):
        m = re.search(r'\slocalSheetId="(\d+)"', attrs)
        return int(m.group(1)) if m else None

    def with_lsid(elem, n):
        return re.sub(r'(\slocalSheetId=")\d+(")', lambda m: f"{m.group(1)}{n}{m.group(2)}", elem, count=1)

    def fix_block(m):
        block = m.group(1)
        copies = [
            with_lsid(dm.group(0), pos).replace(old, new)
            for dm in _DEFNAME.finditer(block)
            if lsid(dm.group(1)) == pos
        ]

        def shift(dm):
            n = lsid(dm.group(1))
            return with_lsid(dm.group(0), n + 1) if n is not None and n >= pos else dm.group(0)

        return "<definedNames>" + _DEFNAME.sub(shift, block) + "".join(copies) + "</definedNames>"

    return re.sub(r"<definedNames>(.*?)</definedNames>", fix_block, wbx, count=1, flags=re.S)


def _shift_view_index(wbx, pos):
    """activeTab shifts when it sits at or after the insert; firstSheet only when after it (D18)."""

    def bump(shift_if):
        def fix(m):
            v = int(m.group(2))
            return m.group(1) + str(v + 1 if shift_if(v) else v) + m.group(3)

        return fix

    wbx = re.sub(r'(\sactiveTab=")(\d+)(")', bump(lambda v: v >= pos), wbx)
    wbx = re.sub(r'(\sfirstSheet=")(\d+)(")', bump(lambda v: v > pos), wbx)
    return wbx


_CALC_SUCCESSORS = frozenset({
    "oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
    "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst",
})
_XML_TAG = re.compile(r"<(/?)([A-Za-z_][\w.:-]*)[^>]*?(/?)>")


def _direct_child_start(wbx, names):
    """Offset of the first DIRECT child of the root element whose name is in `names`.

    A nested element of the same name (a workbookView's extLst) is not a successor.
    """
    depth = 0
    for m in _XML_TAG.finditer(wbx):
        closing, name, selfclose = m.group(1), m.group(2), m.group(3)
        if closing:
            depth -= 1
            continue
        if depth == 1 and name in names:
            return m.start()
        if not selfclose:
            depth += 1
    return None


def _ensure_full_calc(wbx):
    m = re.search(r"<calcPr\b([^>]*?)(/?)>", wbx)
    if m is None:  # D16: at its schema position, before the first direct successor element
        pos = _direct_child_start(wbx, _CALC_SUCCESSORS)
        if pos is None:
            pos = wbx.rindex("</workbook>")
        return wbx[:pos] + '<calcPr fullCalcOnLoad="1"/>' + wbx[pos:]
    attrs = m.group(1)
    if "fullCalcOnLoad=" in attrs:
        attrs = re.sub(r'fullCalcOnLoad="[^"]*"', 'fullCalcOnLoad="1"', attrs)
    else:
        attrs += ' fullCalcOnLoad="1"'
    return wbx[: m.start()] + f"<calcPr{attrs}{m.group(2)}>" + wbx[m.end():]


@_tracker_boundary
def clone_draw_tab(pkg: bytes, plan: DrawTabPlan, *, draw_date: datetime.date) -> bytes:
    p = _Pkg(pkg)
    before = dict(p.parts)
    wb = _Workbook(p)
    _check_sheet_name(plan.new_tab)
    _check_new_tab_free(wb, plan.new_tab)
    src = wb.sheet(plan.source_tab)
    srels = _load_rels(p, src.part)
    for r in srels:
        if _rel_kind(r) not in ("drawing", "printerSettings"):
            raise UnsupportedTrackerLayout(
                f"tab {echo_text(src.name)} has a {echo_text(_rel_kind(r))} relationship ({echo_text(r['Target'])}); v1 copies only drawings and printer settings"
            )
    _check_no_cross_sheet_formulas(p.text(src.part), src.name)
    for r in srels:
        if _rel_kind(r) == "drawing":
            _check_drawing_rels(p, _resolve(_dir(src.part), r["Target"]), src.name)

    old = escape(_qname(src.name)) + "!"
    new = escape(_qname(plan.new_tab)) + "!"
    src_xml = p.text(src.part)
    if src_xml.count(old) == 0:
        raise DrawTabsError(f"tab {echo_text(src.name)} has no cross-sheet references to rewrite; refusing to clone")
    sx = src_xml.replace(old, new)
    sx = re.sub(r'(<sheetView\b[^>]*?)\s+tabSelected="(?:1|true)"', r"\1", sx)
    sx = re.sub(r'(<sheetPr\b[^>]*?)\s+codeName="[^"]*"', r"\1", sx)  # D19
    sx = re.sub(r'\s+xr:uid="[^"]*"', "", sx)                            # D19
    doc = _SheetDoc(sx, src.name)
    _put_number(doc, 4, 4, _serial(draw_date))
    _put_inline(doc, 5, 10, plan.invoice_number)
    if any(_rel_kind(r) == "printerSettings" for r in srels):
        doc.tail = re.sub(r'(<pageSetup\b[^>]*?)\s+r:id="[^"]*"', r"\1", doc.tail)

    new_part = p.next_name("xl/worksheets/sheet{}.xml")
    new_rels = []
    for r in srels:
        kind = _rel_kind(r)
        if kind == "printerSettings":
            continue
        if kind == "drawing":
            dsrc = _resolve(_dir(src.part), r["Target"])
            dnew = p.next_name("xl/drawings/drawing{}.xml")
            p.put(dnew, p.require(dsrc))
            if p.get(_rels_path(dsrc)) is not None:
                p.put(_rels_path(dnew), p.require(_rels_path(dsrc)))
            p.add_override("/" + dnew, p.content_type(dsrc))
            new_rels.append(dict(r, Target=posixpath.relpath(dnew, _dir(new_part))))
        else:
            new_rels.append(r)
    if new_rels:
        p.put(_rels_path(new_part), _ser_rels(new_rels))
    p.put(new_part, doc.to_xml())
    p.add_override("/" + new_part, p.content_type(src.part))

    rels_text = p.text(_WB_RELS)
    rid = _next_rid(rels_text)
    target = posixpath.relpath(new_part, "xl")
    p.put(
        _WB_RELS,
        rels_text.replace(
            "</Relationships>",
            f'<Relationship Id="{rid}" Type="{_xml_attr(src.rel_type)}" Target="{target}"/></Relationships>',
        ),
    )
    max_sid = max(int(s.sheet_id) for s in wb.sheets)
    new_tag = _with_attr(src.tag, "name", _xml_attr(plan.new_tab))
    new_tag = re.sub(r'\sstate="[^"]*"', "", new_tag)  # D8: the copy is never hidden
    new_tag = _with_attr(new_tag, "sheetId", str(max_sid + 1))
    new_tag = _with_attr(new_tag, "r:id", rid)

    wbx = p.text(_WB)
    if wbx.count(src.tag) != 1:
        raise DrawTabsError(f"workbook.xml does not hold a unique <sheet> entry for {echo_text(src.name)}")
    wbx = wbx.replace(src.tag, new_tag + src.tag, 1)
    wbx = _shift_defined_names(wbx, src.index, src.name, plan.new_tab)
    wbx = _shift_view_index(wbx, src.index)
    wbx = _ensure_full_calc(wbx)
    p.put(_WB, wbx)
    _drop_calc_chain(p)
    final = _refresh(p, [plan.new_tab])
    _check_wellformed(final, before)
    return final.to_bytes()


# ------------------------------------------------------------ extend trend

def _fix_cols(body, L):
    def fix(m):
        t = m.group(0)
        mn = int(re.search(r'\smin="(\d+)"', t).group(1))
        mx = int(re.search(r'\smax="(\d+)"', t).group(1))
        nmn = mn + 1 if mn > L else mn
        nmx = mx if mx >= 16384 else (mx + 1 if mx >= L else mx)
        t = re.sub(r'\smin="\d+"', f' min="{nmn}"', t, count=1)
        return re.sub(r'\smax="\d+"', f' max="{nmx}"', t, count=1)

    return re.sub(r"<col\b[^>]*/>", fix, body)


def _remap_attr(xml, tag, attr, fn):
    pat = re.compile(rf'(<{tag}\b[^>]*?\s{attr}=")([^"]*)(")')
    return pat.sub(lambda m: m.group(1) + fn(m.group(2)) + m.group(3), xml)


@_tracker_boundary
def extend_trend(pkg: bytes, plan: DrawTabPlan, *, draw_date: datetime.date) -> bytes:
    return _extend_trend(pkg, plan, draw_date=draw_date)[0]


def _extend_trend(pkg: bytes, plan: DrawTabPlan, *, draw_date: datetime.date):
    """(final bytes, the model of final). finish_draw_tabs reuses the model for the tab totals."""
    p = _Pkg(pkg)
    before = dict(p.parts)  # U-786 T3: the input parts, to tell untouched parts from rewritten ones
    wb = _Workbook(p)
    trend = wb.sheet(plan.trend_tab)
    wb.sheet(plan.source_tab)  # raises if the source tab is gone
    sst = wb.sst
    t_xml = p.text(trend.part)
    if "<conditionalFormatting" in t_xml or "<dataValidations" in t_xml:
        raise UnsupportedTrackerLayout(
            f"tab {echo_text(plan.trend_tab)} has conditional formatting or data validation; v1 cannot widen it"
        )
    _check_markup(t_xml, plan.trend_tab)
    for r in _load_rels(p, trend.part):
        if _rel_kind(r) not in ("drawing", "printerSettings"):
            raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} has a {echo_text(_rel_kind(r))} relationship; v1 cannot widen it")
        if _rel_kind(r) == "drawing":
            _check_drawing_rels(p, _resolve(_dir(trend.part), r["Target"]), plan.trend_tab)
    tq = escape(_qname(plan.trend_tab)) + "!"
    for s in wb.sheets:
        if s.name != plan.trend_tab and tq in p.text(s.part):
            raise UnsupportedTrackerLayout(f"tab {echo_text(s.name)} has formulas that reference {echo_text(plan.trend_tab)}; v1 cannot widen it")
    for dm in _DEFNAME.finditer(p.text(_WB)):
        lid = re.search(r'\slocalSheetId="(\d+)"', dm.group(1))
        if tq in dm.group(2) and (lid is None or int(lid.group(1)) != trend.index):
            raise UnsupportedTrackerLayout(f"a defined name outside {echo_text(plan.trend_tab)} references it; v1 cannot widen it")

    doc = _SheetDoc(t_xml, plan.trend_tab)
    cat_rows = []
    L = 0
    for rnum in sorted(doc.rows):
        row = doc.rows[rnum]
        a = row.cells.get(1)
        if a is None or a.f_attrs is not None or not isinstance(_const(a, sst), Decimal):
            continue
        lk = [c for c, cell in row.cells.items() if _is_vlookup(cell)]
        if lk:
            cat_rows.append(rnum)
            L = max([L] + lk)
    if not cat_rows:
        raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} has no VLOOKUP draw columns")

    for rnum in cat_rows:
        cell = doc.rows[rnum].cells.get(L)
        if cell is None:  # D3: the new column would silently miss this category
            raise UnsupportedTrackerLayout(
                f"tab {echo_text(plan.trend_tab)} cell {_cref(L, rnum)} has no VLOOKUP; the new column would miss this category"
            )
        m = _VLOOKUP.match(cell.f_text) if cell.f_attrs is not None and cell.f_text is not None else None
        if m is None:
            raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} cell {cell.ref} is not a VLOOKUP; v1 cannot extend it")
        tab = _unquote(m.group("tab"))
        if tab != plan.source_tab:
            raise UnsupportedTrackerLayout(f"Trend's last draw column reads {echo_text(tab)}, expected {echo_text(plan.source_tab)}")

    hdr = _category_header(doc, sst)
    if hdr is None:
        raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} has no CATEGORY header row")
    hcell = doc.rows[hdr].cells.get(L)
    htext = _const(hcell, sst) if hcell is not None and hcell.f_attrs is None else None
    if not isinstance(htext, str):
        raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} header cell {_cref(L, hdr)} is not text")
    hm = re.search(r"(\d+)$", htext.strip())
    if hm is None:
        raise UnsupportedTrackerLayout(
            f"tab {echo_text(plan.trend_tab)} header cell {_cref(L, hdr)} ({echo_text(htext)}) has no trailing draw number"
        )
    new_header = htext.strip()[: hm.start()] + str(plan.draw_no).zfill(len(hm.group(1)))

    def shift(c):
        return c + 1 if c > L else c

    for rnum in sorted(doc.rows):  # D1: every own-sheet reference right of L follows the move
        for cell in doc.rows[rnum].cells.values():
            if cell.f_attrs is not None and cell.f_text is not None:
                text = _map_formula(cell.f_text, col_fn=shift, own=plan.trend_tab)
                if text != cell.f_text:
                    cell.f_text = text
                    cell.dirty = True
                    doc.rows[rnum].dirty = True
    for rnum in sorted(doc.rows):
        row = doc.rows[rnum]
        moved = sorted((c for c in row.cells if c > L), reverse=True)
        for c in moved:
            cell = row.cells.pop(c)
            if cell.f_attrs is not None:
                if cell.f_text is None:
                    raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} cell {cell.ref} is a shared formula; v1 cannot shift it")
                if "!" in cell.f_text:
                    raise UnsupportedTrackerLayout(
                        f"tab {echo_text(plan.trend_tab)} cell {cell.ref} is right of the new column and holds a cross-sheet formula; v1 cannot shift it"
                    )
            cell.col = c + 1
            cell.dirty = True
            row.cells[c + 1] = cell
        if moved:
            row.dirty = True

    nc = L + 1
    for rnum in sorted(doc.rows):
        row = doc.rows[rnum]
        sc = row.cells.get(L)
        if sc is None:
            continue
        if rnum == hdr:
            cell = _Cell.new(nc, rnum, _style(sc), is_raw=_inline(new_header), t="inlineStr")
        elif sc.f_attrs is not None:
            if sc.f_text is None:
                raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} cell {sc.ref} is a shared formula; v1 cannot copy it")
            text = _map_formula(
                sc.f_text,
                col_fn=lambda c: nc if c == L else c,
                own=plan.trend_tab,
                rename=(plan.source_tab, plan.new_tab),
            )
            cell = _Cell.new(nc, rnum, sc.attrs, f_attrs=sc.f_attrs, f_text=text)
        elif sc.v is None and sc.is_raw is None:
            cell = _Cell.new(nc, rnum, sc.attrs)
        else:
            continue
        row.cells[nc] = cell
        row.dirty = True

    def shift_text(t):
        return _map_ref(t, shift)

    head, tail = doc.head, doc.tail
    for tag, attr in (
        ("dimension", "ref"),
        ("autoFilter", "ref"),
        ("mergeCell", "ref"),
        ("selection", "sqref"),
        ("selection", "activeCell"),
        ("pane", "topLeftCell"),
    ):
        head = _remap_attr(head, tag, attr, shift_text)
        tail = _remap_attr(tail, tag, attr, shift_text)
    head = re.sub(r"<cols>(.*?)</cols>", lambda m: "<cols>" + _fix_cols(m.group(1), L) + "</cols>", head, flags=re.S)
    doc.head, doc.tail = head, tail

    for row in doc.rows.values():
        if row.dirty and row.get("spans") is not None and row.cells:
            row.set_attr("spans", f"{min(row.cells)}:{max(row.cells)}")

    _put_number(doc, 4, 4, _serial(draw_date))
    _put_inline(doc, 5, 10, plan.invoice_number)
    p.put(trend.part, doc.to_xml())

    def fix_name(dm):
        lid = re.search(r'\slocalSheetId="(\d+)"', dm.group(1))
        if lid is None or int(lid.group(1)) != trend.index:
            return dm.group(0)
        text = _map_formula(dm.group(2), col_fn=shift, own=plan.trend_tab)
        return f"<definedName{dm.group(1)}>{text}</definedName>"

    p.put(_WB, _DEFNAME.sub(fix_name, p.text(_WB)))

    for rel in _load_rels(p, trend.part):
        if _rel_kind(rel) == "drawing":
            dp = _resolve(_dir(trend.part), rel["Target"])
            dx = p.text(dp)
            dx = re.sub(
                r"<((?:\w+:)?col)>(\d+)</\1>",
                lambda m: f"<{m.group(1)}>{int(m.group(2)) + 1 if int(m.group(2)) >= L else int(m.group(2))}</{m.group(1)}>",
                dx,
            )
            p.put(dp, dx)
    _drop_calc_chain(p)
    final = _refresh(p, [plan.trend_tab]).to_bytes()
    _check_wellformed(_Pkg(final), before)  # U-786 S3/T3: parse the parts this module rewrote
    m = _check_totals(final, plan, nc, pkg)
    return final, m


# --------------------------------------------------------------- rendering

def _remove_balanced(xml: str, name: str) -> str:
    """Remove every <name>...</name> (and <name/>), counting nested <name> to find its close."""
    tag = re.compile(rf"<(/?){name}\b[^>]*?(/?)>")
    out, pos = [], 0
    while True:
        m = tag.search(xml, pos)
        if m is None:
            break
        if m.group(1) == "/":  # a stray close tag is not ours to judge; keep it
            out.append(xml[pos:m.end()])
            pos = m.end()
            continue
        if m.group(2) == "/":  # self-closing
            out.append(xml[pos:m.start()])
            pos = m.end()
            continue
        depth, scan = 1, m.end()
        while depth:
            n = tag.search(xml, scan)
            if n is None:
                raise DrawTabsError(f"unbalanced <{name}> in the sheet tail")
            if n.group(1) == "/":
                depth -= 1
            elif n.group(2) != "/":
                depth += 1
            scan = n.end()
        out.append(xml[pos:m.start()])
        pos = scan
    out.append(xml[pos:])
    return "".join(out)


_AC_BLOCK = re.compile(r"<((?:\w+:)?)AlternateContent\b[^>]*(?<!/)>(.*?)</\1AlternateContent>", re.S)
_AC_BRANCH = re.compile(r"<(?:\w+:)?(?:Choice|Fallback)\b[^>]*?/>|<(?:\w+:)?(?:Choice|Fallback)\b[^>]*?>|</(?:\w+:)?(?:Choice|Fallback)>")


def _drop_empty_alternate_content(xml: str) -> str:
    """Drop an AlternateContent whose Choice and Fallback hold nothing once the stripped content is gone."""
    return _AC_BLOCK.sub(lambda m: "" if not _AC_BRANCH.sub("", m.group(2)).strip() else m.group(0), xml)


@_tracker_boundary
def one_tab_render_package(pkg: bytes, sheet_name: str) -> bytes:
    p = _Pkg(pkg)
    before = dict(p.parts)
    wb = _Workbook(p)
    sh = wb.sheet(sheet_name)
    doc = _SheetDoc(p.text(sh.part), sheet_name)
    for row in doc.rows.values():
        for cell in row.cells.values():
            if cell.f_attrs is None:
                continue
            if cell.v in (None, ""):
                raise DrawTabsError(
                    f"tab {echo_text(sheet_name)} cell {cell.ref} is a formula with no cached value; refresh the tracker before rendering"
                )
            cell.f_attrs = None
            cell.f_text = None
            cell.dirty = True
            if cell.get("t") == "str":  # D10: the stripped cell keeps its text as an inline string
                cell.is_raw = _inline(cell.v)
                cell.v = None
                cell.set_attr("t", "inlineStr")

    srels = _load_rels(p, sh.part)
    drels = [r for r in srels if _rel_kind(r) == "drawing"]
    keep = {_CT, "_rels/.rels", _WB, _WB_RELS, sh.part}
    keep |= {n for n in p.parts if n.startswith("docProps/") or n.startswith("xl/theme/")}
    for r in wb.rels:
        if _rel_kind(r) in ("styles", "theme", "sharedStrings"):
            keep.add(_resolve("xl", r["Target"]))
    if drels:
        keep.add(_rels_path(sh.part))
    for r in drels:
        dp = _resolve(_dir(sh.part), r["Target"])
        keep.add(dp)
        keep.add(_rels_path(dp))
        for dr in _load_rels(p, dp):
            if dr.get("TargetMode") != "External":
                keep.add(_resolve(_dir(dp), dr["Target"]))

    doc.tail = re.sub(r'(<pageSetup\b[^>]*?)\s+r:id="[^"]*"', r"\1", doc.tail)
    # B3: customProperties carry r:ids (their relationships are dropped). An AlternateContent
    # whose Choice/Fallback wraps controls or OLE objects is removed whole, never left empty.
    doc.tail = re.sub(
        r"<customProperties\b[^>]*/>|<customProperties\b[^>]*(?<!/)>.*?</customProperties>", "", doc.tail, flags=re.S
    )
    # Excel nests AlternateContent inside controls / oleObjects, so those are removed by a
    # depth-counting scan (a non-greedy regex stops at the inner close tag).
    doc.tail = _remove_balanced(doc.tail, "controls")
    doc.tail = _remove_balanced(doc.tail, "oleObjects")
    doc.tail = _drop_empty_alternate_content(doc.tail)
    for tag in ("hyperlinks", "legacyDrawing", "legacyDrawingHF", "tableParts", "picture"):
        # D10: these carry r:id attributes whose relationships were dropped above
        doc.tail = re.sub(rf"<{tag}\b[^>]*/>|<{tag}\b[^>]*>.*?</{tag}>", "", doc.tail, flags=re.S)
    p.put(sh.part, doc.to_xml())
    if drels:
        p.put(_rels_path(sh.part), _ser_rels(drels))

    wbx = p.text(_WB)
    wbx = re.sub(r"<externalReferences>.*?</externalReferences>", "", wbx, flags=re.S)
    wbx = re.sub(
        r"<sheets>.*?</sheets>",
        lambda m: "<sheets>" + re.sub(r'\sstate="[^"]*"', "", _with_attr(sh.tag, "sheetId", "1")) + "</sheets>",
        wbx,
        flags=re.S,
    )

    def dn(m):
        out = ""
        for dm in _DEFNAME.finditer(m.group(1)):
            lid = re.search(r'\slocalSheetId="(\d+)"', dm.group(1))
            if lid and int(lid.group(1)) == sh.index:
                out += re.sub(r'(\slocalSheetId=")\d+(")', r"\g<1>0\2", dm.group(0), count=1)
        return f"<definedNames>{out}</definedNames>" if out else ""

    wbx = re.sub(r"<definedNames>(.*?)</definedNames>", dn, wbx, flags=re.S)
    wbx = re.sub(r'(\s(?:activeTab|firstSheet)=")\d+(")', r"\g<1>0\2", wbx)
    p.put(_WB, wbx)
    p.put(
        _WB_RELS,
        _ser_rels([r for r in wb.rels if _rel_kind(r) in ("styles", "theme", "sharedStrings") or r["Id"] == sh.rid]),
    )

    ct = p.text(_CT)

    def ov(m):
        pn = _attrs(m.group(0)).get("PartName", "")[1:]
        return m.group(0) if pn in keep else ""

    p.put(_CT, re.sub(r"<Override\b[^>]*/>", ov, ct))
    names = [n for n in p.order if n in keep and n in p.parts]
    _check_wellformed(p, before)
    return p.to_bytes(names)


# ----------------------------------------------------------------- totals

def _find_total_row(m, sheet):
    doc = m.doc(sheet)
    for rnum in sorted(doc.rows):
        for c in sorted(c for c in doc.rows[rnum].cells if c <= 6):  # A-F: the draw tab puts it in E, the Trend in C (D23); U-786 S1 sparse
            v = m.value(sheet, c, rnum)
            if isinstance(v, str) and v.strip().casefold() == "total due":
                return rnum
    raise UnsupportedTrackerLayout(f"tab {echo_text(sheet)} has no 'Total Due' row")


def _total_due_at(m, sheet, col):
    v = m.value(sheet, col, _find_total_row(m, sheet))
    if not isinstance(v, Decimal):
        raise UnsupportedTrackerLayout(f"tab {echo_text(sheet)} Total Due in column {_n2c(col)} is not a number")
    return v


def _total_col(m, trend):
    # Found once per model and tab: the visibility pass asks for it on every category row, and
    # _category_header scans the whole sheet (quadratic on a long Trend without the cache).
    if trend in m._total_cols:
        return m._total_cols[trend]
    hdr = _category_header(m.doc(trend), m.wb.sst)
    if hdr is None:
        raise UnsupportedTrackerLayout(f"tab {echo_text(trend)} has no CATEGORY header row")
    for c in sorted(m.doc(trend).rows[hdr].cells):
        v = m.value(trend, c, hdr)
        if isinstance(v, str) and v.strip().casefold() == "total":
            m._total_cols[trend] = c
            return c
    raise UnsupportedTrackerLayout(f"tab {echo_text(trend)} has no Total column header")


def _trend_total(m, trend):
    return _total_due_at(m, trend, _total_col(m, trend))


def _num(value, what):
    if value is None:
        return Decimal(0)
    if not isinstance(value, Decimal):
        raise UnsupportedTrackerLayout(f"{what} is not a number")
    return value


def _row_index(m, trend):
    """{("cat", code) | ("due", None): row} for every category row and the Total Due row."""
    rows = {}
    for rnum in sorted(m.doc(trend).rows):
        a = m.value(trend, 1, rnum)
        if isinstance(a, Decimal):
            rows[("cat", a)] = rnum
    rows[("due", None)] = _find_total_row(m, trend)
    return rows


def _check_totals(final, plan, nc, before):
    """Check the extended Trend against the package before the extension; return the model of final.

    Invariants, on the Trend tab:
      1. the new column's Total Due equals the draw tab's total;
      2. the trend total grew by exactly the draw tab's total;
      3. for every category row and the Total Due row, the row's Total moved by exactly
         its new-column value (this replaces a check that the Total column equals the sum
         of every draw column, which refused a Trend whose Total also covers a non-draw column).
    """
    m = _Model(_Workbook(_Pkg(final)))
    before_m = _Model(_Workbook(_Pkg(before)), peer=m)
    tab_total = _total_due_at(m, plan.new_tab, 7)
    new_due = _total_due_at(m, plan.trend_tab, nc)
    if new_due != tab_total:
        raise UnsupportedTrackerLayout(
            f"tab {echo_text(plan.trend_tab)} new column {_n2c(nc)} Total Due is {new_due}, not the draw's {tab_total}"
        )
    if _trend_total(m, plan.trend_tab) != _trend_total(before_m, plan.trend_tab) + tab_total:
        raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} trend total did not grow by the draw's total")
    before_tc = _total_col(before_m, plan.trend_tab)
    after_tc = _total_col(m, plan.trend_tab)
    after_rows = _row_index(m, plan.trend_tab)
    for key, brow in _row_index(before_m, plan.trend_tab).items():
        label = key[1] if key[0] == "cat" else "Total Due"
        arow = after_rows.get(key)
        if arow is None:
            raise UnsupportedTrackerLayout(f"tab {echo_text(plan.trend_tab)} row {label} is missing after the draw")
        before_total = _num(before_m.value(plan.trend_tab, before_tc, brow), f"row {label} Total before")
        after_total = _num(m.value(plan.trend_tab, after_tc, arow), f"row {label} Total after")
        added = _num(m.value(plan.trend_tab, nc, arow), f"row {label} new column")
        if after_total - before_total != added:
            raise UnsupportedTrackerLayout(
                f"tab {echo_text(plan.trend_tab)} row {label} Total moved by {after_total - before_total}, "
                f"not its new column's {added}"
            )
    return m


def _finished(final, plan, m=None):
    if m is None:
        m = _Model(_Workbook(_Pkg(final)))
    tab_total = round_money(_total_due_at(m, plan.new_tab, 7))
    trend_total = round_money(_trend_total(m, plan.trend_tab))
    return FinishedDrawTabs(
        workbook=final,
        plan=plan,
        tab_total=tab_total,
        trend_total=trend_total,
        render_invoice=one_tab_render_package(final, plan.new_tab),
        render_trend=one_tab_render_package(final, plan.trend_tab),
    )


@_tracker_boundary
def finish_draw_tabs(pkg: bytes, *, invoice_number: str, draw_date: datetime.date) -> FinishedDrawTabs:
    plan = plan_next_draw(pkg, invoice_number=invoice_number)
    cloned = clone_draw_tab(pkg, plan, draw_date=draw_date)
    final, m = _extend_trend(cloned, plan, draw_date=draw_date)
    return _finished(final, plan, m)


@_tracker_boundary
def render_existing(pkg: bytes, *, invoice_number: str) -> FinishedDrawTabs:
    wb = _Workbook(_Pkg(pkg))
    m = _Model(wb)
    tabs = [
        s.name for s in wb.sheets
        if s.name.startswith("Draw Request") and s.name != TREND_TAB and _text(m, s.name, 5, 10) == invoice_number
    ]
    if not tabs:
        raise DrawTabsError(f"no draw tab has E10 = {echo_text(invoice_number)}")
    if len(tabs) > 1:  # D5: duplicates refuse, never first-wins
        raise UnsupportedTrackerLayout(f"more than one draw tab has E10 = {echo_text(invoice_number)}: {', '.join(echo_text(t) for t in tabs)}")
    tab = tabs[0]
    trend = wb.sheet(TREND_TAB)
    latest = "only the latest finished draw can be re-rendered; the trend has moved on past it"
    if _text(m, TREND_TAB, 5, 10) != invoice_number:
        raise UnsupportedTrackerLayout(latest)
    lookups = [cell for row in m.doc(TREND_TAB).rows.values() for cell in row.cells.values() if _is_vlookup(cell)]
    if not lookups:
        raise UnsupportedTrackerLayout(f"tab {echo_text(TREND_TAB)} has no draw column")
    last = max(cell.col for cell in lookups)
    reads = {_unquote(_VLOOKUP.match(cell.f_text).group("tab")) for cell in lookups if cell.col == last}
    if reads != {tab}:
        raise UnsupportedTrackerLayout(latest)
    split = _label_split(invoice_number)
    if split is None:
        raise UnsupportedTrackerLayout(f"invoice number {echo_text(invoice_number)} has no trailing draw number")
    prefix, digits = split
    plan = DrawTabPlan(
        invoice_number=invoice_number,
        draw_no=int(digits),
        previous_label=prefix + str(int(digits) - 1).zfill(len(digits)),
        source_tab=tab,
        new_tab=tab,
        trend_tab=trend.name,
    )
    final = refresh_cached_values(pkg, [tab, TREND_TAB])
    return _finished(final, plan)
