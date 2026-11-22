"""U-797c2a step 0 — FROZEN goldens for the review-submit email, captured from the
UNCHANGED code before any production file was touched.

Pins, for Bill / Expense (via the U-754 characterization harness) and for Contract
Labor (via fakes at the module seams), the exact outbound `enqueue_send_mail`
arguments: subject, to / cc / bcc lists (order and names), HTML body, body_type,
mode, entity_type, entity_public_id, review_id / bill_id kwargs.

The literals below are the contract. Never regenerate them to make a refactor
pass: a diff here IS a behaviour change.

No DB, no Graph, no network. Every collaborator is faked.
"""

import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business import cl_notification_service as cl_mod
from entities.review.business.cl_notification_service import (
    CL_LEGACY_NOTIFICATION_SUBJECT_PREFIX,
    CL_NOTIFICATION_SUBJECT_PREFIX,
    ContractLaborReviewNotificationService,
    is_cl_notification_subject,
)
from tests.test_u754_review_notification_characterization import SCENARIOS, _run as _u754_run

CAPTURE = os.environ.get("U797C2A_CAPTURE") == "1"


# ---------------------------------------------------------------------------
# Bill / Expense — the U-754 harness, enqueue_send_mail kwargs only
# ---------------------------------------------------------------------------

BILL_EXPENSE_CASES = [
    ("bill", "full"),                          # multi-project bill, attachment PDF wins
    ("bill", "no_attachment_single_project"),  # single-project bill, no attachment
    ("bill", "sparse_entity"),                 # bill with no number, no vendor
    ("bill", "bcc_only"),                      # no PM/owner, archive BCC only
    ("bill", "zero_recipients_no_bcc"),        # nothing to send to: no enqueue
    ("expense", "full"),                       # multi-project expense with memo
    ("expense", "no_attachment_single_project"),  # single-project expense with memo
    ("expense", "sparse_entity"),              # expense with no reference, blank memo
    ("expense", "owners_only"),                # owners only, no BCC inbox configured
    ("expense", "mode_setting_draft"),         # config flip never changes the mode
]


def _enqueue_kwargs(calls):
    return [c[2] for c in calls if c[0] == "outbox.enqueue_send_mail"]


def _bill_expense_actual(caplog, kind, name):
    out = _u754_run(caplog, kind, name)
    return _enqueue_kwargs(out["calls"])


@pytest.mark.parametrize("kind,name", BILL_EXPENSE_CASES, ids=lambda v: str(v))
def test_bill_expense_enqueue_is_frozen(caplog, kind, name):
    actual = _bill_expense_actual(caplog, kind, name)
    if CAPTURE:
        print(f"\nCAPTURE {kind}|{name} = {actual!r}")
        return
    assert actual == BILL_EXPENSE_GOLDEN[(kind, name)]


# ---------------------------------------------------------------------------
# Contract Labor — fakes at the module seams
# ---------------------------------------------------------------------------


def _pm(uid, first, last, email):
    return {"user_id": uid, "firstname": first, "lastname": last, "email": email}


def _row(cl_id, pid, pname, pabbr, emp, hours, billable, overhead, desc):
    return SimpleNamespace(
        ContractLaborId=cl_id, ProjectId=pid, ProjectName=pname, ProjectAbbreviation=pabbr,
        EmployeeName=emp, Hours=hours, IsBillable=billable, IsOverhead=overhead,
        Description=desc,
    )


