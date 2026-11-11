"""Restore DrawingML shapes that openpyxl drops from a Box tracker workbook (U-786).

WHY: openpyxl silently drops DrawingML shapes (xdr:sp, xdr:grpSp, xdr:cxnSp) on
save. It keeps pictures and charts only by re-writing them into its own drawing
parts. On the Box-hosted project trackers that meant every DETAILS write deleted
the "INVOICE" title box from each "Draw Request NN" tab.

WHAT: regraft_drawings() runs after the openpyxl save. For every worksheet that
had a drawing in the ORIGINAL package it copies that drawing (plus everything it
references: images, charts, their rels) back into the edited package, byte for
byte, and points the sheet at the copy. openpyxl's own re-written drawing for
that sheet is removed once nothing references it. Only worksheets are grafted
(chartsheets have no </worksheet>), and the drawing restored is the one the
sheet's own <drawing r:id> names.

PER-SHEET ISOLATION: each sheet is planned on its own. A sheet whose graft
cannot be built (dangling drawing relationship, no content type for a part,
or a drawing that depends on parts openpyxl drops) is skipped with a WARNING
box.excel.drawing_regraft_sheet_skipped; the other sheets still graft.

SKIP LIST (R11): drawings whose XML contains compatExt (form controls), a
slicer extension (sle:slicer / slicer) or timeslicer. openpyxl does not write
the parts those depend on, so a restored copy would point at nothing.

VALIDATION BACKSTOP: before returning, every part the graft wrote or rewrote is
parsed (content types, each rels file, each sheet XML it changed), every
internal Target in the rels it wrote must resolve to a package member, and each
copied part's effective content type (Override, else Default by extension) must
equal the original's. Any failure logs box.excel.drawing_regraft_invalid and
returns the edited package unchanged.

FAIL-SOFT: this is cosmetic; DETAILS rows are the money path. Any unexpected
failure logs box.excel.drawing_regraft_failed (with the traceback) and returns
the edited package unchanged, so a graft bug can never block a DETAILS write. A
package with no drawings to restore comes back as the very same object.

COST BOUNDS (U-786): the graft runs inside the Box drain lock, so a crafted
tracker must not make it slow. Members are size-capped before inflating, and a
drawing closure's .rels is capped at _MAX_CLOSURE_RELS_BYTES (sheet .rels are not:
a DETAILS sheet may carry thousands of hyperlinks). Each .rels is parsed at most
once per graft, a failed parse included, and the rels the graft writes are never
re-parsed except by the validation backstop. A drawing's reachable closure is
walked at most once, whether it succeeded or failed, and every walk is charged to
a per-graft visit budget. Every phase whose cost grows with the input checks the
deadline: past _GRAFT_DEADLINE_SECONDS the whole graft fails soft
(drawing_regraft_failed), never one sheet at a time.

ROW ANCHORS: drawings on the DETAILS sheet keep their original row anchors after
insert_rows, as openpyxl already did for pictures.
"""

import logging
import posixpath
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from io import BytesIO
from typing import Dict, List, Optional, Set, Tuple
from xml.sax.saxutils import escape

from integrations.box.excel.business.ooxml import write_package

logger = logging.getLogger(__name__)

_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DRAWING_TYPE = f"{_NS_R}/drawing"

# Relationship types never followed when copying a drawing's dependencies (R10).
_NEVER_FOLLOW = (
    "/worksheet", "/chartsheet", "/table", "/comments",
    "/vmlDrawing", "/officeDocument", "/hyperlink",
)
# Drawing markup whose dependent parts openpyxl drops (R11).
_UNSUPPORTED_DRAWING = (b"compatExt", b"sle:slicer", b"slicer", b"timeslicer")
# Worksheet children that must follow <drawing> (R7).
_AFTER_DRAWING = frozenset({
    "legacyDrawing", "legacyDrawingHF", "drawingHF", "picture", "oleObjects",
    "controls", "webPublishItems", "tableParts", "extLst",
})
_REGRAFT_PREFIX = re.compile(r"^(?:regraft\d+_)+")
# Tokenizer for worksheet XML: comments, PIs and CDATA are skipped; an element
# is (closing?, name, self-closing?). Quoted attribute values may hold '>'.
_XML_TAG = re.compile(
    r"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>"
    r"|<(/?)([\w:.-]+)(?:\s+[^\s=/>]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*\s*(/?)>",
    re.S,
)


