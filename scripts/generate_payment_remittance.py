#!/usr/bin/env python3
"""Generate Payment Remittance (a.k.a. check-stub) PDFs from a QBO BillPayment.

For a given payment number (QBO BillPayment.DocNumber), QBO may hold several
BillPayment records — one per vendor paid in that ACH/check batch. This script
queries them live (read-only), and for EACH vendor renders a one-page remittance
PDF (reportlab, house style mirroring scripts/_clr_remittance.py) listing the
bills paid (Bill # / Date / Amount) with a total, then optionally uploads each
PDF to the Box check-stubs folder for the year of the payment's TxnDate:

    999 - Accounting / 02 - Accounts Payable / 535 - Rogers Build - Check Stubs / <year>

Filename convention (matches the 268 existing files in that folder):
    {TxnDate yyyy.mm.dd} - BILL PAYMENT - {DocNumber} - {VendorRef.Name} - ${TotalAmt:,.2f}.pdf

Usage:
    # generate locally only (no Box writes), into ./_remittance_out
    .venv/bin/python scripts/generate_payment_remittance.py 9361486213

    # generate AND upload to Box (prod write — requires the env flags)
    ALLOW_BOX_WRITES=true BOX_AS_USER_ID=31760447449 \
        .venv/bin/python scripts/generate_payment_remittance.py 9361486213 --upload

    # file to BOTH surfaces (Box + the SharePoint mirror) in one run
    ALLOW_BOX_WRITES=true ALLOW_MS_WRITES=true BOX_AS_USER_ID=31760447449 \
        .venv/bin/python scripts/generate_payment_remittance.py 9361486213 \
        --upload --upload-sharepoint

    # reconcile a year: list what SharePoint is missing (dry run), then upload
    .venv/bin/python scripts/generate_payment_remittance.py --backfill-sharepoint 2026
    ALLOW_MS_WRITES=true BOX_AS_USER_ID=31760447449 \
        .venv/bin/python scripts/generate_payment_remittance.py \
        --backfill-sharepoint 2026 --apply

`--upload` stays Box-ONLY on purpose: every historical invocation in SESSION_NOTES
assumes that meaning, so widening it would silently turn replayed commands into
double-writes. Ask for SharePoint explicitly with --upload-sharepoint.

Read-only against QBO and the local DB. External writes are the Box upload
(--upload + ALLOW_BOX_WRITES=true) and the SharePoint upload (--upload-sharepoint
or --backfill-sharepoint --apply, + ALLOW_MS_WRITES=true); all default off.
"""
# Standard Library
import argparse
import base64
import html
import io
import os
import re
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Third-party
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Local
from scripts.sync_helper import assert_cli_system_admin
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.box.base.client import BoxHttpClient
from integrations.box.base.errors import BoxConflictError
from integrations.ms.sharepoint.external import client as sp_client
from entities.vendor.business.service import VendorService
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only

# Box anchor: the '999 - Accounting' folder (stable id, found via the template probe).
BOX_999_ACCOUNTING_ID = "388262075849"
BOX_PATH_SEGMENTS = ["02 - Accounts Payable", "535 - Rogers Build - Check Stubs"]
COMPANY_FALLBACK = "Rogers Build, Inc."
METHOD_LABEL = "ACH"  # business pays via ACH even though QBO books PayType=Check
REMITTANCE_BCC = "invoice@rogersbuild.com"  # always BCC'd on vendor remittance drafts

# SharePoint mirror of the Box check-stubs tree. Same filenames, same year folders.
# Anchor is the drive id (stable); every folder below it is walked BY NAME so a
# reorganisation fails loudly here instead of writing into the wrong folder.
SP_DRIVE_ID = "b!ORGYF05isEixyjaiGrjpY8og4Bos92VGmN9aSns5dDZlsBbazGm1R72YjQfn3bmj"
SP_PATH_SEGMENTS = ["General", "999 - Accounting", "02 - Accounts Payable",
                    "535 - Rogers Build - Check Stubs"]
CENTS = Decimal("0.01")


# ---------------------------------------------------------------------------- #
# QBO (read-only)
# ---------------------------------------------------------------------------- #
def _qbo_query(client: QboHttpClient, query: str) -> Dict[str, Any]:
    data = client.get("query", params={"query": query}, operation_name="remittance.query")
    return (data or {}).get("QueryResponse", {}) if isinstance(data, dict) else {}


def _money(value: Any) -> Decimal:
    """Decimal-safe currency parse (never float())."""
    return Decimal(str(value or 0)).quantize(CENTS, rounding=ROUND_HALF_UP)