CL_CASES = {
    "cl_single_worker": dict(
        rows=[_row(1, 10, "Hidden Elm 12", "HE12", "Ana Diaz", Decimal("8.00"), True, False, "Framing")],
        recipients={10: {"name": "Hidden Elm 12", "abbreviation": "HE12",
                         "pms": [_pm(1, "Pat", "Lee", "pat@example.com")],
                         "owners": [_pm(5, "Olive", "Own", "olive@example.com")]}},
        inbox="invoice@example.com",
    ),
    "cl_multi_worker_multi_line": dict(
        rows=[
            _row(1, 10, "Hidden Elm 12", "HE12", "Ana Diaz", Decimal("4.5"), None, True, "Drywall"),
            _row(1, 10, "Hidden Elm 12", "HE12", "Ana Diaz", 2, False, None, None),
            _row(2, 10, "Hidden Elm 12", "HE12", "Ben <Ort>", 6, False, None, "  "),
            _row(3, 11, "Oak & Pine", None, "Cy Dale", None, None, None, "Trim & paint"),
        ],
        recipients={
            10: {"name": "Hidden Elm 12", "abbreviation": "HE12",
                 "pms": [_pm(1, "Cassidy", "A", "cassidy@example.com"),
                         _pm(2, "Zach", "B", "zach@example.com"),
                         _pm(3, "Cassidy", "C", "cassidy2@example.com"),
                         _pm(4, "  ", "D", None)],
                 "owners": [_pm(5, "Olive", "Own", "olive@example.com"),
                            _pm(6, "Oscar", "Gone", None)]},
            11: {"name": "Oak & Pine", "abbreviation": None, "pms": [], "owners": []},
        },
        inbox="invoice@example.com",
    ),
    "cl_no_pm_no_greeting": dict(
        rows=[_row(7, 10, "Hidden Elm 12", "HE12", "Eve Ng", Decimal("3"), True, True, "Cleanup")],
        recipients={10: {"name": "", "abbreviation": "HE12", "pms": [],
                         "owners": [_pm(5, "Olive", "Own", "olive@example.com")]}},
        inbox="invoice@example.com",
    ),
    "cl_label_fallback_to_id": dict(
        rows=[_row(8, 12, "", "", "Flo Park", Decimal("1"), True, False, "Misc")],
        recipients={12: {"name": "", "abbreviation": "", "pms": [], "owners": []}},
        inbox="invoice@example.com",
    ),
    "cl_no_bcc_when_unconfigured": dict(
        rows=[_row(9, 10, "Hidden Elm 12", "HE12", "Gus Hart", Decimal("2"), True, False, "Paint")],
        recipients={10: {"name": "", "abbreviation": "HE12",
                         "pms": [_pm(1, "Pat", "Lee", "pat@example.com")], "owners": []}},
        inbox="",
    ),
}