from collections.abc import Mapping  # noqa: E402  (U-786 S2: lazy member reads)

_MAX_MEMBER_BYTES = 64 * 1024 * 1024    # U-786 S2: a member larger than this is never inflated
_MAX_READ_BYTES = 256 * 1024 * 1024     # U-786 S2: total inflated per graft
_MAX_DRAWING_PARTS = 500                # U-786 S2: a drawing's reachable closure above this is skipped
_MAX_GRAFT_VISITS = 5000               # U-786 S2c: reachable-part visits per graft (a memoised closure counts once)
_MAX_CLOSURE_RELS_BYTES = 1024 * 1024  # U-786 H: a drawing/chart .rels lists a handful of images; ~7000 is crafted
_GRAFT_DEADLINE_SECONDS = 30.0         # U-786 H: a backstop for crafted input only. The real MR2 tracker
                                       # grafts in 0.5-1.0 s idle, ~2 s on a 3x-oversubscribed CPU; a trip
                                       # writes DETAILS WITHOUT the shapes, and they stay lost (the next
                                       # write's original no longer has them), so it sits far above any
                                       # real tracker.
_now = time.monotonic                  # the graft's clock (a seam for tests)


class _TooLarge(Exception):
    """A member or the total read exceeds a size cap; fail-soft at the top (drawing_regraft_failed)."""


class _OverTime(_TooLarge):
    """The graft passed its deadline; fail-soft for the whole graft, like a size cap."""


class _Budget:
    def __init__(self):
        self.used = 0

    def read(self, zf, name: str) -> bytes:
        size = zf.getinfo(name).file_size  # checked BEFORE inflating anything
        if size > _MAX_MEMBER_BYTES:
            raise _TooLarge(f"member {name[:80]!r} is {size} bytes, over the {_MAX_MEMBER_BYTES}-byte cap")
        if self.used + size > _MAX_READ_BYTES:
            raise _TooLarge(f"members read would exceed {_MAX_READ_BYTES} bytes")
        self.used += size
        return zf.read(name)


class _Members(Mapping):
    """Original package members, inflated on first access and cached (U-786 S2b)."""

    def __init__(self, zf, budget: "_Budget"):
        self._zf = zf
        self._budget = budget
        self._names = {i.filename for i in zf.infolist()}
        self._cache: Dict[str, bytes] = {}

    def __getitem__(self, name):
        if name not in self._cache:
            if name not in self._names:
                raise KeyError(name)
            self._cache[name] = self._budget.read(self._zf, name)
        return self._cache[name]

    def __contains__(self, name) -> bool:  # membership must not inflate
        return name in self._names

    def size(self, name: str) -> int:
        """Inflated size from the zip directory, without inflating."""
        return self._zf.getinfo(name).file_size

    def __iter__(self):
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)


class _Skip(Exception):
    """One sheet cannot be restored; the others still can."""


class _Invalid(Exception):
    """The assembled package failed the validation backstop."""


def regraft_drawings(original: bytes, edited: bytes) -> bytes:
    """Return `edited` with each sheet's ORIGINAL drawing restored byte-for-byte.

    Sheets are matched by name. Sheets without a drawing in `original` are left
    alone; a package with nothing to restore comes back as the same object.
    """
    try:
        return _regraft(original, edited)
    except _Invalid as e:
        logger.warning(
            "box.excel.drawing_regraft_invalid",
            extra={
                "event_name": "box.excel.drawing_regraft_invalid",
                "reason": str(e)[:200],
            },
        )
        return edited
    except Exception as e:  # fail-soft by design: never block a DETAILS write
        logger.warning(
            "box.excel.drawing_regraft_failed",
            exc_info=True,
            extra={
                "event_name": "box.excel.drawing_regraft_failed",
                "error_class": type(e).__name__,
                "reason": str(e)[:200],
            },
        )
        return edited


# ---------------------------------------------------------------- xml helpers

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr_escape(value: str) -> str:
    """Escape an attribute value; \\t \\n \\r must survive as character references."""
    return escape(value, {'"': "&quot;", "\t": "&#9;", "\n": "&#10;", "\r": "&#13;"})