def fetch_payment_batch(doc_number: str) -> Dict[str, Any]:
    """Return {payer, payments:[{vendor, doc_number, txn_date, total, lines:[...]}], realm_id}."""
    assert_cli_system_admin()
    auths = QboAuthService().read_all()
    if not auths:
        raise SystemExit("No QBO auth found — connect QuickBooks first.")
    realm_id = auths[0].realm_id

    safe_doc = doc_number.replace("'", "''")
    with QboHttpClient(realm_id=realm_id, minor_version=65) as client:
        ci = _qbo_query(client, "select * from CompanyInfo").get("CompanyInfo", [])
        payer = (ci[0].get("LegalName") or ci[0].get("CompanyName")) if ci else COMPANY_FALLBACK

        payments_raw = _qbo_query(
            client, f"select * from BillPayment where DocNumber = '{safe_doc}'"
        ).get("BillPayment", [])
        if not payments_raw:
            raise SystemExit(f"No BillPayment found with DocNumber='{doc_number}'.")

        # Resolve every linked Bill's human number + date in one query.
        bill_ids = sorted({
            str(lt["TxnId"])
            for p in payments_raw for line in (p.get("Line") or [])
            for lt in (line.get("LinkedTxn") or [])
            if lt.get("TxnType") == "Bill" and lt.get("TxnId")
        })
        bill_by_id: Dict[str, Dict[str, Any]] = {}
        if bill_ids:
            id_list = ",".join(f"'{b}'" for b in bill_ids)
            for b in _qbo_query(
                client, f"select Id, DocNumber, TxnDate, TotalAmt from Bill where Id in ({id_list})"
            ).get("Bill", []):
                bill_by_id[str(b["Id"])] = b

        # Resolve linked VendorCredit numbers + dates (applied credits reduce the payment;
        # net = sum(bills) - sum(credits) = BillPayment.TotalAmt).
        credit_ids = sorted({
            str(lt["TxnId"])
            for p in payments_raw for line in (p.get("Line") or [])
            for lt in (line.get("LinkedTxn") or [])
            if lt.get("TxnType") == "VendorCredit" and lt.get("TxnId")
        })
        credit_by_id: Dict[str, Dict[str, Any]] = {}
        if credit_ids:
            cid_list = ",".join(f"'{c}'" for c in credit_ids)
            for cr in _qbo_query(
                client, f"select Id, DocNumber, TxnDate, TotalAmt from VendorCredit where Id in ({cid_list})"
            ).get("VendorCredit", []):
                credit_by_id[str(cr["Id"])] = cr

        # Each vendor's QBO PrimaryEmailAddr (fallback email source after local Contacts).
        vendor_qbo_ids = sorted({str(p["VendorRef"]["value"]) for p in payments_raw})
        vendor_email_by_id: Dict[str, Optional[str]] = {}
        if vendor_qbo_ids:
            vid_list = ",".join(f"'{v}'" for v in vendor_qbo_ids)
            for v in _qbo_query(
                client, f"select Id, PrimaryEmailAddr from Vendor where Id in ({vid_list})"
            ).get("Vendor", []):
                vendor_email_by_id[str(v["Id"])] = (v.get("PrimaryEmailAddr") or {}).get("Address")

    payments = []
    for p in payments_raw:
        lines = []
        for line in p.get("Line") or []:
            amt = _money(line.get("Amount"))
            for lt in line.get("LinkedTxn") or []:
                ttype = lt.get("TxnType")
                tid = str(lt.get("TxnId"))
                if ttype == "Bill":
                    b = bill_by_id.get(tid, {})
                    lines.append({
                        "kind": "bill",
                        "bill_number": str(b.get("DocNumber") or tid),
                        "bill_date": b.get("TxnDate"),
                        "amount": amt,
                    })
                elif ttype == "VendorCredit":
                    cr = credit_by_id.get(tid, {})
                    docn = str(cr.get("DocNumber") or tid)
                    # Label as a credit memo, but don't double up if the number already says "CM"/"CR".
                    label = docn if docn.upper().startswith(("CM", "CR")) else f"CM {docn}"
                    lines.append({
                        "kind": "credit",
                        "bill_number": label,
                        "bill_date": cr.get("TxnDate"),
                        "amount": -amt,  # credit reduces the net paid
                    })
        vqid = str(p["VendorRef"]["value"])
        payments.append({
            "vendor": p["VendorRef"]["name"],
            "vendor_qbo_id": vqid,
            "qbo_email": vendor_email_by_id.get(vqid),
            "doc_number": str(p.get("DocNumber")),
            "txn_date": p.get("TxnDate"),
            "total": _money(p.get("TotalAmt")),
            "lines": lines,
        })
    return {"payer": payer, "payments": payments, "realm_id": realm_id}


# ---------------------------------------------------------------------------- #
# Formatting
# ---------------------------------------------------------------------------- #
def _date_dots(iso: Optional[str]) -> str:
    """'YYYY-MM-DD' -> 'YYYY.MM.DD' (filename)."""
    return (iso or "").replace("-", ".")


def _date_short(iso: Optional[str]) -> str:
    """'YYYY-MM-DD' -> 'MM-DD-YY' (PDF body, mirrors the .xlsx template)."""
    if not iso:
        return ""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%m-%d-%y")
    except ValueError:
        return iso


def _date_long(iso: Optional[str]) -> str:
    """'YYYY-MM-DD' -> 'June 12, 2026' (email body, vendor-facing)."""
    if not iso:
        return ""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %-d, %Y")
    except ValueError:
        return iso


def _usd(d: Decimal) -> str:
    return f"${d:,.2f}"


def _money_cell(d: Decimal) -> str:
    """Currency for a PDF table cell; negatives (credits) in accounting parens: $(8,203.81)."""
    return f"$({abs(d):,.2f})" if d < 0 else f"${d:,.2f}"


def _bill_sort_key(bill_number: str):
    """Natural-sort key for the Bill column: numeric runs compared as ints so
    99 < 100, while date-style ('2026.05.31') and alphanumeric ('K72551')
    numbers stay deterministic. re.split on (\\d+) alternates non-digit/digit
    chunks, so at any position both keys are the same type (no int/str clash)."""
    return [int(c) if c.isdigit() else c.lower()
            for c in re.split(r"(\d+)", str(bill_number))]