def _cl_actual(case):
    contract_labor = SimpleNamespace(public_id="cl-pub-42", work_date=date(2026, 10, 5))
    sent = []
    outbox = MagicMock()

    def _enqueue(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(id=500 + len(sent), public_id=f"ob-{len(sent)}")

    outbox.enqueue_send_mail.side_effect = _enqueue
    settings = MagicMock(invoice_inbox_email=case["inbox"])
    with patch.object(ContractLaborReviewNotificationService, "_count_pending_for_date", return_value=0), \
         patch.object(ContractLaborReviewNotificationService, "_read_submitted_lines_for_date",
                      return_value=case["rows"]), \
         patch.object(ContractLaborReviewNotificationService, "_claim_notification", return_value=True), \
         patch.object(ContractLaborReviewNotificationService, "_fetch_recipients",
                      return_value=case["recipients"]), \
         patch.object(cl_mod, "MsOutboxService", return_value=outbox), \
         patch("config.Settings", return_value=settings), \
         patch("entities.review.business.review_notification_service.ReviewNotificationRecordService") as rec_cls:
        # No record claim: the enqueue call is then the pre-U-797c2a shape (no key).
        rec_cls.return_value.claim_for_crew.return_value = None
        rec_cls.return_value.try_claim_for_crew.return_value = None
        ContractLaborReviewNotificationService()._do_enqueue(contract_labor=contract_labor)
    return sent


@pytest.mark.parametrize("name", sorted(CL_CASES))
def test_contract_labor_enqueue_is_frozen(name):
    actual = _cl_actual(CL_CASES[name])
    if CAPTURE:
        print(f"\nCAPTURE {name} = {actual!r}")
        return
    assert actual == CL_GOLDEN[name]


def test_cl_subject_prefixes_are_frozen():
    assert CL_NOTIFICATION_SUBJECT_PREFIX == "[Review] Contract Labor - "
    assert CL_LEGACY_NOTIFICATION_SUBJECT_PREFIX == "Contract Labor - "
    assert is_cl_notification_subject("[Review] Contract Labor - HE12 - 2026-10-05") is True
    assert is_cl_notification_subject("Contract Labor - HE12 - 2026-10-05") is True
    assert is_cl_notification_subject("Re: [Review] Contract Labor - HE12 - 2026-10-05") is False
    assert is_cl_notification_subject("Bill #1 - Acme") is False
    assert is_cl_notification_subject(None) is False


# ---------------------------------------------------------------------------
# FROZEN LITERALS
# ---------------------------------------------------------------------------

BILL_EXPENSE_GOLDEN = {('bill', 'full'): [{'attachment': {'name': 'bill.pdf',
                                    'content_type': 'Application/PDF',
                                    'blob_url': 'b/904.pdf'},
                     'bcc_addresses': [{'email': 'invoice@example.com', 'name': 'Invoice Inbox (archive)'}],
                     'bill_id': 55,
                     'body': '<p>Pat/Zoë,</p><p>A new bill has been submitted for review:</p><p>Project: '
                             'HE12, Oak &amp; Pine<br/>Vendor: Acme &lt;Supply&gt; &amp; Co<br/>Number: '
                             'INV-1001<br/>Amount: $1,234.50</p><p>Submitted By: Sam Sub<br/>Submitted Date: '
                             "10/01/2026</p><table cellpadding='4' cellspacing='0' border='1' "
                             "style='border-collapse:collapse; margin-top:6px;'><tr><th "
                             "align='left'>Description</th><th align='left'>Project</th><th align='left'>Sub "
                             "Cost Code</th><th align='right'>Amount</th></tr><tr><td>Studs &amp; "
                             'plates</td><td>HE12</td><td>01-100 Framing</td><td '
                             "style='text-align:right;'>$1,000.50</td></tr><tr><td></td><td>Oak &amp; "
                             'Pine</td><td>None Drywall &lt;L5&gt;</td><td '
                             "style='text-align:right;'>$200.00</td></tr><tr><td>&lt;b&gt;freight&lt;/b&gt;</td><td></td><td></td><td "
                             "style='text-align:right;'>$0.00</td></tr><tr><td>misc</td><td></td><td></td><td "
                             "style='text-align:right;'>$0.00</td></tr><tr><td>unknown "
                             'project</td><td></td><td></td><td '
                             "style='text-align:right;'>$34.00</td></tr></table><p>Attached: "
                             '<strong>bill.pdf</strong></p><p>When you have a moment, will you please reply '
                             'for approval with Sub Cost Code and Description, or non-approval?</p>',
                     'body_type': 'HTML',
                     'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'},
                                      {'email': 'noname@example.com', 'name': 'noname@example.com'}],
                     'entity_public_id': 'bill-pub-55',
                     'entity_type': 'Bill',
                     'mode': 'draft',
                     'review_id': 9,
                     'subject': '[Review] Acme <Supply> & Co — Bill #INV-1001 — HE12, Oak & Pine — $1,234.50',
                     'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'},
                                      {'email': 'blank@example.com', 'name': '   Blank'},
                                      {'email': 'zoe@example.com', 'name': 'Zoë'}]}],
 ('bill', 'no_attachment_single_project'): [{'attachment': None,
                                             'bcc_addresses': [{'email': 'invoice@example.com',
                                                                'name': 'Invoice Inbox (archive)'}],
                                             'bill_id': 55,
                                             'body': '<p>Pat/Zoë,</p><p>A new bill has been submitted for '
                                                     'review:</p><p>Project: HE12<br/>Vendor: Acme '
                                                     '&lt;Supply&gt; &amp; Co<br/>Number: '
                                                     'INV-1001<br/>Amount: $1,234.50</p><p>Submitted By: Sam '
                                                     'Sub<br/>Submitted Date: 10/01/2026</p><table '
                                                     "cellpadding='4' cellspacing='0' border='1' "
                                                     "style='border-collapse:collapse; "
                                                     "margin-top:6px;'><tr><th "
                                                     "align='left'>Description</th><th align='left'>Sub Cost "
                                                     'Code</th><th '
                                                     "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                     'Framing</td><td '
                                                     "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                     'you have a moment, will you please reply for approval '
                                                     'with Sub Cost Code and Description, or '
                                                     'non-approval?</p>',
                                             'body_type': 'HTML',
                                             'cc_addresses': [{'email': 'olive@example.com',
                                                               'name': 'Olive Own'},
                                                              {'email': 'noname@example.com',
                                                               'name': 'noname@example.com'}],
                                             'entity_public_id': 'bill-pub-55',
                                             'entity_type': 'Bill',
                                             'mode': 'draft',
                                             'review_id': 9,
                                             'subject': '[Review] Acme <Supply> & Co — Bill #INV-1001 — HE12 '
                                                        '— $1,234.50',
                                             'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'},
                                                              {'email': 'blank@example.com',
                                                               'name': '   Blank'},
                                                              {'email': 'zoe@example.com', 'name': 'Zoë'}]}],
 ('bill', 'sparse_entity'): [{'attachment': None,
                              'bcc_addresses': [{'email': 'invoice@example.com',
                                                 'name': 'Invoice Inbox (archive)'}],
                              'bill_id': 55,
                              'body': '<p>Pat/Zoë,</p><p>A new bill has been submitted for '
                                      'review:</p><p>Project: (no project)<br/>Vendor: (unknown '
                                      'vendor)<br/>Number: (no number)<br/>Amount: $0.00</p><p>Submitted By: '
                                      'User 27<br/>Submitted Date: </p><p>When you have a moment, will you '
                                      'please reply for approval with Sub Cost Code and Description, or '
                                      'non-approval?</p>',
                              'body_type': 'HTML',
                              'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'},
                                               {'email': 'noname@example.com', 'name': 'noname@example.com'}],
                              'entity_public_id': 'bill-pub-55',
                              'entity_type': 'Bill',
                              'mode': 'draft',
                              'review_id': 9,
                              'subject': '[Review] (unknown vendor) — Bill #(no number) — (no project) — '
                                         '$0.00',
                              'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'},
                                               {'email': 'blank@example.com', 'name': '   Blank'},
                                               {'email': 'zoe@example.com', 'name': 'Zoë'}]}],
 ('bill', 'bcc_only'): [{'attachment': None,
                         'bcc_addresses': [{'email': 'invoice@example.com',
                                            'name': 'Invoice Inbox (archive)'}],
                         'bill_id': 55,
                         'body': '<p>A new bill has been submitted for review:</p><p>Project: '
                                 'HE12<br/>Vendor: Acme &lt;Supply&gt; &amp; Co<br/>Number: '
                                 'INV-1001<br/>Amount: $1,234.50</p><p>Submitted By: Sam Sub<br/>Submitted '
                                 "Date: 10/01/2026</p><table cellpadding='4' cellspacing='0' border='1' "
                                 "style='border-collapse:collapse; margin-top:6px;'><tr><th "
                                 "align='left'>Description</th><th align='left'>Sub Cost Code</th><th "
                                 "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 Framing</td><td "
                                 "style='text-align:right;'>$12.30</td></tr></table><p>When you have a "
                                 'moment, will you please reply for approval with Sub Cost Code and '
                                 'Description, or non-approval?</p>',
                         'body_type': 'HTML',
                         'cc_addresses': [],
                         'entity_public_id': 'bill-pub-55',
                         'entity_type': 'Bill',
                         'mode': 'draft',
                         'review_id': 9,
                         'subject': '[Review] Acme <Supply> & Co — Bill #INV-1001 — HE12 — $1,234.50',
                         'to_addresses': []}],
 ('bill', 'zero_recipients_no_bcc'): [],
 ('expense', 'full'): [{'attachment': {'name': 'expense.pdf',
                                       'content_type': 'Application/PDF',
                                       'blob_url': 'b/904.pdf'},
                        'bcc_addresses': [{'email': 'invoice@example.com',
                                           'name': 'Invoice Inbox (archive)'}],
                        'body': '<p>Pat/Zoë,</p><p>A new expense has been submitted for '
                                'review:</p><p>Project: HE12, Oak &amp; Pine<br/>Vendor: Acme &lt;Supply&gt; '
                                '&amp; Co<br/>Reference: R-77<br/>Amount: $42.00<br/>Memo: Lunch &amp; '
                                '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted By: Sam '
                                "Sub<br/>Submitted Date: 10/02/2026</p><table cellpadding='4' "
                                "cellspacing='0' border='1' style='border-collapse:collapse; "
                                "margin-top:6px;'><tr><th align='left'>Description</th><th "
                                "align='left'>Project</th><th align='left'>Sub Cost Code</th><th "
                                "align='right'>Amount</th></tr><tr><td>Studs &amp; "
                                'plates</td><td>HE12</td><td>01-100 Framing</td><td '
                                "style='text-align:right;'>$1,000.50</td></tr><tr><td></td><td>Oak &amp; "
                                'Pine</td><td>None Drywall &lt;L5&gt;</td><td '
                                "style='text-align:right;'>$200.00</td></tr><tr><td>&lt;b&gt;freight&lt;/b&gt;</td><td></td><td></td><td "
                                "style='text-align:right;'>$0.00</td></tr><tr><td>misc</td><td></td><td></td><td "
                                "style='text-align:right;'>$0.00</td></tr><tr><td>unknown "
                                'project</td><td></td><td></td><td '
                                "style='text-align:right;'>$34.00</td></tr></table><p>Attached: "
                                '<strong>expense.pdf</strong></p><p>When you have a moment, will you please '
                                'reply for approval with Sub Cost Code and Description, or non-approval?</p>',
                        'body_type': 'HTML',
                        'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'},
                                         {'email': 'noname@example.com', 'name': 'noname@example.com'}],
                        'entity_public_id': 'exp-pub-66',
                        'entity_type': 'Expense',
                        'mode': 'draft',
                        'review_id': 9,
                        'subject': '[Review] Acme <Supply> & Co — Expense R-77 — HE12, Oak & Pine — $42.00',
                        'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'},
                                         {'email': 'blank@example.com', 'name': '   Blank'},
                                         {'email': 'zoe@example.com', 'name': 'Zoë'}]}],
 ('expense', 'no_attachment_single_project'): [{'attachment': None,
                                                'bcc_addresses': [{'email': 'invoice@example.com',
                                                                   'name': 'Invoice Inbox (archive)'}],
                                                'body': '<p>Pat/Zoë,</p><p>A new expense has been submitted '
                                                        'for review:</p><p>Project: HE12<br/>Vendor: Acme '
                                                        '&lt;Supply&gt; &amp; Co<br/>Reference: '
                                                        'R-77<br/>Amount: $42.00<br/>Memo: Lunch &amp; '
                                                        '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                                        'By: Sam Sub<br/>Submitted Date: '
                                                        "10/02/2026</p><table cellpadding='4' "
                                                        "cellspacing='0' border='1' "
                                                        "style='border-collapse:collapse; "
                                                        "margin-top:6px;'><tr><th "
                                                        "align='left'>Description</th><th align='left'>Sub "
                                                        'Cost Code</th><th '
                                                        "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                        'Framing</td><td '
                                                        "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                        'you have a moment, will you please reply for '
                                                        'approval with Sub Cost Code and Description, or '
                                                        'non-approval?</p>',
                                                'body_type': 'HTML',
                                                'cc_addresses': [{'email': 'olive@example.com',
                                                                  'name': 'Olive Own'},
                                                                 {'email': 'noname@example.com',
                                                                  'name': 'noname@example.com'}],
                                                'entity_public_id': 'exp-pub-66',
                                                'entity_type': 'Expense',
                                                'mode': 'draft',
                                                'review_id': 9,
                                                'subject': '[Review] Acme <Supply> & Co — Expense R-77 — '
                                                           'HE12 — $42.00',
                                                'to_addresses': [{'email': 'pat@example.com',
                                                                  'name': 'Pat Lee'},
                                                                 {'email': 'blank@example.com',
                                                                  'name': '   Blank'},
                                                                 {'email': 'zoe@example.com',
                                                                  'name': 'Zoë'}]}],
 ('expense', 'sparse_entity'): [{'attachment': None,
                                 'bcc_addresses': [{'email': 'invoice@example.com',
                                                    'name': 'Invoice Inbox (archive)'}],
                                 'body': '<p>Pat/Zoë,</p><p>A new expense has been submitted for '
                                         'review:</p><p>Project: (no project)<br/>Vendor: (unknown '
                                         'vendor)<br/>Reference: (no reference)<br/>Amount: '
                                         '$0.00</p><p>Submitted By: User 27<br/>Submitted Date: '
                                         'garbage</p><p>When you have a moment, will you please reply for '
                                         'approval with Sub Cost Code and Description, or non-approval?</p>',
                                 'body_type': 'HTML',
                                 'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'},
                                                  {'email': 'noname@example.com',
                                                   'name': 'noname@example.com'}],
                                 'entity_public_id': 'exp-pub-66',
                                 'entity_type': 'Expense',
                                 'mode': 'draft',
                                 'review_id': 9,
                                 'subject': '[Review] (unknown vendor) — Expense (no reference) — (no '
                                            'project) — $0.00',
                                 'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'},
                                                  {'email': 'blank@example.com', 'name': '   Blank'},
                                                  {'email': 'zoe@example.com', 'name': 'Zoë'}]}],
 ('expense', 'owners_only'): [{'attachment': None,
                               'bcc_addresses': [],
                               'body': '<p>A new expense has been submitted for review:</p><p>Project: '
                                       'HE12<br/>Vendor: Acme &lt;Supply&gt; &amp; Co<br/>Reference: '
                                       'R-77<br/>Amount: $42.00<br/>Memo: Lunch &amp; '
                                       '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted By: '
                                       "Sam Sub<br/>Submitted Date: 10/02/2026</p><table cellpadding='4' "
                                       "cellspacing='0' border='1' style='border-collapse:collapse; "
                                       "margin-top:6px;'><tr><th align='left'>Description</th><th "
                                       "align='left'>Sub Cost Code</th><th "
                                       "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                       'Framing</td><td '
                                       "style='text-align:right;'>$12.30</td></tr></table><p>When you have a "
                                       'moment, will you please reply for approval with Sub Cost Code and '
                                       'Description, or non-approval?</p>',
                               'body_type': 'HTML',
                               'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'},
                                                {'email': 'noname@example.com',
                                                 'name': 'noname@example.com'}],
                               'entity_public_id': 'exp-pub-66',
                               'entity_type': 'Expense',
                               'mode': 'draft',
                               'review_id': 9,
                               'subject': '[Review] Acme <Supply> & Co — Expense R-77 — HE12 — $42.00',
                               'to_addresses': []}],
 ('expense', 'mode_setting_draft'): [{'attachment': None,
                                      'bcc_addresses': [{'email': 'invoice@example.com',
                                                         'name': 'Invoice Inbox (archive)'}],
                                      'body': '<p>Pat/Zoë,</p><p>A new expense has been submitted for '
                                              'review:</p><p>Project: HE12<br/>Vendor: Acme &lt;Supply&gt; '
                                              '&amp; Co<br/>Reference: R-77<br/>Amount: $42.00<br/>Memo: '
                                              'Lunch &amp; '
                                              '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                              'By: Sam Sub<br/>Submitted Date: 10/02/2026</p><table '
                                              "cellpadding='4' cellspacing='0' border='1' "
                                              "style='border-collapse:collapse; margin-top:6px;'><tr><th "
                                              "align='left'>Description</th><th align='left'>Sub Cost "
                                              'Code</th><th '
                                              "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                              'Framing</td><td '
                                              "style='text-align:right;'>$12.30</td></tr></table><p>When you "
                                              'have a moment, will you please reply for approval with Sub '
                                              'Cost Code and Description, or non-approval?</p>',
                                      'body_type': 'HTML',
                                      'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'},
                                                       {'email': 'noname@example.com',
                                                        'name': 'noname@example.com'}],
                                      'entity_public_id': 'exp-pub-66',
                                      'entity_type': 'Expense',
                                      'mode': 'draft',
                                      'review_id': 9,
                                      'subject': '[Review] Acme <Supply> & Co — Expense R-77 — HE12 — $42.00',
                                      'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'},
                                                       {'email': 'blank@example.com', 'name': '   Blank'},
                                                       {'email': 'zoe@example.com', 'name': 'Zoë'}]}]}