def _parse_rels(data: Optional[bytes]) -> List[Dict[str, str]]:
    if data is None:
        return []
    return [dict(el.attrib) for el in ET.fromstring(data).iter(f"{{{_NS_PKG_REL}}}Relationship")]


def _ser_rels(rels: List[Dict[str, str]]) -> bytes:
    body = "".join(
        "<Relationship " + " ".join(f'{k}="{_attr_escape(v)}"' for k, v in r.items()) + "/>"
        for r in rels
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_NS_PKG_REL}">{body}</Relationships>'
    ).encode("utf-8")


def _is_internal(rel: Dict[str, str]) -> bool:
    return rel.get("TargetMode") != "External"


def _is_package_rel(rel: Dict[str, str]) -> bool:
    """An internal relationship that points at a package part. Hyperlinks are not
    parts: an internal one targets a location ('#Sheet!A1') and an external one
    targets a URL. Neither must be resolved, followed, or required to exist (G1)."""
    if not _is_internal(rel):
        return False
    if rel.get("Target", "").startswith("#"):
        return False
    return not rel.get("Type", "").endswith("/hyperlink")


# ---------------------------------------------------------------- package paths

def _resolve(base: str, target: str) -> str:
    """Resolve a relationship Target (absolute '/x/y' or relative to `base`)."""
    if target.startswith("/"):
        return posixpath.normpath(target[1:])
    if not base:
        return posixpath.normpath(target)
    return posixpath.normpath(posixpath.join(base, target))


def _rels_path(part: str) -> str:
    d, b = posixpath.split(part)
    return posixpath.join(d, "_rels", b + ".rels")


def _rels_base(rels_name: str) -> str:
    parts = rels_name.split("/")
    if len(parts) >= 2 and parts[-2] == "_rels":
        return "/".join(parts[:-2])
    return ""


def _rels(parts: Dict[str, bytes], part: str) -> List[Dict[str, str]]:
    return _parse_rels(parts.get(_rels_path(part)))


def _reachable(parts, root: str, limit: Optional[int] = None, rels_of=None) -> List[str]:
    """`root` plus every internal part reachable through rels, breadth-first."""
    if root not in parts:
        return []
    rels_of = rels_of or (lambda q: _rels(parts, q))
    order = [root]
    seen = {root}
    i = 0
    while i < len(order):
        p = order[i]
        i += 1
        for r in rels_of(p):
            if not _is_package_rel(r) or r.get("Type", "").endswith(_NEVER_FOLLOW):
                continue
            t = _resolve(posixpath.dirname(p), r.get("Target", ""))
            if t in parts and t not in seen:
                seen.add(t)
                order.append(t)
                if limit is not None and len(order) > limit:
                    raise _Skip(f"drawing reaches more than {limit} parts")
    return order