def build_filename(payment: Dict[str, Any]) -> str:
    name = (
        f"{_date_dots(payment['txn_date'])} - BILL PAYMENT - {payment['doc_number']} - "
        f"{payment['vendor']} - {_usd(payment['total'])}.pdf"
    )
    # Box forbids slashes in item names; nothing else in these tokens is illegal.
    return name.replace("/", "-").replace("\\", "-").strip()


# ---------------------------------------------------------------------------- #
# PDF (reportlab — house style from scripts/_clr_remittance.py)
# ---------------------------------------------------------------------------- #
def render_pdf(payer: str, payment: Dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.6 * inch, leftMargin=0.6 * inch,
        rightMargin=0.5 * inch, bottomMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    co = ParagraphStyle("co", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11)
    ti = ParagraphStyle("ti", parent=styles["Normal"], fontName="Helvetica", fontSize=10, spaceAfter=10)
    el: List[Any] = [Paragraph(payer, co), Paragraph("Payment Remittance", ti)]

    meta = [
        ["Vendor", payment["vendor"]],
        [METHOD_LABEL, payment["doc_number"]],
        ["Date", _date_short(payment["txn_date"])],
    ]
    mt = Table(meta, colWidths=[1.0 * inch, 4.5 * inch])
    mt.hAlign = "LEFT"
    mt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    el += [mt, Spacer(1, 14)]

    data: List[List[str]] = [["Bill", "Date", "Amount"]]
    bills = [ln for ln in payment["lines"] if ln.get("kind") != "credit"]
    credits = [ln for ln in payment["lines"] if ln.get("kind") == "credit"]
    for ln in sorted(bills, key=lambda l: _bill_sort_key(l["bill_number"])):
        data.append([ln["bill_number"], _date_short(ln["bill_date"]), _money_cell(ln["amount"])])
    for ln in sorted(credits, key=lambda l: _bill_sort_key(l["bill_number"])):
        data.append([ln["bill_number"], _date_short(ln["bill_date"]), _money_cell(ln["amount"])])
    data.append(["", "Total", _money_cell(payment["total"])])
    n = len(data)
    t = Table(data, colWidths=[1.6 * inch, 1.3 * inch, 1.6 * inch])
    t.hAlign = "LEFT"
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.black),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),    # amount column
        ("ALIGN", (0, 1), (0, -1), "RIGHT"),    # bill numbers (data rows)
        ("LINEABOVE", (1, n - 1), (2, n - 1), 0.7, colors.black),
        ("FONTNAME", (1, n - 1), (2, n - 1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    el.append(t)
    doc.build(el)
    return buf.getvalue()


# ---------------------------------------------------------------------------- #
# Box (find-or-create year folder; upload-or-version)
# ---------------------------------------------------------------------------- #
def _box_items(client: BoxHttpClient, folder_id: str) -> List[Dict[str, Any]]:
    out, off = [], 0
    while True:
        r = client.get(
            f"folders/{folder_id}/items",
            params={"fields": "id,name,type", "limit": 1000, "offset": off},
            operation_name="remittance.box.items",
        )
        e = r.get("entries", [])
        out += e
        off += len(e)
        if not e or off >= r.get("total_count", len(out)):
            break
    return out


def _conflict_id(err: BoxConflictError) -> Optional[str]:
    conf = (err.context_info or {}).get("conflicts")
    if isinstance(conf, dict):
        return conf.get("id")
    if isinstance(conf, list) and conf:
        return conf[0].get("id")
    return None


def resolve_year_folder(client: BoxHttpClient, year: str, create: bool) -> str:
    cur = BOX_999_ACCOUNTING_ID
    for seg in BOX_PATH_SEGMENTS:
        match = next((k for k in _box_items(client, cur)
                      if k["type"] == "folder" and k["name"] == seg), None)
        if not match:
            raise SystemExit(f"Box path segment not found: {seg!r}")
        cur = match["id"]
    yr = next((k for k in _box_items(client, cur)
               if k["type"] == "folder" and k["name"] == year), None)
    if yr:
        return yr["id"]
    if not create:
        raise SystemExit(f"Year folder {year!r} missing and create disabled.")
    try:
        return client.post("folders", json_body={"name": year, "parent": {"id": cur}},
                           operation_name="remittance.box.mkdir")["id"]
    except BoxConflictError as e:  # race: someone created it first
        cid = _conflict_id(e)
        if cid:
            return cid
        raise


def upload_or_version(client: BoxHttpClient, folder_id: str, filename: str, data: bytes) -> str:
    try:
        client.upload_file(folder_id, filename, data, content_type="application/pdf",
                           operation_name="remittance.box.upload")
        return "created"
    except BoxConflictError as e:
        existing = _conflict_id(e)
        if not existing:
            raise
        client.upload_file_version(existing, filename, data, content_type="application/pdf",
                                  operation_name="remittance.box.version")
        return "versioned"


def parse_remittance_filename(name: str) -> Optional[Dict[str, str]]:
    """Split '{date} - BILL PAYMENT - {doc} - {vendor} - {total}.pdf' into its parts.

    Returns None when the name doesn't follow the convention. Joins on ' - ' so a
    vendor that itself contains ' - ' survives (none do today, but be safe).
    Sole parser for this convention — every caller keys off this one shape.
    """
    if " - BILL PAYMENT - " not in name:
        return None
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    parts = stem.split(" - ")
    if len(parts) < 5:  # [date, 'BILL PAYMENT', doc, *vendor, total]
        return None
    return {
        "date": parts[0],
        "doc_number": parts[2],
        "vendor": " - ".join(parts[3:-1]),
        "total": parts[-1],
    }


def _vendor_from_filename(name: str) -> Optional[str]:
    """Vendor segment of a convention-named remittance file, else None."""
    parsed = parse_remittance_filename(name)
    return parsed["vendor"] if parsed else None


def _norm_vendor(vendor: str) -> str:
    """Lowercase, alphanumeric-only. Punctuation and case ONLY.

    Collapses 'B. Christopher & Co., LLC' and 'B. Christopher & Co, LLC' onto one
    string, and nothing more. An earlier version also dropped a leading 'the',
    which quietly made 'Acme' equal 'The Acme' — a word-level judgement that
    belongs in the reviewed alias map, not in normalization. Every difference
    beyond punctuation and case must be listed in `SP_VENDOR_ALIASES` to suppress
    an upload, so the guarantee this function supports stays literally true.
    """
    return "".join(ch for ch in vendor.lower() if ch.isalnum())


# Human-reviewed equivalences between the pre-script SharePoint vendor names and
# the QBO names this script writes. Derived 2026-09-15 by listing every Box file
# that shares (payment number, total) with a SharePoint file whose normalized
# vendor differs, then confirming each pair by eye. EXPLICIT on purpose: an
# earlier draft matched on "one name is a prefix of the other", which also makes
# 'Acme' equal 'Acme Construction' — a rule that can declare a remittance already
# filed when it is not, and silently drop a payment document. Nothing but a pair
# listed here suppresses an upload; every other difference uploads.
SP_VENDOR_ALIASES = frozenset({
    frozenset({"cobrallc", "cobra"}),
    frozenset({"fergusonenterprisesllc", "ferguson"}),
    frozenset({"garmanengineeringllc", "garmanengineering"}),
    frozenset({"hartleybotanicinc", "hartleybotanic"}),
    frozenset({"idealmillworkhardware", "idealmillwork"}),
    frozenset({"jonesstoneco", "jonesstone"}),
    frozenset({"mobilematerialsnashville", "mobilematerials"}),
    frozenset({"thestructurecompanyofnashvillellc", "structurecompanyofnashville"}),
})


def same_remittance(name_a: str, name_b: str) -> bool:
    """True when two filenames denote the SAME remittance document.

    Payment number and total must match exactly, and the normalized vendor must
    either be identical or be a reviewed pair in `SP_VENDOR_ALIASES`.

    The bias is deliberate and one-directional: an unrecognised difference means
    "not the same", so the file uploads. A false duplicate is visible and
    deletable; a document that is never filed is neither. That is why there is no
    fuzzy fallback here — two different vendors in one payment can be paid the
    identical amount (payment 2502356724 pays Elmer Cordova and Wilmer Diaz
    $3,380.00 each, on the same date), so any rule loose enough to join short and
    long spellings by shape alone is also loose enough to merge two real vendors.

    The DATE is not compared: the same remittance is sometimes filed under a
    different date (payment 9361486213's B. Christopher pair, 06.12 vs 06.18).
    """
    a, b = parse_remittance_filename(name_a), parse_remittance_filename(name_b)
    if not a or not b:
        return False
    if (a["doc_number"], a["total"]) != (b["doc_number"], b["total"]):
        return False
    va, vb = _norm_vendor(a["vendor"]), _norm_vendor(b["vendor"])
    return va == vb or frozenset({va, vb}) in SP_VENDOR_ALIASES


def needs_sharepoint_upload(filename: str, existing_names: Iterable[str]) -> bool:
    """True when no file already in the destination is this same remittance.

    Exact filename match wins first; otherwise `same_remittance` decides. A name
    that doesn't parse falls back to exact-name comparison only (never fuzzy).
    """
    existing = list(existing_names)
    if filename in existing:
        return False
    if parse_remittance_filename(filename) is None:
        return True
    return not any(same_remittance(filename, n) for n in existing)


def find_possible_twins(candidates: Iterable[str],
                        existing_names: Iterable[str]) -> List[Tuple[str, str]]:
    """(candidate, existing) pairs sharing payment number + total but NOT vendor.

    These WILL be uploaded — refusing to file a document on an amount collision is
    the worse error — but each pair is worth a human glance, because the shape
    covers both the benign case (two vendors genuinely paid the same amount) and a
    vendor spelled differently enough to be missing from `SP_VENDOR_ALIASES`, which
    would make the upload a duplicate.
    """
    by_doc_total: Dict[Tuple[str, str], List[str]] = {}
    for name in existing_names:
        parsed = parse_remittance_filename(name)
        if parsed:
            by_doc_total.setdefault((parsed["doc_number"], parsed["total"]), []).append(name)
    pairs: List[Tuple[str, str]] = []
    for cand in candidates:
        parsed = parse_remittance_filename(cand)
        if not parsed:
            continue
        for other in by_doc_total.get((parsed["doc_number"], parsed["total"]), []):
            if not same_remittance(cand, other):
                pairs.append((cand, other))
    return pairs


# ---------------------------------------------------------------------------- #
# SharePoint (mirror of the Box check-stubs tree)
# ---------------------------------------------------------------------------- #
def _sp_file_names(drive_id: str, folder_id: str) -> List[str]:
    """Names of the FILES in a folder.

    Folders are excluded deliberately: a folder that happens to share a PDF's name
    would otherwise satisfy the exact-name dedupe and suppress the upload, leaving
    the document unfiled.
    """
    return [i["name"] for i in _sp_children(drive_id, folder_id)
            if i.get("item_type") == "file" and i.get("name")]


def _sp_children(drive_id: str, item_id: Optional[str]) -> List[Dict[str, Any]]:
    """Children of a drive folder (root when item_id is None).

    Raises on a non-200. The client swallows Graph errors into an EMPTY `items`
    list, which is indistinguishable from a genuinely empty folder — and an empty
    listing read as 'nothing is there yet' would defeat every dedupe check below
    and duplicate the whole folder. Absence of evidence is not evidence here.
    """
    res = (sp_client.list_drive_root_children(drive_id) if item_id is None
           else sp_client.list_drive_item_children(drive_id, item_id))
    if res.get("status_code") != 200:
        raise SystemExit(f"SharePoint listing failed ({res.get('status_code')}): {res.get('message')}")
    if res.get("truncated"):
        # A partial listing arrives as a normal 200. Treating it as the whole
        # folder would let dedupe miss a file that IS there, and the Graph path
        # PUT replaces rather than fails — so stop instead of guessing.
        raise SystemExit("SharePoint listing was truncated (pagination cap or repeated "
                         "nextLink) — refusing to dedupe against a partial folder listing.")
    return res.get("items") or []


def resolve_sp_year_folder(drive_id: str, year: str, create: bool) -> str:
    """Walk to '<segments>/<year>' by name, optionally creating the year folder."""
    cur: Optional[str] = None
    for seg in SP_PATH_SEGMENTS:
        match = next((k for k in _sp_children(drive_id, cur)
                      if k.get("item_type") == "folder" and k.get("name") == seg), None)
        if not match:
            raise SystemExit(f"SharePoint path segment not found: {seg!r}")
        cur = match["item_id"]
    yr = next((k for k in _sp_children(drive_id, cur)
               if k.get("item_type") == "folder" and k.get("name") == year), None)
    if yr:
        return yr["item_id"]
    if not create:
        raise SystemExit(f"SharePoint year folder {year!r} missing and create disabled.")
    res = sp_client.create_folder(drive_id, cur, year)
    if res.get("status_code") not in (200, 201) or not res.get("item"):
        raise SystemExit(f"Could not create SharePoint year folder {year!r}: {res.get('message')}")
    return res["item"]["item_id"]


def sp_folder_for_year(cache: Dict[str, str], drive_id: str, year: str) -> str:
    """Year folder id, memoised PER YEAR.

    One payment number can return BillPayments with different TxnDates (payment
    8280187478 carries both 2026.03.20 and 2026.03.23), so a single cached folder
    would file every later vendor into the first vendor's year while logging its
    own — a misfiled financial document that looks correct in the log.
    """
    if year not in cache:
        cache[year] = resolve_sp_year_folder(drive_id, year, create=True)
    return cache[year]


def upload_to_sharepoint(drive_id: str, folder_id: str, filename: str, data: bytes) -> str:
    """Upload one remittance PDF. Gated by ALLOW_MS_WRITES inside the Graph client."""
    if len(data) >= 4 * 1024 * 1024:  # simple PUT ceiling; remittances are ~2-3KB
        raise SystemExit(f"{filename!r} is {len(data)} bytes — too large for a simple upload.")
    res = sp_client.upload_small_file(drive_id, folder_id, filename, data,
                                      content_type="application/pdf")
    if res.get("status_code") not in (200, 201):
        raise SystemExit(f"SharePoint upload failed for {filename!r}: {res.get('message')}")
    return "uploaded"


def find_existing_for_payment(client: BoxHttpClient, folder_id: str,
                              doc_number: str, vendor: str) -> Optional[Dict[str, Any]]:
    """Match an existing file on (doc#, vendor), ignoring date/punctuation differences.

    The vendor is parsed from the filename and compared EXACTLY (normalized) rather
    than as a substring — otherwise 'Weston Parker' would spuriously match a
    'Weston Parker (Expense)' file (and vice versa) for the same payment.
    """
    def norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())
    vtoken = norm(vendor)
    for it in _box_items(client, folder_id):
        if it["type"] != "file":
            continue
        nm = it["name"]
        if doc_number not in nm:
            continue
        fv = _vendor_from_filename(nm)
        if fv is not None and norm(fv) == vtoken:
            return it
    return None


def extract_pdf_text(data: bytes) -> str:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages)


