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

Read-only against QBO and the local DB; the ONLY external write is the Box
upload, gated behind --upload AND ALLOW_BOX_WRITES=true.
"""
# Standard Library
import argparse
import base64
import html
import io
import os
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

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

# Box anchor: the '999 - Accounting' folder (stable id, found via the template probe).
BOX_999_ACCOUNTING_ID = "388262075849"
BOX_PATH_SEGMENTS = ["02 - Accounts Payable", "535 - Rogers Build - Check Stubs"]
COMPANY_FALLBACK = "Rogers Build, Inc."
METHOD_LABEL = "ACH"  # business pays via ACH even though QBO books PayType=Check
REMITTANCE_BCC = "invoice@rogersbuild.com"  # always BCC'd on vendor remittance drafts
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
    """Return {payer, payments:[{vendor, doc_number, txn_date, total, lines:[...]}]}."""
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
                if lt.get("TxnType") != "Bill":
                    continue
                b = bill_by_id.get(str(lt.get("TxnId")), {})
                lines.append({
                    "bill_number": str(b.get("DocNumber") or lt.get("TxnId")),
                    "bill_date": b.get("TxnDate"),
                    "amount": amt,
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
    return {"payer": payer, "payments": payments}


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
    for ln in payment["lines"]:
        data.append([ln["bill_number"], _date_short(ln["bill_date"]), _usd(ln["amount"])])
    data.append(["", "Total", _usd(payment["total"])])
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


def find_existing_for_payment(client: BoxHttpClient, folder_id: str,
                              doc_number: str, vendor: str) -> Optional[Dict[str, Any]]:
    """Loose (doc#, vendor) match against existing filenames, ignoring date/punctuation."""
    def norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())
    vtoken = norm(vendor)
    for it in _box_items(client, folder_id):
        if it["type"] != "file":
            continue
        nm = it["name"]
        if doc_number in nm and vtoken in norm(nm):
            return it
    return None


def extract_pdf_text(data: bytes) -> str:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages)


# ---------------------------------------------------------------------------- #
# Vendor email resolution + draft (MS Graph, into invoice@rogersbuild.com Drafts)
# ---------------------------------------------------------------------------- #
def local_vendor_and_contact_emails(qbo_vendor_id: str):
    """Map a QBO vendor id -> (local Vendor.Id, [existing Contact emails])."""
    from shared.database import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT v.Id, c.Email
            FROM qbo.Vendor qv
            JOIN qbo.VendorVendor vv ON vv.QboVendorId = qv.Id
            JOIN dbo.Vendor v ON v.Id = vv.VendorId
            LEFT JOIN dbo.Contact c ON c.VendorId = v.Id
            WHERE qv.QboId = ?
            """,
            (qbo_vendor_id,),
        )
        rows = cur.fetchall()
    if not rows:
        return None, []
    local_id = rows[0][0]
    emails = sorted({r[1].strip() for r in rows if r[1] and r[1].strip()})
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


def resolve_vendor_emails(payment: Dict[str, Any], overrides: Dict[str, List[str]]):
    """Resolve recipient emails: --email override -> local Contact -> QBO PrimaryEmailAddr.

    Returns (emails, source, local_vendor_id, contact_emails).
    """
    vqid = payment["vendor_qbo_id"]
    local_id, contact_emails = local_vendor_and_contact_emails(vqid)
    if overrides.get(vqid):
        return overrides[vqid], "override", local_id, contact_emails
    if contact_emails:
        return contact_emails, "contact", local_id, contact_emails
    if payment.get("qbo_email"):
        return [payment["qbo_email"]], "qbo", local_id, contact_emails
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
def main() -> None:
    ap = argparse.ArgumentParser(description="Generate (and optionally upload) payment remittance PDFs from a QBO BillPayment.")
    ap.add_argument("doc_number", help="QBO BillPayment.DocNumber (the payment/ACH number)")
    ap.add_argument("--out-dir", default="/Users/chris/Applications/build.one/_remittance_out",
                    help="Local folder for generated PDFs (default: %(default)s)")
    ap.add_argument("--upload", action="store_true", help="Upload each PDF to Box (prod write; needs ALLOW_BOX_WRITES=true).")
    ap.add_argument("--draft-emails", action="store_true",
                    help="Draft a per-vendor email (PDF attached) in invoice@rogersbuild.com Drafts (needs ALLOW_MS_WRITES=true).")
    ap.add_argument("--email", action="append", metavar="QBOID=addr[,addr2]",
                    help="Supply email(s) for a vendor missing one (repeatable). e.g. --email 767=ap@bemac.com")
    ap.add_argument("--vendors", help="Comma-separated QBO vendor ids to limit processing to (e.g. 767,139).")
    args = ap.parse_args()

    overrides = parse_email_overrides(args.email)
    vendor_filter = {v.strip() for v in args.vendors.split(",")} if args.vendors else None

    batch = fetch_payment_batch(args.doc_number)
    payer = batch["payer"]
    payments = batch["payments"]
    if vendor_filter:
        payments = [p for p in payments if p["vendor_qbo_id"] in vendor_filter]
    os.makedirs(args.out_dir, exist_ok=True)
    print(f"Payer: {payer}")
    print(f"Payment #{args.doc_number}: {len(payments)} vendor(s)\n")

    box = BoxHttpClient() if args.upload else None
    year_folder: Optional[str] = None
    needs_email: List[Dict[str, Any]] = []

    for p in payments:
        filename = build_filename(p)
        pdf = render_pdf(payer, p)
        local_path = os.path.join(args.out_dir, filename)
        with open(local_path, "wb") as fh:
            fh.write(pdf)
        sum_lines = sum((ln["amount"] for ln in p["lines"]), Decimal("0"))
        flag = "" if sum_lines == p["total"] else f"  !! lines sum {sum_lines} != total {p['total']}"
        print(f"• {p['vendor']}: {len(p['lines'])} bill(s), total {_usd(p['total'])}{flag}")
        print(f"    -> {local_path}")

        if args.draft_emails:
            emails, source, local_id, contact_emails = resolve_vendor_emails(p, overrides)
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