class _Walk:
    """Per-graft memo over the ORIGINAL package: parsed rels and reachable closures, plus the
    total-visit budget (U-786 S2c) and the graft deadline (U-786 H). Shared closures are walked
    once, not once per sheet, and so are failures: a .rels that does not parse, or a closure
    whose walk raised, is remembered and never re-done."""

    def __init__(self, parts):
        self._parts = parts
        self._rels: Dict[str, List[Dict[str, str]]] = {}
        self._bad_rels: Dict[str, str] = {}  # part -> why its .rels cannot be read
        self._closures: Dict[str, List[str]] = {}
        self._failed: Dict[str, str] = {}  # roots whose walk raised -> the skip reason
        self._restorable: Dict[str, Optional[str]] = {}  # drawing -> None (ok) or the skip reason
        self.visits = 0
        self.exhausted = False
        self._deadline = _now() + _GRAFT_DEADLINE_SECONDS

    def check_time(self) -> None:
        if _now() > self._deadline:
            raise _OverTime(f"graft passed its {_GRAFT_DEADLINE_SECONDS:g}-second deadline")

    def rels_of(self, part: str) -> List[Dict[str, str]]:
        self.check_time()
        if part in self._bad_rels:
            raise _Skip(self._bad_rels[part])
        if part not in self._rels:
            try:
                self._rels[part] = _rels(self._parts, part)
            except _TooLarge:
                raise
            except Exception as ex:  # U-786 H: parsed once per graft, failure included
                self._bad_rels[part] = f"{_rels_path(part)[:80]!r} cannot be read ({type(ex).__name__})"
                raise _Skip(self._bad_rels[part]) from None
        return self._rels[part]

    def closure_rels_of(self, part: str) -> List[Dict[str, str]]:
        """rels_of for a part inside a drawing's closure, refusing an oversized .rels before inflating it."""
        rp = _rels_path(part)
        if rp in self._parts:
            size = self._parts.size(rp)
            if size > _MAX_CLOSURE_RELS_BYTES:
                raise _Skip(f"{rp[:80]!r} is {size} bytes, over the {_MAX_CLOSURE_RELS_BYTES}-byte cap for a drawing's rels")
        return self.rels_of(part)

    def reach(self, root: str) -> List[str]:
        if root in self._closures:
            return self._closures[root]
        if root in self._failed:
            raise _Skip(self._failed[root])
        if self.exhausted:
            raise _Skip(f"graft reachable-part budget of {_MAX_GRAFT_VISITS} is spent")
        try:
            order = _reachable(self._parts, root, _MAX_DRAWING_PARTS, self.closure_rels_of)
        except _TooLarge:
            raise
        except Exception as failed:
            # U-786 S2c/H: a walk that raised (over the part limit, or a .rels that cannot be read)
            # is remembered and charged as a full walk, so many sheets over one bad closure
            # exhaust the budget instead of re-walking it each time.
            reason = failed.args[0] if isinstance(failed, _Skip) and failed.args else type(failed).__name__
            self._failed[root] = str(reason)
            self.visits += _MAX_DRAWING_PARTS + 1
            if self.visits > _MAX_GRAFT_VISITS:
                self.exhausted = True
            raise _Skip(self._failed[root]) from None
        self.visits += len(order)
        if self.visits > _MAX_GRAFT_VISITS:
            self.exhausted = True
            raise _Skip(f"graft reachable-part budget of {_MAX_GRAFT_VISITS} is spent")
        self._closures[root] = order
        return order


def _sheet_parts(parts: Dict[str, bytes]) -> Dict[str, str]:
    """Worksheet name (unescaped) -> worksheet part path. Chartsheets are excluded."""
    if "xl/workbook.xml" not in parts:
        return {}
    rels = {r["Id"]: r for r in _parse_rels(parts.get("xl/_rels/workbook.xml.rels")) if "Id" in r}
    out: Dict[str, str] = {}
    for el in ET.fromstring(parts["xl/workbook.xml"]).iter():
        if _local(el.tag) != "sheet":  # any prefix (R13)
            continue
        rel = rels.get(el.get(f"{{{_NS_R}}}id"))
        name = el.get("name")
        if rel is None or name is None or not _is_internal(rel):
            continue
        if not rel.get("Type", "").endswith("/worksheet"):
            continue
        out[name] = _resolve("xl", rel.get("Target", ""))
    return out


def _sheet_drawing_rid(sheet_xml: bytes) -> Tuple[bool, Optional[str]]:
    """(sheet has a <drawing> element, the r:id it names)."""
    el = ET.fromstring(sheet_xml).find(f"{{{_NS_MAIN}}}drawing")
    if el is None:
        return False, None
    return True, el.get(f"{{{_NS_R}}}id")


def _drawing_rel(rels: List[Dict[str, str]], rid: Optional[str]) -> Optional[Dict[str, str]]:
    for r in rels:
        if r.get("Id") == rid and r.get("Type", "").endswith("/drawing") and _is_internal(r):
            return r
    return None