# ---------------------------------------------------------------------------- #
# Vendor email resolution + draft (MS Graph, into invoice@rogersbuild.com Drafts)
# ---------------------------------------------------------------------------- #
# U-313: dbo-first -> verify_identity_dbo_only, no legacy fallback left
# (mirrors U-284v's cross-family resolvers, now all repointed the same way —
# BillBillConnector._get_vendor_public_id (pull) + _get_qbo_vendor_ref
# (push), PurchaseExpenseConnector._get_vendor_public_id,
# VendorCreditBillCreditConnector._get_vendor_public_id,
# ExpenseCodingItemService._resolve_vendor_id, and this script's sibling
# copy in backfill_qbo_bills.py — see TODO.md's U-005[reuse] breadcrumb).
# Hand-copied deliberately per that unit's precedent, not extracted.
def resolve_local_vendor_id(
    qbo_vendor_id: str,
    realm_id: Optional[str],
    *,
    vendor_service=None,
) -> Optional[int]:
    """Resolve a QBO vendor id -> local dbo.Vendor.Id, or None if unresolvable."""
    vendor_service = vendor_service or VendorService()

    direct_vendor = vendor_service.read_by_qbo_identity(qbo_vendor_id, realm_id)
    if direct_vendor:
        verified_qbo_id = verify_identity_dbo_only(
            direct_vendor,
            read_direct_by_qbo_identity=vendor_service.read_by_qbo_identity,
        )
        if verified_qbo_id:
            return direct_vendor.id

    return None


