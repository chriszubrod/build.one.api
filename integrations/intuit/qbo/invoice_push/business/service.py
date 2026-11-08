"""U-778d1 b2 -- InvoicePushDryRunService: real (read-only) facts -> bite 1's pure rules -> a verdict, and when
everything passes the QBO create payload plus the canonical expected invoice. Writes NOTHING (not to QBO, not to dbo).

Read contract: NO business writes -- nothing is created or changed in QBO or dbo. The shared QBO transport still
meters each call (`IncrementQboApiUsage`, fail-open) and may refresh the OAuth token: the normal side effects of any
QBO read.

Owns: orchestration only -- the rules live in eligibility.py, the payload in payload.py, the comparison in canonical.py.
Invariants: every QBO call goes through `_qbo_call`, which refuses any name outside READ_ONLY_QBO_METHODS; the
  doc number is whitelisted BEFORE it reaches the sproc or the QBO query; home currency is `USD` unless injected
  (the staged company info is not wired in this bite -- a foreign-currency charge is refused either way).
External writes: none.
Sprocs used (read-only, via persistence/repo.py): ReadInvoicePushProjectFacts, ReadInvoicePushBillLineFacts,
  ReadInvoicePushExpenseLineFacts, ReadInvoicePushBillCreditLineFacts, ReadInvoicePushStagedInvoiceLinksByChargeIds.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from integrations.intuit.qbo.invoice_push.business.canonical import CanonicalInvoice, canonicalize, compare
from integrations.intuit.qbo.base.errors import QboNotFoundError
from integrations.intuit.qbo.invoice_push.business.eligibility import (
    MAX_MONEY, evaluate_proposal, out_of_range, reason, unsupported_source_type_reason,
)
from integrations.intuit.qbo.invoice_push.business.model import (
    DIGITS, SUPPORTED_SOURCE_TYPES, FeeItem, InvoiceNotFound, Proposal, ProposalContext, ref_value,
)
from integrations.intuit.qbo.invoice_push.business.payload import build_invoice
from integrations.intuit.qbo.invoice_push.business.ports import READ_ONLY_QBO_METHODS
from shared.lifecycle.completion_gate import HoldingCodeRefused

logger = logging.getLogger(__name__)

# QBO DocNumber max length is 21. fullmatch only (a `$` would let a trailing newline through).
DOC_NUMBER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/#-]{0,20}")
_HOLDING = object()  # memo marker: this sub cost code is a holding code (HoldingCodeRefused)


def _qbo_call(reader: Any, method: str, *args, **kwargs):
    """The only door to QBO: reads only. (Not a bare `assert`: it must survive `python -O`.)"""
    if method not in READ_ONLY_QBO_METHODS:
        raise AssertionError(f"QBO method {method!r} is not a read; the dry run writes nothing")
    return getattr(reader, method)(*args, **kwargs)


def jsonable(o: Any) -> Any:
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {f.name: jsonable(getattr(o, f.name)) for f in dataclasses.fields(o)}
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in o]
    return o


@dataclass(frozen=True)
class _Built:
    """What `_finish` needs to build the payload once every rule has passed."""
    charges: dict
    live: dict
    fee_items: list
    customer_id: Optional[str]
    sales_term_id: Optional[str]


@dataclass(frozen=True)
class DryRunResult:
    ok: bool
    header_reasons: tuple
    lines: tuple  # (LineVerdict, ...)
    payload: Optional[dict]
    expected: Optional[CanonicalInvoice]

    def to_dict(self) -> dict:
        return jsonable(self)


def _default_customer_resolver(project_id) -> Optional[str]:
    """Reuse the invoice connector's dbo-only identity verification (U-311) rather than re-implementing it."""
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector
    ref = InvoiceInvoiceConnector()._get_qbo_customer_ref(int(project_id))
    return ref.value if ref else None


def _default_fee_item_resolver(sub_cost_code_id, realm_id) -> Optional[FeeItem]:
    from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref
    ref = resolve_qbo_item_ref(int(sub_cost_code_id), realm_id)
    return FeeItem(item_ref=ref.value) if ref else None