class _Types:
    """[Content_Types].xml as ordered Defaults and Overrides."""

    def __init__(self, data: Optional[bytes]):
        self.defaults: List[Tuple[str, str]] = []
        self.overrides: List[Tuple[str, str]] = []
        self._index = None
        if data is None:
            return
        for el in ET.fromstring(data):
            name = _local(el.tag)
            if name == "Default":
                self.defaults.append((el.get("Extension", ""), el.get("ContentType", "")))
            elif name == "Override":
                self.overrides.append((el.get("PartName", ""), el.get("ContentType", "")))

    def effective(self, part: str) -> Optional[str]:
        """Override (case-insensitive) else Default by extension. A .xml part needs
        an Override: the generic xml Default is wrong for a drawing or chart (R4)."""
        if self._index is None:  # U-786 S2a: one dict, first Override wins (was a scan per part)
            self._index = {}
            for pn, ct in self.overrides:
                self._index.setdefault(pn.lstrip("/").lower(), ct)
        key = part.lower()
        if key in self._index:
            return self._index[key]
        ext = posixpath.basename(part).rsplit(".", 1)[-1].lower() if "." in posixpath.basename(part) else ""
        if not ext or ext == "xml":
            return None
        for e, ct in self.defaults:
            if e.lower() == ext:
                return ct
        return None

    def to_bytes(self) -> bytes:
        body = "".join(
            f'<Default Extension="{_attr_escape(e)}" ContentType="{_attr_escape(c)}"/>'
            for e, c in self.defaults
        ) + "".join(
            f'<Override PartName="{_attr_escape(p)}" ContentType="{_attr_escape(c)}"/>'
            for p, c in self.overrides
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Types xmlns="{_NS_CT}">{body}</Types>'
        ).encode("utf-8")


def _insert_drawing(sheet_xml: str, rid: str) -> str:
    """Insert <drawing r:id/> before the first DIRECT worksheet child from the
    follow-set, else before the worksheet's closing tag (R7). Depth is tracked
    with a tokenizer so nested elements (an extLst inside a cfRule) never match."""
    pos = None
    prefix = ""
    root_close = None
    depth = 0
    for m in _XML_TAG.finditer(sheet_xml):
        text = m.group(0)
        if text.startswith(("<!--", "<?", "<![CDATA[")):
            continue
        name = m.group(2)
        if m.group(1) == "/":
            depth -= 1
            if depth == 0:
                root_close = m.start()
            continue
        local = name.rsplit(":", 1)[-1]
        if depth == 0:
            prefix = name.rpartition(":")[0]
        elif depth == 1 and local in _AFTER_DRAWING:
            pos = m.start()
            break
        if m.group(3) != "/":
            depth += 1
    if pos is None:
        pos = root_close
    if pos is None:
        raise ValueError("worksheet part has no closing root element")
    tag = f"{prefix}:drawing" if prefix else "drawing"
    el = f'<{tag} xmlns:r="{_NS_R}" r:id="{rid}"/>'
    return sheet_xml[:pos] + el + sheet_xml[pos:]


def _fresh_name(part: str, taken: Set[str]) -> str:
    """A part name in the same directory, prefixed 'regraftN_', unused so far.
    Any existing regraft prefixes are stripped first so names do not grow (R3)."""
    d, b = posixpath.split(part)
    b = _REGRAFT_PREFIX.sub("", b)
    i = 1
    while True:
        cand = posixpath.join(d, f"regraft{i}_{b}")
        if cand not in taken and _rels_path(cand) not in taken:
            taken.add(cand)
            return cand
        i += 1


def _warn_skipped(sheet: str, reason: str) -> None:
    logger.warning(
        "box.excel.drawing_regraft_sheet_skipped",
        extra={
            "event_name": "box.excel.drawing_regraft_sheet_skipped",
            "sheet": sheet,
            "reason": reason[:200],
        },
    )


def _check_restorable(parts: Dict[str, bytes], types: _Types, drawing: str, walk: "_Walk") -> None:
    """Raise _Skip unless `drawing` can be restored. The verdict is memoised per drawing on the walk
    (U-786 H): many sheets sharing one large drawing scan it once."""
    if drawing not in walk._restorable:
        try:
            _restorable(parts, types, drawing, walk)
            walk._restorable[drawing] = None
        except _Skip as s:
            walk._restorable[drawing] = s.args[0]
    if walk._restorable[drawing] is not None:
        raise _Skip(walk._restorable[drawing])


def _restorable(parts: Dict[str, bytes], types: _Types, drawing: str, walk: "_Walk") -> None:
    if drawing not in parts:
        raise _Skip("drawing part is missing from the original package")
    for marker in _UNSUPPORTED_DRAWING:
        if marker in parts[drawing]:
            raise _Skip(f"drawing uses {marker.decode()}, which openpyxl drops")
    if types.effective(drawing) is None:
        raise _Skip(f"no content type in original for {drawing}")
    for p in walk.reach(drawing):
        if types.effective(p) is None:
            raise _Skip(f"no content type in original for {p}")