def local_vendor_and_contact_emails(qbo_vendor_id: str, realm_id: Optional[str] = None):
    """Map a QBO vendor id -> (local Vendor.Id, [existing Contact emails])."""
    local_id = resolve_local_vendor_id(qbo_vendor_id, realm_id)
    if local_id is None:
        return None, []
    from entities.contact.business.service import ContactService
    contacts = ContactService().read_by_vendor_id(local_id)
    emails = sorted({c.email.strip() for c in contacts if c.email and c.email.strip()})
    return local_id, emails


def backfill_contact_email(local_vendor_id: int, email: str) -> bool:
    """Create a Contact row carrying a newly-sourced vendor email. Best-effort."""
    try:
        from entities.contact.business.service import ContactService
        ContactService().create(
            email=email,
            vendor_id=local_vendor_id,
            notes="Email captured for payment-remittance distribution",
        )
        return True
    except Exception as ex:  # never let a backfill failure block the draft
        print(f"    (contact backfill failed for {email}: {ex})")
        return False


def _split_emails(raw: Optional[str]) -> List[str]:
    """Split a possibly multi-address string on commas/semicolons into clean
    individual addresses. QBO `PrimaryEmailAddr` can hold 'a@x.com,b@y.com'."""
    return [a.strip() for a in re.split(r"[,;]", raw or "") if a.strip()]