CL_GOLDEN = {'cl_label_fallback_to_id': [{'entity_type': 'ContractLabor',
                              'entity_public_id': 'cl-pub-42',
                              'to_addresses': [],
                              'cc_addresses': [],
                              'bcc_addresses': [{'email': 'invoice@example.com', 'name': None}],
                              'subject': '[Review] Contract Labor - #12 - 2026-10-05',
                              'body': '<p>The following Contract Labor for #12 on 2026-10-05 has been '
                                      'submitted for review. When you have a moment, please review and reply '
                                      'with an approval with sub cost code and description or not approved. '
                                      'The sub cost code you provide will be applied to the full crew listed '
                                      'below.</p><p><b>Flo Park</b><br>Hours: 1.00<br>Is Billable: Yes<br>Is '
                                      'Overhead: No<br>Description: Misc</p>',
                              'body_type': 'HTML',
                              'mode': 'draft'}],
 'cl_multi_worker_multi_line': [{'entity_type': 'ContractLabor',
                                 'entity_public_id': 'cl-pub-42',
                                 'to_addresses': [{'email': 'cassidy@example.com', 'name': 'Cassidy A'},
                                                  {'email': 'zach@example.com', 'name': 'Zach B'},
                                                  {'email': 'cassidy2@example.com', 'name': 'Cassidy C'}],
                                 'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'}],
                                 'bcc_addresses': [{'email': 'invoice@example.com', 'name': None}],
                                 'subject': '[Review] Contract Labor - HE12 - 2026-10-05',
                                 'body': '<p>Cassidy/Zach,</p><p>The following Contract Labor for HE12 on '
                                         '2026-10-05 has been submitted for review. When you have a moment, '
                                         'please review and reply with an approval with sub cost code and '
                                         'description or not approved. The sub cost code you provide will be '
                                         'applied to the full crew listed below.</p><p><b>Ana '
                                         'Diaz</b><br>Hours: 4.50<br>Is Billable: Yes<br>Is Overhead: '
                                         'Yes<br>Description: Drywall<br><br>Hours: 2.00<br>Is Billable: '
                                         'No<br>Is Overhead: No<br>Description: (no '
                                         'description)</p><p><b>Ben &lt;Ort&gt;</b><br>Hours: 6.00<br>Is '
                                         'Billable: No<br>Is Overhead: No<br>Description: (no '
                                         'description)</p>',
                                 'body_type': 'HTML',
                                 'mode': 'draft'},
                                {'entity_type': 'ContractLabor',
                                 'entity_public_id': 'cl-pub-42',
                                 'to_addresses': [],
                                 'cc_addresses': [],
                                 'bcc_addresses': [{'email': 'invoice@example.com', 'name': None}],
                                 'subject': '[Review] Contract Labor - Oak & Pine - 2026-10-05',
                                 'body': '<p>The following Contract Labor for Oak &amp; Pine on 2026-10-05 '
                                         'has been submitted for review. When you have a moment, please '
                                         'review and reply with an approval with sub cost code and '
                                         'description or not approved. The sub cost code you provide will be '
                                         'applied to the full crew listed below.</p><p><b>Cy '
                                         'Dale</b><br>Hours: 0.00<br>Is Billable: Yes<br>Is Overhead: '
                                         'No<br>Description: Trim &amp; paint</p>',
                                 'body_type': 'HTML',
                                 'mode': 'draft'}],
 'cl_no_bcc_when_unconfigured': [{'entity_type': 'ContractLabor',
                                  'entity_public_id': 'cl-pub-42',
                                  'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'}],
                                  'cc_addresses': [],
                                  'bcc_addresses': [],
                                  'subject': '[Review] Contract Labor - HE12 - 2026-10-05',
                                  'body': '<p>Pat,</p><p>The following Contract Labor for HE12 on 2026-10-05 '
                                          'has been submitted for review. When you have a moment, please '
                                          'review and reply with an approval with sub cost code and '
                                          'description or not approved. The sub cost code you provide will '
                                          'be applied to the full crew listed below.</p><p><b>Gus '
                                          'Hart</b><br>Hours: 2.00<br>Is Billable: Yes<br>Is Overhead: '
                                          'No<br>Description: Paint</p>',
                                  'body_type': 'HTML',
                                  'mode': 'draft'}],
 'cl_no_pm_no_greeting': [{'entity_type': 'ContractLabor',
                           'entity_public_id': 'cl-pub-42',
                           'to_addresses': [],
                           'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'}],
                           'bcc_addresses': [{'email': 'invoice@example.com', 'name': None}],
                           'subject': '[Review] Contract Labor - HE12 - 2026-10-05',
                           'body': '<p>The following Contract Labor for HE12 on 2026-10-05 has been '
                                   'submitted for review. When you have a moment, please review and reply '
                                   'with an approval with sub cost code and description or not approved. The '
                                   'sub cost code you provide will be applied to the full crew listed '
                                   'below.</p><p><b>Eve Ng</b><br>Hours: 3.00<br>Is Billable: Yes<br>Is '
                                   'Overhead: Yes<br>Description: Cleanup</p>',
                           'body_type': 'HTML',
                           'mode': 'draft'}],
 'cl_single_worker': [{'entity_type': 'ContractLabor',
                       'entity_public_id': 'cl-pub-42',
                       'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'}],
                       'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'}],
                       'bcc_addresses': [{'email': 'invoice@example.com', 'name': None}],
                       'subject': '[Review] Contract Labor - HE12 - 2026-10-05',
                       'body': '<p>Pat,</p><p>The following Contract Labor for HE12 on 2026-10-05 has been '
                               'submitted for review. When you have a moment, please review and reply with '
                               'an approval with sub cost code and description or not approved. The sub cost '
                               'code you provide will be applied to the full crew listed below.</p><p><b>Ana '
                               'Diaz</b><br>Hours: 8.00<br>Is Billable: Yes<br>Is Overhead: '
                               'No<br>Description: Framing</p>',
                       'body_type': 'HTML',
                       'mode': 'draft'}]}