def _validate(
    e: Dict[str, bytes],
    original_types: _Types,
    copies: Dict[str, str],
    written_rels: Set[str],
    changed_sheets: List[str],
    check_time,
) -> None:
    """Backstop: raise _Invalid unless the package the graft wrote is consistent. Each part is parsed
    once (U-786 H): a written .rels's tree is both the well-formedness check and its relationships."""
    trees = {}
    for name in ["[Content_Types].xml", *sorted(written_rels), *changed_sheets]:
        check_time()
        try:
            trees[name] = ET.fromstring(e[name])
        except ET.ParseError as ex:
            raise _Invalid(f"{name} is not well-formed XML: {ex}") from ex
    for rels_name in written_rels:
        base = _rels_base(rels_name)
        for el in trees[rels_name].iter(f"{{{_NS_PKG_REL}}}Relationship"):
            r = dict(el.attrib)
            if not _is_package_rel(r):
                continue
            target = _resolve(base, r.get("Target", ""))
            if target not in e:
                raise _Invalid(f"{rels_name} targets missing part {target}")
    new_types = _Types(e["[Content_Types].xml"])
    for old, new in copies.items():
        want = original_types.effective(old)
        got = new_types.effective(new)
        if want is None or got != want:
            raise _Invalid(f"content type of {new} is {got!r}, original {old} is {want!r}")


# ---------------------------------------------------------------- the graft