def resolve_vendor_emails(payment: Dict[str, Any], overrides: Dict[str, List[str]],
                          realm_id: Optional[str] = None):
    """Resolve recipient emails: --email override -> local Contact -> QBO PrimaryEmailAddr.

    Each source is split on commas/semicolons so a multi-address value becomes
    individual recipients (and one Contact backfill each), never one malformed
    'a@x.com,b@y.com' address. Returns (emails, source, local_vendor_id, contact_emails).
    """
    vqid = payment["vendor_qbo_id"]
    local_id, contact_emails = local_vendor_and_contact_emails(vqid, realm_id)
    if overrides.get(vqid):
        return overrides[vqid], "override", local_id, contact_emails
    if contact_emails:
        flat = [a for e in contact_emails for a in _split_emails(e)]
        return flat, "contact", local_id, contact_emails
    if payment.get("qbo_email"):
        return _split_emails(payment["qbo_email"]), "qbo", local_id, contact_emails
    return [], "none", local_id, contact_emails


def email_subject(payment: Dict[str, Any]) -> str:
    return f"Rogers Build Inc. - {payment['vendor']} - ACH {payment['doc_number']}"


def email_body_html(payment: Dict[str, Any]) -> str:
    return (
        f"<p>{html.escape(payment['vendor'])} Team,</p>"
        f"<p>ACH details are attached for the payment process on "
        f"{_date_long(payment['txn_date'])}. Please review, and let us know if you have "
        f"any questions or need additional information.</p>"
        f"<p>Thanks,<br>Accounting</p>"
    )


def create_vendor_draft(payment: Dict[str, Any], emails: List[str],
                        pdf: bytes, filename: str) -> Dict[str, Any]:
    """Create a draft (with the remittance PDF attached) in invoice@rogersbuild.com Drafts."""
    from integrations.ms.mail.message.business.service import MsMessageService
    return MsMessageService().create_draft(
        to_recipients=[{"email": e, "name": payment["vendor"]} for e in emails],
        bcc_recipients=[{"email": REMITTANCE_BCC}],
        subject=email_subject(payment),
        body=email_body_html(payment),
        body_type="HTML",
        attachments=[{
            "name": filename,
            "content_type": "application/pdf",
            "content_bytes": base64.b64encode(pdf).decode("ascii"),
        }],
    )


def parse_email_overrides(values: Optional[List[str]]) -> Dict[str, List[str]]:
    """--email QBOID=addr1,addr2  (repeatable) -> {qbo_id: [addrs]}."""
    out: Dict[str, List[str]] = {}
    for raw in values or []:
        if "=" not in raw:
            raise SystemExit(f"--email expects QBOID=address, got {raw!r}")
        vid, addrs = raw.split("=", 1)
        out.setdefault(vid.strip(), []).extend(
            a.strip() for a in addrs.split(",") if a.strip()
        )
    return out