def _default_payment_term_resolver(payment_term_id, realm_id) -> Optional[str]:
    """dbo.PaymentTerm's native QboId, realm-checked (dbo-only, like the bill push); None = unmapped."""
    tid = _as_int(payment_term_id)
    if tid is None:
        return None
    from entities.payment_term.business.service import PaymentTermService
    try:
        term = PaymentTermService().read_by_id(tid)
    except Exception as e:  # a transient read failure is "unmapped" -> the proposal is refused, never guessed
        logger.warning("dbo.PaymentTerm read failed for %s: %s", payment_term_id, e)
        return None
    if term and term.qbo_id and term.realm_id == realm_id:
        return str(term.qbo_id)
    return None


def _as_int(x) -> Optional[int]:
    try:
        return int(str(x))
    except (TypeError, ValueError):
        return None


class InvoicePushDryRunService:
    def __init__(self, *, repo, qbo_reader_factory: Callable[[str], Any],
                 customer_resolver: Optional[Callable] = None, fee_item_resolver: Optional[Callable] = None,
                 payment_term_resolver: Optional[Callable] = None, home_currency: str = "USD"):
        self.repo = repo
        self.qbo_reader_factory = qbo_reader_factory
        self.customer_resolver = customer_resolver or _default_customer_resolver
        self.fee_item_resolver = fee_item_resolver or _default_fee_item_resolver
        self.payment_term_resolver = payment_term_resolver or _default_payment_term_resolver
        self.home_currency = home_currency

    # ---- public --------------------------------------------------------------------
    def dry_run(self, proposal: Proposal) -> DryRunResult:
        return self._run(proposal, compare_invoice_id=None)[0]

    def compare(self, proposal: Proposal, qbo_invoice_id: str) -> dict:
        """Shadow round: the dry run's expected invoice vs the existing QBO invoice, PrivateNote ignored.

        Bound to the compared invoice: reasons that are consequences of that invoice existing are tolerated only
        when the evidence names exactly it, and the fetched invoice must belong to the proposal project's QBO customer
        or `InvoiceNotFound` is raised (nothing about the invoice is returned)."""
        qid = str(qbo_invoice_id)
        if not DIGITS.fullmatch(qid):
            raise ValueError("qbo_invoice_id must be a numeric QBO id")
        result, actual_raw = self._run(proposal, compare_invoice_id=qid)
        out: Dict[str, Any] = {"dry_run": result.to_dict(), "diffs": None}
        if result.expected is not None and actual_raw is not None:
            actual = canonicalize(actual_raw, home_currency=self.home_currency, require_total=True)
            actual = replace(actual, private_note=result.expected.private_note)
            out["diffs"] = jsonable(compare(result.expected, actual, require_line_ids=True))
        return out

    # ---- internals -----------------------------------------------------------------
    @staticmethod
    def _refused(header, line_verdicts=()) -> DryRunResult:
        return DryRunResult(False, tuple(header), tuple(line_verdicts), None, None)

    def _resolve_fee_item(self, reader, sub_cost_code_id, realm):
        """FeeItem with its income account: from the resolver when it supplies one, else the QBO Item's own
        IncomeAccountRef (read-only GET). None when the account cannot be resolved."""
        item = self.fee_item_resolver(sub_cost_code_id, realm)
        if item is None or item.account_ref:
            return item
        raw = _qbo_call(reader, "get_item", item.item_ref)
        acct = ref_value(raw.get("IncomeAccountRef")) if isinstance(raw, dict) else None
        if not acct or str(raw.get("Id")) != str(item.item_ref):
            return None
        return replace(item, account_ref=acct)

    def _collect_facts(self, proposal: Proposal, realm: str, header: list):
        """One sproc call per source type -> (parsed, facts, facts_by_type). `parsed` = (type, raw id, int id | None)
        per requested line, in order; `facts` keyed (type, int id); `facts_by_type` = {type: [(key, facts)]} for the
        types that had usable ids. Unsupported types are reported here (header) and never reach the repo."""
        parsed = [(key[0], key[1], _as_int(key[1])) for key in proposal.source_lines]
        by_type: Dict[str, list] = {}
        for st, _, i in parsed:
            if st not in SUPPORTED_SOURCE_TYPES:
                header.append(unsupported_source_type_reason(st))
            elif i is not None:
                by_type.setdefault(st, []).append(i)
        facts: Dict[tuple, Any] = {}
        facts_by_type: Dict[str, list] = {}
        for st, ids in by_type.items():
            facts_by_type[st] = []
            for k, f in self.repo.read_source_facts(st, ids, realm).items():
                key = (k[0], k[1])
                facts[key] = f
                facts_by_type[st].append((key, f))
        return parsed, facts, facts_by_type

    @staticmethod
    def _unavailable_source_lines(parsed, facts, project_id) -> list:
        """A requested source line that does not exist, has an unusable id, or belongs to ANOTHER project: missing and
        foreign must be indistinguishable, or a caller authorized for this project could enumerate other projects'
        lines. Unsupported types are skipped (already reported; says nothing about existence)."""
        out = []
        for st, sid, i in parsed:
            if st not in SUPPORTED_SOURCE_TYPES:
                continue
            f = facts.get((st, i)) if i is not None else None
            if f is None or f.project_id is None or str(f.project_id) != str(project_id):
                out.append(f"{st}:{sid}")
        return out

    @staticmethod
    def _bind_compared_invoice(reader, compare_invoice_id: str, customer_id) -> dict:
        """GET the compared invoice; it must be the REQUESTED one and belong to this project's customer, or
        `InvoiceNotFound` (absent, deleted and not-ours are one opaque answer)."""
        try:
            actual_raw = _qbo_call(reader, "get_invoice", compare_invoice_id)
        except QboNotFoundError:  # absent or deleted (404 / 400 fault 610)
            raise InvoiceNotFound(compare_invoice_id)
        if (str((actual_raw or {}).get("Id") or "") != str(compare_invoice_id)
                or not customer_id
                or ref_value((actual_raw or {}).get("CustomerRef")) != str(customer_id)):
            raise InvoiceNotFound(compare_invoice_id)
        return actual_raw

    @staticmethod
    def _read_live_lines(reader, facts_by_type) -> Dict[tuple, Any]:
        """Live source lines, keyed back to our (type, line id)."""
        live: Dict[tuple, Any] = {}
        for st, entries in facts_by_type.items():
            parents = sorted({f.parent_qbo_id for _, f in entries if f.parent_qbo_id})
            if not parents:
                continue
            lines = _qbo_call(reader, "read_source_lines", st, parents)
            for k, f in entries:
                if f.parent_qbo_id and f.line_qbo_id:
                    lv = lines.get((str(f.parent_qbo_id), str(f.line_qbo_id)))
                    if lv is not None:
                        live[k] = lv
        return live

    def _resolve_fee_items(self, reader, proposal: Proposal, realm: str, header: list) -> list:
        """One resolution (and at most one QBO `get_item`) per distinct sub cost code within the run."""
        memo: Dict[Any, Any] = {}
        fee_items: list = []
        for i, fee in enumerate(proposal.fee_lines):
            scc = fee.sub_cost_code_id
            if scc not in memo:
                try:
                    memo[scc] = self._resolve_fee_item(reader, scc, realm)
                except HoldingCodeRefused:
                    memo[scc] = _HOLDING
            item = memo[scc]
            if item is _HOLDING:
                header.append(reason("fee_holding_code", f"fee line {i} sub cost code {scc} is a holding code"))
            elif item is None:
                header.append(reason("fee_item_unresolved", f"fee line {i} sub cost code {scc} "
                                                            f"has no QBO item with an income account in realm {realm}"))
            else:
                fee_items.append(item)
        return fee_items

    def _resolve_sales_term(self, proposal: Proposal, realm: str, header: list) -> Optional[str]:
        if not proposal.payment_term_id:
            return None
        sales_term_id = self.payment_term_resolver(proposal.payment_term_id, realm)
        if not sales_term_id:
            header.append(reason("payment_term_unmapped", f"payment term {proposal.payment_term_id} has no QBO term "
                                                          f"in realm {realm}"))
        return sales_term_id

    def _run(self, proposal: Proposal, *, compare_invoice_id: Optional[str]):
        """-> (DryRunResult, raw actual invoice | None). The reader is closed on EVERY path."""
        header: list = []
        doc_ok = bool(DOC_NUMBER_RE.fullmatch(proposal.doc_number or ""))
        if (proposal.doc_number or "").strip() and not doc_ok:
            header.append(reason("doc_number_invalid", "must match [A-Za-z0-9][A-Za-z0-9 ._/#-]{0,20} "
                                                       f"(got {proposal.doc_number!r})"))
        project = self.repo.read_project_facts(proposal.project_id, proposal.doc_number if doc_ok else "")
        if project is None:
            header.append(reason("project_not_found", f"project {proposal.project_id}"))
            return self._refused(header), None
        if not project.realm_id:
            header.append(reason("no_realm", f"project {proposal.project_id} has no QBO realm"))
            return self._refused(header), None
        realm = project.realm_id
        customer_id = self.customer_resolver(proposal.project_id)

        parsed, facts, facts_by_type = self._collect_facts(proposal, realm, header)
        # ONE opaque answer: no live read, no per-line diagnostics
        unavailable = self._unavailable_source_lines(parsed, facts, proposal.project_id)
        if unavailable:
            header.append(reason("source_line_unavailable", f"{len(unavailable)} source line(s) are not available in "
                                                            f"project {proposal.project_id}: " + ", ".join(unavailable)))
            return self._refused(header), None

        reader = self.qbo_reader_factory(realm)
        try:
            actual_raw = None
            if compare_invoice_id is not None:
                actual_raw = self._bind_compared_invoice(reader, compare_invoice_id, customer_id)
            live = self._read_live_lines(reader, facts_by_type)
            charge_ids = sorted({lv.charge_id for lv in live.values() if lv.charge_id})
            charges = _qbo_call(reader, "read_charges", charge_ids) if charge_ids else {}
            # staged invoice evidence is keyed by the LIVE charge ids (the staged charge id is a warning only)
            staged_links = self.repo.read_staged_invoice_links(charge_ids, realm) if charge_ids else {}
            qbo_doc_ids = tuple(_qbo_call(reader, "doc_number_invoice_ids", proposal.doc_number)) if doc_ok else ()
            fee_items = self._resolve_fee_items(reader, proposal, realm, header)
            sales_term_id = self._resolve_sales_term(proposal, realm, header)

            ctx = ProposalContext(project_id=project.project_id, customer_id=customer_id,
                                  home_currency=self.home_currency,
                                  doc_number_used_locally=project.doc_number_used_locally,
                                  doc_number_used_in_qbo=bool(qbo_doc_ids),
                                  compare_invoice_id=compare_invoice_id,
                                  doc_number_local_qbo_ids=tuple(project.doc_number_local_invoice_qbo_ids),
                                  doc_number_qbo_ids=qbo_doc_ids)
            verdict = evaluate_proposal(proposal, ctx, facts, live, charges, staged_links=staged_links)
            header += list(verdict.header_reasons)
            built = _Built(charges, live, fee_items, customer_id, sales_term_id)
            return self._finish(proposal, header, verdict.lines, built), actual_raw
        finally:
            _qbo_call(reader, "close")

    def _finish(self, proposal, header, line_verdicts, built: Optional[_Built] = None) -> DryRunResult:
        header = list(header)
        ok = not header and bool(line_verdicts or not proposal.source_lines) and all(v.ok for v in line_verdicts)
        if built is None or not ok or len(line_verdicts) != len(proposal.source_lines):
            return self._refused(header, line_verdicts)
        ordered = [built.charges[built.live[tuple(k)].charge_id] for k in proposal.source_lines]
        total = sum((ln.amount for c in ordered for ln in c.lines), Decimal("0")) + sum(
            (fee.qty * fee.unit_price for fee in proposal.fee_lines), Decimal("0"))
        if out_of_range(total):
            header.append(reason("amount_out_of_range", f"invoice total {total} (|x| must be < {MAX_MONEY:f})"))
            return self._refused(header, line_verdicts)
        out = build_invoice(proposal, customer_id=built.customer_id, charges=ordered, fee_items=built.fee_items,
                            sales_term_id=built.sales_term_id, home_currency=self.home_currency)
        # the bound applies to what is actually EMITTED (rounded lines and their total), not only the raw inputs
        emitted = [ln.amount for ln in out.expected.lines if ln.amount is not None]
        if out.expected.total_amt is not None:
            emitted.append(out.expected.total_amt)
        if any(out_of_range(a) for a in emitted):
            header.append(reason("amount_out_of_range",
                                 f"an emitted amount or the invoice total reaches {MAX_MONEY:f}"))
            return self._refused(header, line_verdicts)
        return DryRunResult(True, tuple(header), tuple(line_verdicts), out.payload, out.expected)