def _regraft(original: bytes, edited: bytes) -> bytes:
    zo = zipfile.ZipFile(BytesIO(original))
    budget = _Budget()
    o = _Members(zo, budget)  # U-786 S2b: an original member is inflated only when a step needs it
    walk = _Walk(o)  # U-786 S2c: rels and closures of the original, memoised for this graft
    # Cheap exit before anything is inflated: no original worksheet has a drawing relationship.
    o_sheets = _sheet_parts(o)
    if not any(
        any(r.get("Type", "").endswith("/drawing") for r in walk.rels_of(p)) for p in o_sheets.values()
    ):
        return edited
    ze = zipfile.ZipFile(BytesIO(edited))
    e: Dict[str, bytes] = OrderedDict((n, budget.read(ze, n)) for n in ze.namelist())
    infos = {i.filename: i for i in ze.infolist()}
    o_types = _Types(o.get("[Content_Types].xml"))

    # 0. Plan each sheet on its own: a sheet that cannot be restored is skipped (R9).
    e_sheets = _sheet_parts(e)
    plans: List[Tuple[str, str, str]] = []  # (sheet name, original drawing, edited sheet part)
    skipped = 0
    for name, o_part in o_sheets.items():
        if name not in e_sheets:
            continue
        walk.check_time()  # U-786 H: each sheet's parse is bounded only by the size caps
        e_part = e_sheets[name]
        try:
            has, rid = _sheet_drawing_rid(o[o_part])
            if not has:
                continue  # nothing to restore for this sheet
            o_rel = _drawing_rel(walk.rels_of(o_part), rid)
            if o_rel is None:
                raise _Skip("sheet's <drawing r:id> names no drawing relationship")
            o_drawing = _resolve(posixpath.dirname(o_part), o_rel.get("Target", ""))
            _check_restorable(o, o_types, o_drawing, walk)
            e_has, e_rid = _sheet_drawing_rid(e[e_part])
            if e_has and _drawing_rel(_rels(e, e_part), e_rid) is None:
                raise _Skip("edited sheet's <drawing r:id> names no drawing relationship")
            plans.append((name, o_drawing, e_part))
        except _Skip as s:
            skipped += 1
            _warn_skipped(name, s.args[0])
        except _TooLarge:
            raise  # U-786 S2b: a size cap is fail-soft for the whole graft, not one sheet
        except Exception as ex:
            skipped += 1
            _warn_skipped(name, str(ex) or type(ex).__name__)
    if not plans:
        return edited

    # 1. Copy every part reachable from each planned drawing under a fresh name.
    taken: Set[str] = set(e)
    mapping: Dict[str, str] = {}
    for _, o_drawing, _ in plans:
        for p in walk.reach(o_drawing):
            if p not in mapping:
                mapping[p] = _fresh_name(p, taken)

    written_rels: Dict[str, List[Dict[str, str]]] = {}  # rels the graft wrote -> what it wrote (never re-parsed)
    copies: Dict[str, str] = {}
    for p, np in mapping.items():  # walk.rels_of below checks the deadline each iteration
        e[np] = o[p]
        copies[p] = np
        rels = walk.rels_of(p)
        if rels:
            rewritten = []
            for r in rels:
                r = dict(r)
                if _is_package_rel(r):
                    t = _resolve(posixpath.dirname(p), r.get("Target", ""))
                    if t in mapping:
                        r["Target"] = "/" + mapping[t]
                rewritten.append(r)
            e[_rels_path(np)] = _ser_rels(rewritten)
            written_rels[_rels_path(np)] = rewritten

    # 2. Point each edited worksheet at its restored drawing.
    dead_candidates: Set[str] = set()
    changed_sheets: List[str] = []
    for _, o_drawing, e_part in plans:
        walk.check_time()
        new_drawing = mapping[o_drawing]
        srp = _rels_path(e_part)
        rels = _rels(e, e_part)
        has, rid = _sheet_drawing_rid(e[e_part])
        if has:
            # openpyxl re-wrote the sheet's pictures into its own drawing: retarget
            # the relationship that element names, and prune the old drawing later.
            rel = _drawing_rel(rels, rid)
            dead_candidates.update(_reachable(e, _resolve(posixpath.dirname(e_part), rel.get("Target", ""))))
            rel["Target"] = "/" + new_drawing
        else:
            # openpyxl wrote no drawing (shapes only): add the relationship and the
            # <drawing> element at its schema position.
            ids = {r.get("Id") for r in rels}
            n = 1
            while f"rId{n}" in ids:
                n += 1
            rid = f"rId{n}"
            rels.append({"Id": rid, "Type": _DRAWING_TYPE, "Target": "/" + new_drawing})
            e[e_part] = _insert_drawing(e[e_part].decode("utf-8"), rid).encode("utf-8")
            changed_sheets.append(e_part)
        e[srp] = _ser_rels(rels)
        written_rels[srp] = rels

    # 3. Prune openpyxl's now-unreferenced drawing parts (and what only they used). Each .rels in the
    # package is read once (U-786 H): the ones the graft wrote from what it wrote, the rest parsed.
    targets: Dict[str, Set[str]] = {}
    for n in e:
        if not n.endswith(".rels"):
            continue
        walk.check_time()
        base = _rels_base(n)
        rels = written_rels[n] if n in written_rels else _parse_rels(e[n])
        targets[n] = {_resolve(base, r.get("Target", "")) for r in rels if _is_package_rel(r)}
    dead: Set[str] = set()
    while True:
        dead_rels = {_rels_path(d) for d in dead}
        refs: Set[str] = set()
        for n, t in targets.items():
            if n not in dead_rels:
                refs |= t
        newly = {c for c in dead_candidates if c in e and c not in dead and c not in refs}
        if not newly:
            break
        dead |= newly
    for d in dead:
        e.pop(d, None)
        e.pop(_rels_path(d), None)

    # 4. Content types: drop overrides for pruned parts, add them for the copies.
    e_types = _Types(e.get("[Content_Types].xml"))
    dead_lower = {d.lower() for d in dead}
    e_types.overrides = [
        (pn, ct) for pn, ct in e_types.overrides if pn.lstrip("/").lower() not in dead_lower
    ]
    for p, np in copies.items():
        e_types.overrides.append(("/" + np, o_types.effective(p) or ""))
    e["[Content_Types].xml"] = e_types.to_bytes()

    # 5. Backstop, then write the package: edited member order, new members last.
    _validate(e, o_types, copies, set(written_rels), changed_sheets, walk.check_time)
    package = write_package(e.items(), infos)
    logger.info(
        "box.excel.drawing_regraft_applied",
        extra={
            "event_name": "box.excel.drawing_regraft_applied",
            "restored": len(plans),
            "skipped": skipped,
        },
    )
    return package