# ---------------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------------- #
def run_sharepoint_backfill(year: str, apply: bool) -> None:
    """Copy every Box check-stub for `year` that SharePoint is missing.

    Reads both surfaces live, decides per file with `needs_sharepoint_upload`, and
    prints the plan. Dry-run unless `apply` — the default is deliberately inert so
    the file list can be reviewed before any prod write. Bytes come from Box (the
    surface that has them); nothing is re-rendered, so a backfilled PDF is the
    exact file already filed in Box.
    """
    assert_cli_system_admin()
    box = BoxHttpClient()
    try:
        box_year_folder = resolve_year_folder(box, year, create=False)
        box_files = {k["name"]: k["id"]
                     for k in _box_items(box, box_year_folder) if k["type"] == "file"}

        sp_folder = resolve_sp_year_folder(SP_DRIVE_ID, year, create=False)
        sp_names = _sp_file_names(SP_DRIVE_ID, sp_folder)

        missing = sorted(n for n in box_files if needs_sharepoint_upload(n, sp_names))
        twins = find_possible_twins(missing, sp_names)

        print(f"Box {year}: {len(box_files)} file(s)   SharePoint {year}: {len(sp_names)} file(s)")
        print(f"Missing from SharePoint: {len(missing)}\n")
        for n in missing:
            print(f"  {n}")
        if twins:
            print(f"\n⚠ {len(twins)} possible twin(s) — same (payment#, total) as an existing "
                  f"SharePoint file under an unrecognized vendor name. Uploading these may "
                  f"duplicate; skipping them may lose a document. Review before --apply:")
            for cand, other in twins:
                print(f"  CANDIDATE {cand}\n  EXISTING  {other}\n")
        if not missing:
            print("\nNothing to do.")
            return
        if not apply:
            print(f"\nDRY RUN — nothing written. Re-run with --apply to upload "
                  f"{len(missing)} file(s) to SharePoint {year}/.")
            return

        print(f"\nAPPLYING — uploading {len(missing)} file(s)...")
        done = skipped = 0
        for n in missing:
            # Re-check per file, not once for the batch: the plan above was built
            # from a single listing, and an upload of 157 files is long enough for
            # another session to file one midway. The Graph path PUT is
            # upload-OR-REPLACE, so a stale plan would overwrite rather than fail.
            if not needs_sharepoint_upload(n, _sp_file_names(SP_DRIVE_ID, sp_folder)):
                skipped += 1
                print(f"  [skip] filed by someone else since the plan: {n}")
                continue
            data = box.download_file(box_files[n])
            upload_to_sharepoint(SP_DRIVE_ID, sp_folder, n, data)
            done += 1
            print(f"  [{done}/{len(missing)}] {n}")
        print(f"\nUploaded {done} file(s) to SharePoint {year}/"
              + (f" ({skipped} already filed by another writer)." if skipped else "."))
    finally:
        box.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate (and optionally upload) payment remittance PDFs from a QBO BillPayment.")
    ap.add_argument("doc_number", nargs="?",
                    help="QBO BillPayment.DocNumber (the payment/ACH number). Omit only with --backfill-sharepoint.")
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "_remittance_out"),
                    help="Local folder for generated PDFs (default: %(default)s)")
    ap.add_argument("--upload", action="store_true", help="Upload each PDF to Box (prod write; needs ALLOW_BOX_WRITES=true).")
    ap.add_argument("--draft-emails", action="store_true",
                    help="Draft a per-vendor email (PDF attached) in invoice@rogersbuild.com Drafts (needs ALLOW_MS_WRITES=true).")
    ap.add_argument("--email", action="append", metavar="QBOID=addr[,addr2]",
                    help="Supply email(s) for a vendor missing one (repeatable). e.g. --email 767=ap@bemac.com")
    ap.add_argument("--vendors", help="Comma-separated QBO vendor ids to limit processing to (e.g. 767,139).")
    ap.add_argument("--upload-sharepoint", action="store_true",
                    help="Also upload each PDF to the SharePoint check-stubs mirror "
                         "(prod write; needs ALLOW_MS_WRITES=true). Independent of --upload, "
                         "which stays Box-only.")
    ap.add_argument("--backfill-sharepoint", metavar="YEAR",
                    help="Reconcile mode: copy every Box check-stub for YEAR that SharePoint "
                         "is missing. Dry-run unless --apply is also given. No doc_number needed.")
    ap.add_argument("--apply", action="store_true",
                    help="With --backfill-sharepoint, actually upload (default is dry-run).")
    args = ap.parse_args()

    if args.backfill_sharepoint:
        run_sharepoint_backfill(args.backfill_sharepoint, apply=args.apply)
        return
    if not args.doc_number:
        ap.error("doc_number is required unless --backfill-sharepoint YEAR is given")

    overrides = parse_email_overrides(args.email)
    vendor_filter = {v.strip() for v in args.vendors.split(",")} if args.vendors else None

    batch = fetch_payment_batch(args.doc_number)
    payer = batch["payer"]
    payments = batch["payments"]
    realm_id = batch["realm_id"]
    if vendor_filter:
        payments = [p for p in payments if p["vendor_qbo_id"] in vendor_filter]
    os.makedirs(args.out_dir, exist_ok=True)
    print(f"Payer: {payer}")
    print(f"Payment #{args.doc_number}: {len(payments)} vendor(s)\n")

    box = BoxHttpClient() if args.upload else None
    year_folder: Optional[str] = None
    sp_folders: Dict[str, str] = {}   # keyed by year: one batch can span years
    needs_email: List[Dict[str, Any]] = []

    for p in payments:
        filename = build_filename(p)
        pdf = render_pdf(payer, p)
        local_path = os.path.join(args.out_dir, filename)
        with open(local_path, "wb") as fh:
            fh.write(pdf)
        sum_lines = sum((ln["amount"] for ln in p["lines"]), Decimal("0"))
        flag = "" if sum_lines == p["total"] else f"  !! lines sum {sum_lines} != total {p['total']}"
        n_bill = sum(1 for ln in p["lines"] if ln.get("kind") != "credit")
        n_cr = sum(1 for ln in p["lines"] if ln.get("kind") == "credit")
        credit_note = f" + {n_cr} credit(s)" if n_cr else ""
        print(f"• {p['vendor']}: {n_bill} bill(s){credit_note}, total {_usd(p['total'])}{flag}")
        print(f"    -> {local_path}")

        if args.draft_emails:
            emails, source, local_id, contact_emails = resolve_vendor_emails(p, overrides, realm_id)
            if not emails:
                needs_email.append(p)
                print(f"    EMAIL: no address on file (QBO id {p['vendor_qbo_id']}, local vendor "
                      f"{local_id}) — skipped; supply via --email {p['vendor_qbo_id']}=addr")
            else:
                # Capture any newly-sourced address into a local Contact row.
                if source in ("qbo", "override") and local_id:
                    for e in emails:
                        if e not in contact_emails:
                            ok = backfill_contact_email(local_id, e)
                            if ok:
                                print(f"    CONTACT: backfilled {e} (from {source}) -> vendor {local_id}")
                try:
                    res = create_vendor_draft(p, emails, pdf, filename)
                    ok = res.get("status_code") in (200, 201)
                    print(f"    EMAIL: draft {'created' if ok else 'FAILED: ' + str(res)} "
                          f"-> {', '.join(emails)} (source: {source})")
                except Exception as ex:
                    print(f"    EMAIL: draft FAILED ({type(ex).__name__}: {ex})")

        if args.upload_sharepoint:
            sp_year = (p["txn_date"] or "")[:4]
            sp_folder = sp_folder_for_year(sp_folders, SP_DRIVE_ID, sp_year)
            # Re-list immediately before writing: another session may have just filed it.
            sp_names = _sp_file_names(SP_DRIVE_ID, sp_folder)
            if needs_sharepoint_upload(filename, sp_names):
                for cand, other in find_possible_twins([filename], sp_names):
                    print(f"    SP REVIEW: {cand!r} shares (payment#, total) with existing "
                          f"{other!r} under an unrecognized vendor name — uploading anyway; "
                          f"confirm it is not a duplicate.")
                upload_to_sharepoint(SP_DRIVE_ID, sp_folder, filename, pdf)
                print(f"    SP: uploaded in {sp_year}/ (folder {sp_folder})")
            else:
                print(f"    SP: already present in {sp_year}/ — skipped")

        if not args.upload:
            continue

        year = (p["txn_date"] or "")[:4]
        if year_folder is None:
            year_folder = resolve_year_folder(box, year, create=True)

        existing = find_existing_for_payment(box, year_folder, p["doc_number"], p["vendor"])
        if existing and existing["name"] != filename:
            # Compare-and-decide (per the duplicate handling agreed for B. Christopher).
            try:
                existing_bytes = box.download_file(existing["id"])
                etext = extract_pdf_text(existing_bytes)
            except Exception as ex:
                etext = ""
                print(f"    (could not read existing {existing['name']!r}: {ex})")
            # Compare on financial substance, NOT formatting: normalize away
            # currency symbols / thousands separators / whitespace so that an
            # Office export ("16,000.00$") matches our render ("$16,000.00").
            def _norm(s: str) -> str:
                return s.replace("$", "").replace(",", "").replace(" ", "")
            norm_text = _norm(etext)
            tokens = [p["doc_number"], f"{p['total']:.2f}"] \
                + [ln["bill_number"] for ln in p["lines"]] \
                + [f"{ln['amount']:.2f}" for ln in p["lines"]]
            equivalent = bool(norm_text) and all(_norm(tok) in norm_text for tok in tokens)
            print(f"    DUP: an existing file matches (doc#, vendor): {existing['name']!r}")
            if equivalent:
                print(f"    DECISION: SKIP upload — existing file is financially equivalent "
                      f"(same doc#, bills, amounts, total). It is mis-named vs the convention "
                      f"(date/spelling); recommend replacing it, but not deleting without your OK.")
                continue
            print(f"    DECISION: UPLOAD — existing file differs in content; uploading the "
                  f"convention-correct version as a new file.")

        status = upload_or_version(box, year_folder, filename, pdf)
        print(f"    BOX: {status} in {year}/ (folder {year_folder})")

    if box:
        box.close()
    print(f"\nLocal PDFs in: {args.out_dir}")
    if needs_email:
        print("\nNEEDS EMAIL (no address on file — re-run with --email QBOID=addr):")
        for p in needs_email:
            print(f"  - {p['vendor']}  (QBO id {p['vendor_qbo_id']}, total {_usd(p['total'])})")


if __name__ == "__main__":
    main()
