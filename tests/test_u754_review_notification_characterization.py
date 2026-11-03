"""U-754 — characterization of the bill + expense review-submit notification.

Written against the UNCHANGED `ReviewNotificationService` (md5 b6453c58…) before
the bill/expense pipelines were merged into one spec-driven path. Every
observable effect of `enqueue_for_bill` / `enqueue_for_expense` is captured
into one ordered record and compared to FROZEN literals:

  - every collaborator method call, in order, with its exact args/kwargs
    (recipient resolution incl. the excluded submitter, vendor / line-item /
    project / user / attachment-link / attachment / sub-cost-code reads, the
    outbox enqueue with the FULL subject + HTML body + attachment reference +
    mode, the status reads, and the `ReviewService.create` advance);
  - every log record this module emits (level, rendered message, exc_info);
  - the return value.

The goldens are literals on purpose: regenerating them at test time would make
the test agree with whatever the code does. If one of these fails after a
refactor, the refactor changed behaviour.

No DB, no Graph, no network: every collaborator is patched at its SOURCE module
(the service imports them lazily, so that is where it resolves them).
"""

import logging
from contextlib import ExitStack
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business.notification_service import ReviewNotificationService
from entities.review.business.recipient_model import ResolvedRecipient

LOGGER_NAME = "entities.review.business.notification_service"
SYSTEM_ACTOR = 9001


# ---------------------------------------------------------------------------
# fixtures (plain data)
# ---------------------------------------------------------------------------


def _recipient(user_id, first, last, email, role, project_id=10):
    return ResolvedRecipient(
        user_id=user_id, firstname=first, lastname=last, email=email,
        role_name=role, project_id=project_id,
    )


ALL_RECIPIENTS = [
    _recipient(1, "Pat", "Lee", "pat@example.com", "Project Manager"),
    _recipient(2, "Quinn", "Ray", None, "Project Manager"),            # unreachable PM
    _recipient(3, "  ", "Blank", "blank@example.com", "Project Manager"),  # no greeting name
    _recipient(4, "Zoë", None, "zoe@example.com", "Project Manager", 11),
    _recipient(5, "Olive", "Own", "olive@example.com", "Owner"),
    _recipient(6, "Oscar", "Gone", None, "Owner"),                    # unreachable owner
    _recipient(7, None, None, "noname@example.com", "Owner"),
    _recipient(8, "Ignored", "Role", "x@example.com", "Bookkeeper"),  # neither bucket
]
OWNERS_ONLY = [r for r in ALL_RECIPIENTS if r.role_name == "Owner"]
UNREACHABLE_ONLY = [r for r in ALL_RECIPIENTS if r.email is None]

PROJECTS = {
    10: SimpleNamespace(id=10, abbreviation="HE12", name="Hidden Elm 12"),
    11: SimpleNamespace(id=11, abbreviation=None, name="Oak & Pine"),
    12: SimpleNamespace(id=12, abbreviation="", name=""),             # label-less
}
SCCS = {
    100: SimpleNamespace(number="01-100", name="Framing"),
    101: SimpleNamespace(number=None, name="Drywall <L5>"),
    102: SimpleNamespace(number=None, name=None),
}


def _line(i, *, project_id, scc_id, description, amount):
    return SimpleNamespace(
        id=i, public_id=f"li-pub-{i}", project_id=project_id,
        sub_cost_code_id=scc_id, description=description, amount=amount,
    )


MULTI_LINES = [
    _line(1, project_id=10, scc_id=100, description="Studs & plates", amount=Decimal("1000.5")),
    _line(2, project_id=11, scc_id=101, description=None, amount="200"),
    _line(3, project_id=None, scc_id=None, description="<b>freight</b>", amount=None),
    _line(4, project_id=12, scc_id=102, description="misc", amount="bogus"),
    _line(5, project_id=99, scc_id=999, description="unknown project", amount=34),
]
SINGLE_LINE = [_line(1, project_id=10, scc_id=100, description="Studs", amount="12.3")]

# Line 1 → non-PDF (skipped, logged); line 2 → no link; line 3 → PDF with no
# blob_url (skipped silently); line 4 → the PDF that wins; line 5 never read.
LINKS_WITH_PDF = {
    "li-pub-1": 901, "li-pub-3": 903, "li-pub-4": 904, "li-pub-5": 905,
}
ATTACHMENTS = {
    901: SimpleNamespace(public_id="att-901", blob_url="b/901.jpg", content_type="image/JPEG", filename="scan.jpg"),
    903: SimpleNamespace(public_id="att-903", blob_url=None, content_type="application/pdf", filename="nourl.pdf"),
    904: SimpleNamespace(public_id="att-904", blob_url="b/904.pdf", content_type="Application/PDF", filename=None),
    905: SimpleNamespace(public_id="att-905", blob_url="b/905.pdf", content_type="application/pdf", filename="later.pdf"),
}
STATUSES = [
    SimpleNamespace(id=1, name="Submitted", sort_order=10),
    SimpleNamespace(id=2, name="In Review", sort_order=20),
]


def _bill(**over):
    base = dict(
        id=55, public_id="bill-pub-55", vendor_id=7, bill_number="INV-1001",
        total_amount=Decimal("1234.5"), created_datetime="2026-10-01 09:15:00",
        memo="bills never render a memo",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _expense(**over):
    base = dict(
        id=66, public_id="exp-pub-66", vendor_id=8, reference_number="R-77",
        total_amount="42", created_datetime=datetime(2026, 10, 2, 8, 0),
        memo="  Lunch & <tools>\r\nsecond\rthird\nfourth  ",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _review(**over):
    base = dict(id=9, user_id=27, review_status_id=1)
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _plain(value):
    """Reduce call arguments to comparable literals."""
    if isinstance(value, SimpleNamespace):
        return {"ns": {k: _plain(v) for k, v in sorted(vars(value).items())}}
    if isinstance(value, ResolvedRecipient):
        return {"recipient": value.user_id}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (Decimal, datetime)):
        return repr(value)
    return value


def _harness(
    *,
    recipients,
    line_items,
    links,
    inbox="invoice@example.com",
    mode_setting="send",
    vendor=SimpleNamespace(name="Acme <Supply> & Co"),
    submitter=SimpleNamespace(firstname="Sam", lastname="Sub"),
    outbox_result=SimpleNamespace(public_id="ob-1"),
    next_status=STATUSES[1],
    read_all=None,
    vendor_error=None,
):
    calls = []

    def rec(name, result=None, raises=None):
        def _fn(*args, **kwargs):
            calls.append([name, _plain(list(args)), _plain(dict(sorted(kwargs.items())))])
            if raises is not None:
                raise raises
            return result(*args, **kwargs) if callable(result) else result
        return MagicMock(side_effect=_fn)

    stack = ExitStack()

    def patch_cls(target, **methods):
        cls = stack.enter_context(patch(target))
        for meth, mock in methods.items():
            setattr(cls.return_value, meth, mock)
        return cls

    settings = patch_cls("config.Settings")
    settings.return_value.invoice_inbox_email = inbox
    settings.return_value.review_notification_mode = mode_setting

    patch_cls(
        "entities.review.business.recipient_service.ReviewRecipientRepository",
        resolve_for_bill=rec("recipients.bill", list(recipients)),
        resolve_for_expense=rec("recipients.expense_via_recipient_repo", list(recipients)),
    )
    patch_cls(
        "entities.review.persistence.repo.ReviewRepository",
        resolve_review_recipients_by_expense_id=rec("recipients.expense", list(recipients)),
    )
    patch_cls(
        "entities.vendor.business.service.VendorService",
        read_by_id=rec("vendor.read_by_id", vendor, raises=vendor_error),
    )
    patch_cls(
        "entities.bill_line_item.business.service.BillLineItemService",
        read_by_bill_id=rec("bill_lines.read_by_bill_id", list(line_items)),
    )
    patch_cls(
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        read_by_expense_id=rec("expense_lines.read_by_expense_id", list(line_items)),
    )

    def link_for(*args, **kwargs):
        key = args[0] if args else kwargs["expense_line_item_public_id"]
        att = links.get(key)
        return SimpleNamespace(attachment_id=att) if att else None

    patch_cls(
        "entities.bill_line_item_attachment.business.service.BillLineItemAttachmentService",
        read_by_bill_line_item_id=rec("bla.read_by_bill_line_item_id", link_for),
    )
    patch_cls(
        "entities.expense_line_item_attachment.business.service.ExpenseLineItemAttachmentService",
        read_by_expense_line_item_id=rec("elia.read_by_expense_line_item_id", link_for),
    )
    patch_cls(
        "entities.attachment.business.service.AttachmentService",
        read_by_id=rec("attachment.read_by_id", lambda i: ATTACHMENTS.get(i)),
    )
    patch_cls(
        "entities.project.business.service.ProjectService",
        read_by_id=rec("project.read_by_id", lambda i: PROJECTS.get(i)),
    )
    patch_cls(
        "entities.sub_cost_code.business.service.SubCostCodeService",
        read_by_id=rec("scc.read_by_id", lambda i: SCCS.get(i)),
    )
    patch_cls(
        "entities.user.business.service.UserService",
        read_by_id=rec("user.read_by_id", submitter),
    )
    patch_cls(
        "integrations.ms.outbox.business.service.MsOutboxService",
        enqueue_send_mail=rec("outbox.enqueue_send_mail", outbox_result),
    )
    patch_cls(
        "entities.review_status.business.service.ReviewStatusService",
        read_all=rec("status.read_all", list(STATUSES), raises=read_all),
        get_next_intermediate_status=rec("status.get_next_intermediate_status", next_status),
    )
    patch_cls(
        "entities.review.business.service.ReviewService",
        create=rec("review.create", SimpleNamespace(id=10)),
    )
    stack.enter_context(
        patch("shared.authz.system_actor_user_id", side_effect=lambda: SYSTEM_ACTOR)
    )
    return stack, calls


def _capture(caplog, entity, **harness_kwargs):
    review = harness_kwargs.pop("review", None) or _review()
    stack, calls = _harness(**harness_kwargs)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME), stack:
        if entity.public_id.startswith("bill"):
            result = ReviewNotificationService().enqueue_for_bill(
                bill=entity, review=review, exclude_user_id=27,
            )
        else:
            result = ReviewNotificationService().enqueue_for_expense(
                expense=entity, review=review, exclude_user_id=27,
            )
    logs = [
        [r.levelname, r.getMessage(), bool(r.exc_info)]
        for r in caplog.records
        if r.name == LOGGER_NAME
    ]
    return {"result": result, "calls": calls, "logs": logs}


SCENARIOS = {
    "full": dict(recipients=ALL_RECIPIENTS, line_items=MULTI_LINES, links=LINKS_WITH_PDF),
    "no_attachment_single_project": dict(recipients=ALL_RECIPIENTS, line_items=SINGLE_LINE, links={}),
    "zero_recipients_no_bcc": dict(recipients=[], line_items=MULTI_LINES, links={}, inbox=None),
    "unreachable_only_no_bcc": dict(recipients=UNREACHABLE_ONLY, line_items=[], links={}, inbox=""),
    "bcc_only": dict(recipients=[], line_items=SINGLE_LINE, links={}),
    "owners_only": dict(recipients=OWNERS_ONLY, line_items=SINGLE_LINE, links={}, inbox=None),
    "outbox_refused": dict(recipients=ALL_RECIPIENTS, line_items=SINGLE_LINE, links={}, outbox_result=None),
    "status_unresolved": dict(recipients=ALL_RECIPIENTS, line_items=[], links={}, review=_review(review_status_id=404)),
    "in_review_missing": dict(recipients=ALL_RECIPIENTS, line_items=[], links={}, next_status=None),
    "advance_raises": dict(recipients=ALL_RECIPIENTS, line_items=[], links={}, read_all=RuntimeError("db down")),
    "enqueue_raises": dict(recipients=ALL_RECIPIENTS, line_items=[], links={}, vendor_error=RuntimeError("vendor read boom")),
    "sparse_entity": dict(
        recipients=ALL_RECIPIENTS, line_items=[], links={},
        vendor=None, submitter=SimpleNamespace(firstname=" ", lastname=None),
    ),
    "submitter_missing": dict(
        recipients=ALL_RECIPIENTS, line_items=SINGLE_LINE, links={},
        vendor=SimpleNamespace(name=""), submitter=None,
    ),
    "mode_setting_draft": dict(
        recipients=ALL_RECIPIENTS, line_items=SINGLE_LINE, links={}, mode_setting="draft",
    ),
}

ENTITY_OVERRIDES = {
    "sparse_entity": dict(
        bill=dict(vendor_id=None, bill_number=None, total_amount=None, created_datetime=None),
        expense=dict(vendor_id=None, reference_number=None, total_amount=None, created_datetime="garbage", memo="   "),
    ),
    "submitter_missing": dict(
        bill=dict(created_datetime="2026-10-01T07:00:00"),
        expense=dict(created_datetime="2026-10-03", memo=None),
    ),
}

SUBMITTER_NONE_REVIEW = {"submitter_missing": _review(user_id=None)}


def _run(caplog, kind, name):
    kwargs = dict(SCENARIOS[name])
    if name in SUBMITTER_NONE_REVIEW:
        kwargs["review"] = SUBMITTER_NONE_REVIEW[name]
    over = ENTITY_OVERRIDES.get(name, {}).get(kind, {})
    entity = _bill(**over) if kind == "bill" else _expense(**over)
    return _capture(caplog, entity, **kwargs)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("kind", ["bill", "expense"])
def test_enqueue_is_characterized(caplog, kind, name):
    assert _run(caplog, kind, name) == GOLDEN[kind][name]


def test_the_mode_setting_never_changes_anything():
    """`REVIEW_NOTIFICATION_MODE` is bypassed on both paths: the golden for the
    "draft" setting equals the "send"-setting run of the same scenario apart
    from nothing at all."""
    for kind in ("bill", "expense"):
        a = GOLDEN[kind]["mode_setting_draft"]
        b = GOLDEN[kind]["no_attachment_single_project"]
        assert a == b


def test_every_scenario_has_a_golden():
    for kind in ("bill", "expense"):
        assert sorted(GOLDEN[kind]) == sorted(SCENARIOS)


# ---------------------------------------------------------------------------
# FROZEN goldens — captured once from the UNCHANGED service (md5
# b6453c58d237d75baaa09b9340ba21d7) and pasted as literals. Never regenerate
# these to make a refactor pass; a diff here IS a behaviour change.
# ---------------------------------------------------------------------------

# fmt: off
GOLDEN = {'bill': {'advance_raises': {'result': None,
                             'calls': [['recipients.bill',
                                        [],
                                        {'bill_id': 55, 'exclude_user_id': 27}],
                                       ['vendor.read_by_id', [7], {}],
                                       ['bill_lines.read_by_bill_id', [55], {}],
                                       ['user.read_by_id', [27], {}],
                                       ['outbox.enqueue_send_mail',
                                        [],
                                        {'attachment': None,
                                         'bcc_addresses': [{'email': 'invoice@example.com',
                                                            'name': 'Invoice Inbox (archive)'}],
                                         'bill_id': 55,
                                         'body': '<p>Pat/Zoë,</p><p>A new bill has been submitted '
                                                 'for review:</p><p>Project: (no '
                                                 'project)<br/>Vendor: Acme &lt;Supply&gt; &amp; '
                                                 'Co<br/>Number: INV-1001<br/>Amount: '
                                                 '$1,234.50</p><p>Submitted By: Sam '
                                                 'Sub<br/>Submitted Date: 10/01/2026</p><p>When '
                                                 'you have a moment, will you please reply for '
                                                 'approval with Sub Cost Code and Description, or '
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
                                         'subject': '[Review] Acme <Supply> & Co — Bill #INV-1001 '
                                                    '— (no project) — $1,234.50',
                                         'to_addresses': [{'email': 'pat@example.com',
                                                           'name': 'Pat Lee'},
                                                          {'email': 'blank@example.com',
                                                           'name': '   Blank'},
                                                          {'email': 'zoe@example.com',
                                                           'name': 'Zoë'}]}],
                                       ['status.read_all', [], {}]],
                             'logs': [['WARNING',
                                       'review_notification.unreachable_recipients '
                                       'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                       'reason=no_contact_email',
                                       False],
                                      ['INFO',
                                       'review_notification.enqueued bill_public_id=bill-pub-55 '
                                       'outbox_public_id=ob-1 mode=draft to=3 cc=2 bcc=1 '
                                       'attachment=(none)',
                                       False],
                                      ['ERROR',
                                       'review_notification.in_review_advance_failed '
                                       'bill_public_id=bill-pub-55: db down',
                                       True]]},
          'bcc_only': {'result': None,
                       'calls': [['recipients.bill', [], {'bill_id': 55, 'exclude_user_id': 27}],
                                 ['vendor.read_by_id', [7], {}],
                                 ['bill_lines.read_by_bill_id', [55], {}],
                                 ['project.read_by_id', [10], {}],
                                 ['user.read_by_id', [27], {}],
                                 ['bla.read_by_bill_line_item_id', ['li-pub-1'], {}],
                                 ['scc.read_by_id', [100], {}],
                                 ['outbox.enqueue_send_mail',
                                  [],
                                  {'attachment': None,
                                   'bcc_addresses': [{'email': 'invoice@example.com',
                                                      'name': 'Invoice Inbox (archive)'}],
                                   'bill_id': 55,
                                   'body': '<p>A new bill has been submitted for '
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
                                   'cc_addresses': [],
                                   'entity_public_id': 'bill-pub-55',
                                   'entity_type': 'Bill',
                                   'mode': 'draft',
                                   'review_id': 9,
                                   'subject': '[Review] Acme <Supply> & Co — Bill #INV-1001 — HE12 '
                                              '— $1,234.50',
                                   'to_addresses': []}]],
                       'logs': [['WARNING',
                                 'review_notification.no_to_recipient bill_public_id=bill-pub-55 '
                                 'reason=no_project_manager_with_email — sending anyway (BCC '
                                 'archive still active).',
                                 False],
                                ['INFO',
                                 'review_notification.no_cc_recipient bill_public_id=bill-pub-55 '
                                 'reason=no_owner_with_email',
                                 False],
                                ['INFO',
                                 'review_notification.enqueued bill_public_id=bill-pub-55 '
                                 'outbox_public_id=ob-1 mode=draft to=0 cc=0 bcc=1 '
                                 'attachment=(none)',
                                 False]]},
          'enqueue_raises': {'result': None,
                             'calls': [['recipients.bill',
                                        [],
                                        {'bill_id': 55, 'exclude_user_id': 27}],
                                       ['vendor.read_by_id', [7], {}]],
                             'logs': [['WARNING',
                                       'review_notification.unreachable_recipients '
                                       'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                       'reason=no_contact_email',
                                       False],
                                      ['ERROR',
                                       'review_notification.enqueue_failed '
                                       'bill_public_id=bill-pub-55 review_id=9: vendor read boom',
                                       True]]},
          'full': {'result': None,
                   'calls': [['recipients.bill', [], {'bill_id': 55, 'exclude_user_id': 27}],
                             ['vendor.read_by_id', [7], {}],
                             ['bill_lines.read_by_bill_id', [55], {}],
                             ['project.read_by_id', [10], {}],
                             ['project.read_by_id', [11], {}],
                             ['project.read_by_id', [12], {}],
                             ['project.read_by_id', [99], {}],
                             ['user.read_by_id', [27], {}],
                             ['bla.read_by_bill_line_item_id', ['li-pub-1'], {}],
                             ['attachment.read_by_id', [901], {}],
                             ['bla.read_by_bill_line_item_id', ['li-pub-2'], {}],
                             ['bla.read_by_bill_line_item_id', ['li-pub-3'], {}],
                             ['attachment.read_by_id', [903], {}],
                             ['bla.read_by_bill_line_item_id', ['li-pub-4'], {}],
                             ['attachment.read_by_id', [904], {}],
                             ['scc.read_by_id', [100], {}],
                             ['scc.read_by_id', [101], {}],
                             ['scc.read_by_id', [102], {}],
                             ['scc.read_by_id', [999], {}],
                             ['project.read_by_id', [10], {}],
                             ['project.read_by_id', [11], {}],
                             ['project.read_by_id', [12], {}],
                             ['project.read_by_id', [99], {}],
                             ['outbox.enqueue_send_mail',
                              [],
                              {'attachment': {'name': 'bill.pdf',
                                              'content_type': 'Application/PDF',
                                              'blob_url': 'b/904.pdf'},
                               'bcc_addresses': [{'email': 'invoice@example.com',
                                                  'name': 'Invoice Inbox (archive)'}],
                               'bill_id': 55,
                               'body': '<p>Pat/Zoë,</p><p>A new bill has been submitted for '
                                       'review:</p><p>Project: HE12, Oak &amp; Pine<br/>Vendor: '
                                       'Acme &lt;Supply&gt; &amp; Co<br/>Number: '
                                       'INV-1001<br/>Amount: $1,234.50</p><p>Submitted By: Sam '
                                       'Sub<br/>Submitted Date: 10/01/2026</p><table '
                                       "cellpadding='4' cellspacing='0' border='1' "
                                       "style='border-collapse:collapse; margin-top:6px;'><tr><th "
                                       "align='left'>Description</th><th "
                                       "align='left'>Project</th><th align='left'>Sub Cost "
                                       "Code</th><th align='right'>Amount</th></tr><tr><td>Studs "
                                       '&amp; plates</td><td>HE12</td><td>01-100 Framing</td><td '
                                       "style='text-align:right;'>$1,000.50</td></tr><tr><td></td><td>Oak "
                                       '&amp; Pine</td><td>None Drywall &lt;L5&gt;</td><td '
                                       "style='text-align:right;'>$200.00</td></tr><tr><td>&lt;b&gt;freight&lt;/b&gt;</td><td></td><td></td><td "
                                       "style='text-align:right;'>$0.00</td></tr><tr><td>misc</td><td></td><td></td><td "
                                       "style='text-align:right;'>$0.00</td></tr><tr><td>unknown "
                                       'project</td><td></td><td></td><td '
                                       "style='text-align:right;'>$34.00</td></tr></table><p>Attached: "
                                       '<strong>bill.pdf</strong></p><p>When you have a moment, '
                                       'will you please reply for approval with Sub Cost Code and '
                                       'Description, or non-approval?</p>',
                               'body_type': 'HTML',
                               'cc_addresses': [{'email': 'olive@example.com', 'name': 'Olive Own'},
                                                {'email': 'noname@example.com',
                                                 'name': 'noname@example.com'}],
                               'entity_public_id': 'bill-pub-55',
                               'entity_type': 'Bill',
                               'mode': 'draft',
                               'review_id': 9,
                               'subject': '[Review] Acme <Supply> & Co — Bill #INV-1001 — HE12, '
                                          'Oak & Pine — $1,234.50',
                               'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'},
                                                {'email': 'blank@example.com', 'name': '   Blank'},
                                                {'email': 'zoe@example.com', 'name': 'Zoë'}]}],
                             ['status.read_all', [], {}],
                             ['status.get_next_intermediate_status',
                              [10],
                              {'statuses': [{'ns': {'id': 1,
                                                    'name': 'Submitted',
                                                    'sort_order': 10}},
                                            {'ns': {'id': 2,
                                                    'name': 'In Review',
                                                    'sort_order': 20}}]}],
                             ['review.create',
                              [],
                              {'bill_id': 55,
                               'comments': None,
                               'created_by_user_id': 9001,
                               'email_message_id': None,
                               'review_status_id': 2,
                               'user_id': 27}]],
                   'logs': [['WARNING',
                             'review_notification.unreachable_recipients '
                             'bill_public_id=bill-pub-55 user_ids=[2, 6] reason=no_contact_email',
                             False],
                            ['INFO',
                             'review_notification.attachment_skipped_non_pdf '
                             'bill_public_id=bill-pub-55 attachment_public_id=att-901 '
                             'content_type=image/JPEG',
                             False],
                            ['INFO',
                             'review_notification.enqueued bill_public_id=bill-pub-55 '
                             'outbox_public_id=ob-1 mode=draft to=3 cc=2 bcc=1 attachment=bill.pdf',
                             False],
                            ['INFO',
                             'review_notification.in_review_advanced bill_public_id=bill-pub-55',
                             False]]},
          'in_review_missing': {'result': None,
                                'calls': [['recipients.bill',
                                           [],
                                           {'bill_id': 55, 'exclude_user_id': 27}],
                                          ['vendor.read_by_id', [7], {}],
                                          ['bill_lines.read_by_bill_id', [55], {}],
                                          ['user.read_by_id', [27], {}],
                                          ['outbox.enqueue_send_mail',
                                           [],
                                           {'attachment': None,
                                            'bcc_addresses': [{'email': 'invoice@example.com',
                                                               'name': 'Invoice Inbox (archive)'}],
                                            'bill_id': 55,
                                            'body': '<p>Pat/Zoë,</p><p>A new bill has been '
                                                    'submitted for review:</p><p>Project: (no '
                                                    'project)<br/>Vendor: Acme &lt;Supply&gt; '
                                                    '&amp; Co<br/>Number: INV-1001<br/>Amount: '
                                                    '$1,234.50</p><p>Submitted By: Sam '
                                                    'Sub<br/>Submitted Date: 10/01/2026</p><p>When '
                                                    'you have a moment, will you please reply for '
                                                    'approval with Sub Cost Code and Description, '
                                                    'or non-approval?</p>',
                                            'body_type': 'HTML',
                                            'cc_addresses': [{'email': 'olive@example.com',
                                                              'name': 'Olive Own'},
                                                             {'email': 'noname@example.com',
                                                              'name': 'noname@example.com'}],
                                            'entity_public_id': 'bill-pub-55',
                                            'entity_type': 'Bill',
                                            'mode': 'draft',
                                            'review_id': 9,
                                            'subject': '[Review] Acme <Supply> & Co — Bill '
                                                       '#INV-1001 — (no project) — $1,234.50',
                                            'to_addresses': [{'email': 'pat@example.com',
                                                              'name': 'Pat Lee'},
                                                             {'email': 'blank@example.com',
                                                              'name': '   Blank'},
                                                             {'email': 'zoe@example.com',
                                                              'name': 'Zoë'}]}],
                                          ['status.read_all', [], {}],
                                          ['status.get_next_intermediate_status',
                                           [10],
                                           {'statuses': [{'ns': {'id': 1,
                                                                 'name': 'Submitted',
                                                                 'sort_order': 10}},
                                                         {'ns': {'id': 2,
                                                                 'name': 'In Review',
                                                                 'sort_order': 20}}]}]],
                                'logs': [['WARNING',
                                          'review_notification.unreachable_recipients '
                                          'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                          'reason=no_contact_email',
                                          False],
                                         ['INFO',
                                          'review_notification.enqueued bill_public_id=bill-pub-55 '
                                          'outbox_public_id=ob-1 mode=draft to=3 cc=2 bcc=1 '
                                          'attachment=(none)',
                                          False],
                                         ['INFO',
                                          'review_notification.in_review_status_missing '
                                          'bill_public_id=bill-pub-55 no active non-declined '
                                          'non-final ReviewStatus after sort_order=10',
                                          False]]},
          'mode_setting_draft': {'result': None,
                                 'calls': [['recipients.bill',
                                            [],
                                            {'bill_id': 55, 'exclude_user_id': 27}],
                                           ['vendor.read_by_id', [7], {}],
                                           ['bill_lines.read_by_bill_id', [55], {}],
                                           ['project.read_by_id', [10], {}],
                                           ['user.read_by_id', [27], {}],
                                           ['bla.read_by_bill_line_item_id', ['li-pub-1'], {}],
                                           ['scc.read_by_id', [100], {}],
                                           ['outbox.enqueue_send_mail',
                                            [],
                                            {'attachment': None,
                                             'bcc_addresses': [{'email': 'invoice@example.com',
                                                                'name': 'Invoice Inbox (archive)'}],
                                             'bill_id': 55,
                                             'body': '<p>Pat/Zoë,</p><p>A new bill has been '
                                                     'submitted for review:</p><p>Project: '
                                                     'HE12<br/>Vendor: Acme &lt;Supply&gt; &amp; '
                                                     'Co<br/>Number: INV-1001<br/>Amount: '
                                                     '$1,234.50</p><p>Submitted By: Sam '
                                                     'Sub<br/>Submitted Date: 10/01/2026</p><table '
                                                     "cellpadding='4' cellspacing='0' border='1' "
                                                     "style='border-collapse:collapse; "
                                                     "margin-top:6px;'><tr><th "
                                                     "align='left'>Description</th><th "
                                                     "align='left'>Sub Cost Code</th><th "
                                                     "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                     'Framing</td><td '
                                                     "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                     'you have a moment, will you please reply for '
                                                     'approval with Sub Cost Code and Description, '
                                                     'or non-approval?</p>',
                                             'body_type': 'HTML',
                                             'cc_addresses': [{'email': 'olive@example.com',
                                                               'name': 'Olive Own'},
                                                              {'email': 'noname@example.com',
                                                               'name': 'noname@example.com'}],
                                             'entity_public_id': 'bill-pub-55',
                                             'entity_type': 'Bill',
                                             'mode': 'draft',
                                             'review_id': 9,
                                             'subject': '[Review] Acme <Supply> & Co — Bill '
                                                        '#INV-1001 — HE12 — $1,234.50',
                                             'to_addresses': [{'email': 'pat@example.com',
                                                               'name': 'Pat Lee'},
                                                              {'email': 'blank@example.com',
                                                               'name': '   Blank'},
                                                              {'email': 'zoe@example.com',
                                                               'name': 'Zoë'}]}],
                                           ['status.read_all', [], {}],
                                           ['status.get_next_intermediate_status',
                                            [10],
                                            {'statuses': [{'ns': {'id': 1,
                                                                  'name': 'Submitted',
                                                                  'sort_order': 10}},
                                                          {'ns': {'id': 2,
                                                                  'name': 'In Review',
                                                                  'sort_order': 20}}]}],
                                           ['review.create',
                                            [],
                                            {'bill_id': 55,
                                             'comments': None,
                                             'created_by_user_id': 9001,
                                             'email_message_id': None,
                                             'review_status_id': 2,
                                             'user_id': 27}]],
                                 'logs': [['WARNING',
                                           'review_notification.unreachable_recipients '
                                           'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                           'reason=no_contact_email',
                                           False],
                                          ['INFO',
                                           'review_notification.enqueued '
                                           'bill_public_id=bill-pub-55 outbox_public_id=ob-1 '
                                           'mode=draft to=3 cc=2 bcc=1 attachment=(none)',
                                           False],
                                          ['INFO',
                                           'review_notification.in_review_advanced '
                                           'bill_public_id=bill-pub-55',
                                           False]]},
          'no_attachment_single_project': {'result': None,
                                           'calls': [['recipients.bill',
                                                      [],
                                                      {'bill_id': 55, 'exclude_user_id': 27}],
                                                     ['vendor.read_by_id', [7], {}],
                                                     ['bill_lines.read_by_bill_id', [55], {}],
                                                     ['project.read_by_id', [10], {}],
                                                     ['user.read_by_id', [27], {}],
                                                     ['bla.read_by_bill_line_item_id',
                                                      ['li-pub-1'],
                                                      {}],
                                                     ['scc.read_by_id', [100], {}],
                                                     ['outbox.enqueue_send_mail',
                                                      [],
                                                      {'attachment': None,
                                                       'bcc_addresses': [{'email': 'invoice@example.com',
                                                                          'name': 'Invoice Inbox '
                                                                                  '(archive)'}],
                                                       'bill_id': 55,
                                                       'body': '<p>Pat/Zoë,</p><p>A new bill has '
                                                               'been submitted for '
                                                               'review:</p><p>Project: '
                                                               'HE12<br/>Vendor: Acme '
                                                               '&lt;Supply&gt; &amp; '
                                                               'Co<br/>Number: '
                                                               'INV-1001<br/>Amount: '
                                                               '$1,234.50</p><p>Submitted By: Sam '
                                                               'Sub<br/>Submitted Date: '
                                                               '10/01/2026</p><table '
                                                               "cellpadding='4' cellspacing='0' "
                                                               "border='1' "
                                                               "style='border-collapse:collapse; "
                                                               "margin-top:6px;'><tr><th "
                                                               "align='left'>Description</th><th "
                                                               "align='left'>Sub Cost Code</th><th "
                                                               "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                               'Framing</td><td '
                                                               "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                               'you have a moment, will you please '
                                                               'reply for approval with Sub Cost '
                                                               'Code and Description, or '
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
                                                       'subject': '[Review] Acme <Supply> & Co — '
                                                                  'Bill #INV-1001 — HE12 — '
                                                                  '$1,234.50',
                                                       'to_addresses': [{'email': 'pat@example.com',
                                                                         'name': 'Pat Lee'},
                                                                        {'email': 'blank@example.com',
                                                                         'name': '   Blank'},
                                                                        {'email': 'zoe@example.com',
                                                                         'name': 'Zoë'}]}],
                                                     ['status.read_all', [], {}],
                                                     ['status.get_next_intermediate_status',
                                                      [10],
                                                      {'statuses': [{'ns': {'id': 1,
                                                                            'name': 'Submitted',
                                                                            'sort_order': 10}},
                                                                    {'ns': {'id': 2,
                                                                            'name': 'In Review',
                                                                            'sort_order': 20}}]}],
                                                     ['review.create',
                                                      [],
                                                      {'bill_id': 55,
                                                       'comments': None,
                                                       'created_by_user_id': 9001,
                                                       'email_message_id': None,
                                                       'review_status_id': 2,
                                                       'user_id': 27}]],
                                           'logs': [['WARNING',
                                                     'review_notification.unreachable_recipients '
                                                     'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                                     'reason=no_contact_email',
                                                     False],
                                                    ['INFO',
                                                     'review_notification.enqueued '
                                                     'bill_public_id=bill-pub-55 '
                                                     'outbox_public_id=ob-1 mode=draft to=3 cc=2 '
                                                     'bcc=1 attachment=(none)',
                                                     False],
                                                    ['INFO',
                                                     'review_notification.in_review_advanced '
                                                     'bill_public_id=bill-pub-55',
                                                     False]]},
          'outbox_refused': {'result': None,
                             'calls': [['recipients.bill',
                                        [],
                                        {'bill_id': 55, 'exclude_user_id': 27}],
                                       ['vendor.read_by_id', [7], {}],
                                       ['bill_lines.read_by_bill_id', [55], {}],
                                       ['project.read_by_id', [10], {}],
                                       ['user.read_by_id', [27], {}],
                                       ['bla.read_by_bill_line_item_id', ['li-pub-1'], {}],
                                       ['scc.read_by_id', [100], {}],
                                       ['outbox.enqueue_send_mail',
                                        [],
                                        {'attachment': None,
                                         'bcc_addresses': [{'email': 'invoice@example.com',
                                                            'name': 'Invoice Inbox (archive)'}],
                                         'bill_id': 55,
                                         'body': '<p>Pat/Zoë,</p><p>A new bill has been submitted '
                                                 'for review:</p><p>Project: HE12<br/>Vendor: Acme '
                                                 '&lt;Supply&gt; &amp; Co<br/>Number: '
                                                 'INV-1001<br/>Amount: $1,234.50</p><p>Submitted '
                                                 'By: Sam Sub<br/>Submitted Date: '
                                                 "10/01/2026</p><table cellpadding='4' "
                                                 "cellspacing='0' border='1' "
                                                 "style='border-collapse:collapse; "
                                                 "margin-top:6px;'><tr><th "
                                                 "align='left'>Description</th><th "
                                                 "align='left'>Sub Cost Code</th><th "
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
                                         'entity_public_id': 'bill-pub-55',
                                         'entity_type': 'Bill',
                                         'mode': 'draft',
                                         'review_id': 9,
                                         'subject': '[Review] Acme <Supply> & Co — Bill #INV-1001 '
                                                    '— HE12 — $1,234.50',
                                         'to_addresses': [{'email': 'pat@example.com',
                                                           'name': 'Pat Lee'},
                                                          {'email': 'blank@example.com',
                                                           'name': '   Blank'},
                                                          {'email': 'zoe@example.com',
                                                           'name': 'Zoë'}]}]],
                             'logs': [['WARNING',
                                       'review_notification.unreachable_recipients '
                                       'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                       'reason=no_contact_email',
                                       False],
                                      ['INFO',
                                       'review_notification.enqueue_refused '
                                       'bill_public_id=bill-pub-55 reason=ms_writes_gate',
                                       False]]},
          'owners_only': {'result': None,
                          'calls': [['recipients.bill', [], {'bill_id': 55, 'exclude_user_id': 27}],
                                    ['vendor.read_by_id', [7], {}],
                                    ['bill_lines.read_by_bill_id', [55], {}],
                                    ['project.read_by_id', [10], {}],
                                    ['user.read_by_id', [27], {}],
                                    ['bla.read_by_bill_line_item_id', ['li-pub-1'], {}],
                                    ['scc.read_by_id', [100], {}],
                                    ['outbox.enqueue_send_mail',
                                     [],
                                     {'attachment': None,
                                      'bcc_addresses': [],
                                      'bill_id': 55,
                                      'body': '<p>A new bill has been submitted for '
                                              'review:</p><p>Project: HE12<br/>Vendor: Acme '
                                              '&lt;Supply&gt; &amp; Co<br/>Number: '
                                              'INV-1001<br/>Amount: $1,234.50</p><p>Submitted By: '
                                              'Sam Sub<br/>Submitted Date: 10/01/2026</p><table '
                                              "cellpadding='4' cellspacing='0' border='1' "
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
                                      'entity_public_id': 'bill-pub-55',
                                      'entity_type': 'Bill',
                                      'mode': 'draft',
                                      'review_id': 9,
                                      'subject': '[Review] Acme <Supply> & Co — Bill #INV-1001 — '
                                                 'HE12 — $1,234.50',
                                      'to_addresses': []}],
                                    ['status.read_all', [], {}],
                                    ['status.get_next_intermediate_status',
                                     [10],
                                     {'statuses': [{'ns': {'id': 1,
                                                           'name': 'Submitted',
                                                           'sort_order': 10}},
                                                   {'ns': {'id': 2,
                                                           'name': 'In Review',
                                                           'sort_order': 20}}]}],
                                    ['review.create',
                                     [],
                                     {'bill_id': 55,
                                      'comments': None,
                                      'created_by_user_id': 9001,
                                      'email_message_id': None,
                                      'review_status_id': 2,
                                      'user_id': 27}]],
                          'logs': [['WARNING',
                                    'review_notification.unreachable_recipients '
                                    'bill_public_id=bill-pub-55 user_ids=[6] '
                                    'reason=no_contact_email',
                                    False],
                                   ['WARNING',
                                    'review_notification.no_to_recipient '
                                    'bill_public_id=bill-pub-55 '
                                    'reason=no_project_manager_with_email — sending anyway (BCC '
                                    'archive still active).',
                                    False],
                                   ['WARNING',
                                    'review_notification.bcc_archive_skipped '
                                    'bill_public_id=bill-pub-55 '
                                    'reason=invoice_inbox_email_not_configured',
                                    False],
                                   ['INFO',
                                    'review_notification.enqueued bill_public_id=bill-pub-55 '
                                    'outbox_public_id=ob-1 mode=draft to=0 cc=2 bcc=0 '
                                    'attachment=(none)',
                                    False],
                                   ['INFO',
                                    'review_notification.in_review_advanced '
                                    'bill_public_id=bill-pub-55',
                                    False]]},
          'sparse_entity': {'result': None,
                            'calls': [['recipients.bill',
                                       [],
                                       {'bill_id': 55, 'exclude_user_id': 27}],
                                      ['bill_lines.read_by_bill_id', [55], {}],
                                      ['user.read_by_id', [27], {}],
                                      ['outbox.enqueue_send_mail',
                                       [],
                                       {'attachment': None,
                                        'bcc_addresses': [{'email': 'invoice@example.com',
                                                           'name': 'Invoice Inbox (archive)'}],
                                        'bill_id': 55,
                                        'body': '<p>Pat/Zoë,</p><p>A new bill has been submitted '
                                                'for review:</p><p>Project: (no '
                                                'project)<br/>Vendor: (unknown vendor)<br/>Number: '
                                                '(no number)<br/>Amount: $0.00</p><p>Submitted By: '
                                                'User 27<br/>Submitted Date: </p><p>When you have '
                                                'a moment, will you please reply for approval with '
                                                'Sub Cost Code and Description, or '
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
                                        'subject': '[Review] (unknown vendor) — Bill #(no number) '
                                                   '— (no project) — $0.00',
                                        'to_addresses': [{'email': 'pat@example.com',
                                                          'name': 'Pat Lee'},
                                                         {'email': 'blank@example.com',
                                                          'name': '   Blank'},
                                                         {'email': 'zoe@example.com',
                                                          'name': 'Zoë'}]}],
                                      ['status.read_all', [], {}],
                                      ['status.get_next_intermediate_status',
                                       [10],
                                       {'statuses': [{'ns': {'id': 1,
                                                             'name': 'Submitted',
                                                             'sort_order': 10}},
                                                     {'ns': {'id': 2,
                                                             'name': 'In Review',
                                                             'sort_order': 20}}]}],
                                      ['review.create',
                                       [],
                                       {'bill_id': 55,
                                        'comments': None,
                                        'created_by_user_id': 9001,
                                        'email_message_id': None,
                                        'review_status_id': 2,
                                        'user_id': 27}]],
                            'logs': [['WARNING',
                                      'review_notification.unreachable_recipients '
                                      'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                      'reason=no_contact_email',
                                      False],
                                     ['INFO',
                                      'review_notification.enqueued bill_public_id=bill-pub-55 '
                                      'outbox_public_id=ob-1 mode=draft to=3 cc=2 bcc=1 '
                                      'attachment=(none)',
                                      False],
                                     ['INFO',
                                      'review_notification.in_review_advanced '
                                      'bill_public_id=bill-pub-55',
                                      False]]},
          'status_unresolved': {'result': None,
                                'calls': [['recipients.bill',
                                           [],
                                           {'bill_id': 55, 'exclude_user_id': 27}],
                                          ['vendor.read_by_id', [7], {}],
                                          ['bill_lines.read_by_bill_id', [55], {}],
                                          ['user.read_by_id', [27], {}],
                                          ['outbox.enqueue_send_mail',
                                           [],
                                           {'attachment': None,
                                            'bcc_addresses': [{'email': 'invoice@example.com',
                                                               'name': 'Invoice Inbox (archive)'}],
                                            'bill_id': 55,
                                            'body': '<p>Pat/Zoë,</p><p>A new bill has been '
                                                    'submitted for review:</p><p>Project: (no '
                                                    'project)<br/>Vendor: Acme &lt;Supply&gt; '
                                                    '&amp; Co<br/>Number: INV-1001<br/>Amount: '
                                                    '$1,234.50</p><p>Submitted By: Sam '
                                                    'Sub<br/>Submitted Date: 10/01/2026</p><p>When '
                                                    'you have a moment, will you please reply for '
                                                    'approval with Sub Cost Code and Description, '
                                                    'or non-approval?</p>',
                                            'body_type': 'HTML',
                                            'cc_addresses': [{'email': 'olive@example.com',
                                                              'name': 'Olive Own'},
                                                             {'email': 'noname@example.com',
                                                              'name': 'noname@example.com'}],
                                            'entity_public_id': 'bill-pub-55',
                                            'entity_type': 'Bill',
                                            'mode': 'draft',
                                            'review_id': 9,
                                            'subject': '[Review] Acme <Supply> & Co — Bill '
                                                       '#INV-1001 — (no project) — $1,234.50',
                                            'to_addresses': [{'email': 'pat@example.com',
                                                              'name': 'Pat Lee'},
                                                             {'email': 'blank@example.com',
                                                              'name': '   Blank'},
                                                             {'email': 'zoe@example.com',
                                                              'name': 'Zoë'}]}],
                                          ['status.read_all', [], {}]],
                                'logs': [['WARNING',
                                          'review_notification.unreachable_recipients '
                                          'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                          'reason=no_contact_email',
                                          False],
                                         ['INFO',
                                          'review_notification.enqueued bill_public_id=bill-pub-55 '
                                          'outbox_public_id=ob-1 mode=draft to=3 cc=2 bcc=1 '
                                          'attachment=(none)',
                                          False],
                                         ['INFO',
                                          'review_notification.current_status_unresolved '
                                          'bill_public_id=bill-pub-55 review_status_id=404',
                                          False]]},
          'submitter_missing': {'result': None,
                                'calls': [['recipients.bill',
                                           [],
                                           {'bill_id': 55, 'exclude_user_id': 27}],
                                          ['vendor.read_by_id', [7], {}],
                                          ['bill_lines.read_by_bill_id', [55], {}],
                                          ['project.read_by_id', [10], {}],
                                          ['bla.read_by_bill_line_item_id', ['li-pub-1'], {}],
                                          ['scc.read_by_id', [100], {}],
                                          ['outbox.enqueue_send_mail',
                                           [],
                                           {'attachment': None,
                                            'bcc_addresses': [{'email': 'invoice@example.com',
                                                               'name': 'Invoice Inbox (archive)'}],
                                            'bill_id': 55,
                                            'body': '<p>Pat/Zoë,</p><p>A new bill has been '
                                                    'submitted for review:</p><p>Project: '
                                                    'HE12<br/>Vendor: (unknown vendor)<br/>Number: '
                                                    'INV-1001<br/>Amount: '
                                                    '$1,234.50</p><p>Submitted By: User '
                                                    'None<br/>Submitted Date: 10/01/2026</p><table '
                                                    "cellpadding='4' cellspacing='0' border='1' "
                                                    "style='border-collapse:collapse; "
                                                    "margin-top:6px;'><tr><th "
                                                    "align='left'>Description</th><th "
                                                    "align='left'>Sub Cost Code</th><th "
                                                    "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                    'Framing</td><td '
                                                    "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                    'you have a moment, will you please reply for '
                                                    'approval with Sub Cost Code and Description, '
                                                    'or non-approval?</p>',
                                            'body_type': 'HTML',
                                            'cc_addresses': [{'email': 'olive@example.com',
                                                              'name': 'Olive Own'},
                                                             {'email': 'noname@example.com',
                                                              'name': 'noname@example.com'}],
                                            'entity_public_id': 'bill-pub-55',
                                            'entity_type': 'Bill',
                                            'mode': 'draft',
                                            'review_id': 9,
                                            'subject': '[Review] (unknown vendor) — Bill #INV-1001 '
                                                       '— HE12 — $1,234.50',
                                            'to_addresses': [{'email': 'pat@example.com',
                                                              'name': 'Pat Lee'},
                                                             {'email': 'blank@example.com',
                                                              'name': '   Blank'},
                                                             {'email': 'zoe@example.com',
                                                              'name': 'Zoë'}]}],
                                          ['status.read_all', [], {}],
                                          ['status.get_next_intermediate_status',
                                           [10],
                                           {'statuses': [{'ns': {'id': 1,
                                                                 'name': 'Submitted',
                                                                 'sort_order': 10}},
                                                         {'ns': {'id': 2,
                                                                 'name': 'In Review',
                                                                 'sort_order': 20}}]}],
                                          ['review.create',
                                           [],
                                           {'bill_id': 55,
                                            'comments': None,
                                            'created_by_user_id': 9001,
                                            'email_message_id': None,
                                            'review_status_id': 2,
                                            'user_id': None}]],
                                'logs': [['WARNING',
                                          'review_notification.unreachable_recipients '
                                          'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                          'reason=no_contact_email',
                                          False],
                                         ['INFO',
                                          'review_notification.enqueued bill_public_id=bill-pub-55 '
                                          'outbox_public_id=ob-1 mode=draft to=3 cc=2 bcc=1 '
                                          'attachment=(none)',
                                          False],
                                         ['INFO',
                                          'review_notification.in_review_advanced '
                                          'bill_public_id=bill-pub-55',
                                          False]]},
          'unreachable_only_no_bcc': {'result': None,
                                      'calls': [['recipients.bill',
                                                 [],
                                                 {'bill_id': 55, 'exclude_user_id': 27}]],
                                      'logs': [['WARNING',
                                                'review_notification.unreachable_recipients '
                                                'bill_public_id=bill-pub-55 user_ids=[2, 6] '
                                                'reason=no_contact_email',
                                                False],
                                               ['WARNING',
                                                'review_notification.no_to_recipient '
                                                'bill_public_id=bill-pub-55 '
                                                'reason=no_project_manager_with_email — sending '
                                                'anyway (BCC archive still active).',
                                                False],
                                               ['INFO',
                                                'review_notification.no_cc_recipient '
                                                'bill_public_id=bill-pub-55 '
                                                'reason=no_owner_with_email',
                                                False],
                                               ['WARNING',
                                                'review_notification.bcc_archive_skipped '
                                                'bill_public_id=bill-pub-55 '
                                                'reason=invoice_inbox_email_not_configured',
                                                False],
                                               ['ERROR',
                                                'review_notification.skipped '
                                                'bill_public_id=bill-pub-55 '
                                                'reason=no_recipients_on_any_line',
                                                False]]},
          'zero_recipients_no_bcc': {'result': None,
                                     'calls': [['recipients.bill',
                                                [],
                                                {'bill_id': 55, 'exclude_user_id': 27}]],
                                     'logs': [['WARNING',
                                               'review_notification.no_to_recipient '
                                               'bill_public_id=bill-pub-55 '
                                               'reason=no_project_manager_with_email — sending '
                                               'anyway (BCC archive still active).',
                                               False],
                                              ['INFO',
                                               'review_notification.no_cc_recipient '
                                               'bill_public_id=bill-pub-55 '
                                               'reason=no_owner_with_email',
                                               False],
                                              ['WARNING',
                                               'review_notification.bcc_archive_skipped '
                                               'bill_public_id=bill-pub-55 '
                                               'reason=invoice_inbox_email_not_configured',
                                               False],
                                              ['ERROR',
                                               'review_notification.skipped '
                                               'bill_public_id=bill-pub-55 '
                                               'reason=no_recipients_on_any_line',
                                               False]]}},
 'expense': {'advance_raises': {'result': None,
                                'calls': [['recipients.expense',
                                           [],
                                           {'exclude_user_id': 27, 'expense_id': 66}],
                                          ['vendor.read_by_id', [8], {}],
                                          ['expense_lines.read_by_expense_id', [66], {}],
                                          ['user.read_by_id', [27], {}],
                                          ['outbox.enqueue_send_mail',
                                           [],
                                           {'attachment': None,
                                            'bcc_addresses': [{'email': 'invoice@example.com',
                                                               'name': 'Invoice Inbox (archive)'}],
                                            'body': '<p>Pat/Zoë,</p><p>A new expense has been '
                                                    'submitted for review:</p><p>Project: (no '
                                                    'project)<br/>Vendor: Acme &lt;Supply&gt; '
                                                    '&amp; Co<br/>Reference: R-77<br/>Amount: '
                                                    '$42.00<br/>Memo: Lunch &amp; '
                                                    '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                                    'By: Sam Sub<br/>Submitted Date: '
                                                    '10/02/2026</p><p>When you have a moment, will '
                                                    'you please reply for approval with Sub Cost '
                                                    'Code and Description, or non-approval?</p>',
                                            'body_type': 'HTML',
                                            'cc_addresses': [{'email': 'olive@example.com',
                                                              'name': 'Olive Own'},
                                                             {'email': 'noname@example.com',
                                                              'name': 'noname@example.com'}],
                                            'entity_public_id': 'exp-pub-66',
                                            'entity_type': 'Expense',
                                            'mode': 'draft',
                                            'review_id': 9,
                                            'subject': '[Review] Acme <Supply> & Co — Expense R-77 '
                                                       '— (no project) — $42.00',
                                            'to_addresses': [{'email': 'pat@example.com',
                                                              'name': 'Pat Lee'},
                                                             {'email': 'blank@example.com',
                                                              'name': '   Blank'},
                                                             {'email': 'zoe@example.com',
                                                              'name': 'Zoë'}]}],
                                          ['status.read_all', [], {}]],
                                'logs': [['WARNING',
                                          'review_notification.unreachable_recipients '
                                          'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                          'reason=no_contact_email',
                                          False],
                                         ['INFO',
                                          'review_notification.enqueued '
                                          'expense_public_id=exp-pub-66 outbox_public_id=ob-1 '
                                          'mode=draft to=3 cc=2 bcc=1 attachment=(none)',
                                          False],
                                         ['ERROR',
                                          'review_notification.in_review_advance_failed '
                                          'expense_public_id=exp-pub-66: db down',
                                          True]]},
             'bcc_only': {'result': None,
                          'calls': [['recipients.expense',
                                     [],
                                     {'exclude_user_id': 27, 'expense_id': 66}],
                                    ['vendor.read_by_id', [8], {}],
                                    ['expense_lines.read_by_expense_id', [66], {}],
                                    ['project.read_by_id', [10], {}],
                                    ['user.read_by_id', [27], {}],
                                    ['elia.read_by_expense_line_item_id',
                                     [],
                                     {'expense_line_item_public_id': 'li-pub-1'}],
                                    ['scc.read_by_id', [100], {}],
                                    ['outbox.enqueue_send_mail',
                                     [],
                                     {'attachment': None,
                                      'bcc_addresses': [{'email': 'invoice@example.com',
                                                         'name': 'Invoice Inbox (archive)'}],
                                      'body': '<p>A new expense has been submitted for '
                                              'review:</p><p>Project: HE12<br/>Vendor: Acme '
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
                                      'cc_addresses': [],
                                      'entity_public_id': 'exp-pub-66',
                                      'entity_type': 'Expense',
                                      'mode': 'draft',
                                      'review_id': 9,
                                      'subject': '[Review] Acme <Supply> & Co — Expense R-77 — '
                                                 'HE12 — $42.00',
                                      'to_addresses': []}]],
                          'logs': [['WARNING',
                                    'review_notification.no_to_recipient '
                                    'expense_public_id=exp-pub-66 '
                                    'reason=no_project_manager_with_email — sending anyway (BCC '
                                    'archive still active).',
                                    False],
                                   ['INFO',
                                    'review_notification.no_cc_recipient '
                                    'expense_public_id=exp-pub-66 reason=no_owner_with_email',
                                    False],
                                   ['INFO',
                                    'review_notification.enqueued expense_public_id=exp-pub-66 '
                                    'outbox_public_id=ob-1 mode=draft to=0 cc=0 bcc=1 '
                                    'attachment=(none)',
                                    False]]},
             'enqueue_raises': {'result': None,
                                'calls': [['recipients.expense',
                                           [],
                                           {'exclude_user_id': 27, 'expense_id': 66}],
                                          ['vendor.read_by_id', [8], {}]],
                                'logs': [['WARNING',
                                          'review_notification.unreachable_recipients '
                                          'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                          'reason=no_contact_email',
                                          False],
                                         ['ERROR',
                                          'review_notification.enqueue_failed '
                                          'expense_public_id=exp-pub-66 review_id=9: vendor read '
                                          'boom',
                                          True]]},
             'full': {'result': None,
                      'calls': [['recipients.expense',
                                 [],
                                 {'exclude_user_id': 27, 'expense_id': 66}],
                                ['vendor.read_by_id', [8], {}],
                                ['expense_lines.read_by_expense_id', [66], {}],
                                ['project.read_by_id', [10], {}],
                                ['project.read_by_id', [11], {}],
                                ['project.read_by_id', [12], {}],
                                ['project.read_by_id', [99], {}],
                                ['user.read_by_id', [27], {}],
                                ['elia.read_by_expense_line_item_id',
                                 [],
                                 {'expense_line_item_public_id': 'li-pub-1'}],
                                ['attachment.read_by_id', [901], {}],
                                ['elia.read_by_expense_line_item_id',
                                 [],
                                 {'expense_line_item_public_id': 'li-pub-2'}],
                                ['elia.read_by_expense_line_item_id',
                                 [],
                                 {'expense_line_item_public_id': 'li-pub-3'}],
                                ['attachment.read_by_id', [903], {}],
                                ['elia.read_by_expense_line_item_id',
                                 [],
                                 {'expense_line_item_public_id': 'li-pub-4'}],
                                ['attachment.read_by_id', [904], {}],
                                ['scc.read_by_id', [100], {}],
                                ['scc.read_by_id', [101], {}],
                                ['scc.read_by_id', [102], {}],
                                ['scc.read_by_id', [999], {}],
                                ['project.read_by_id', [10], {}],
                                ['project.read_by_id', [11], {}],
                                ['project.read_by_id', [12], {}],
                                ['project.read_by_id', [99], {}],
                                ['outbox.enqueue_send_mail',
                                 [],
                                 {'attachment': {'name': 'expense.pdf',
                                                 'content_type': 'Application/PDF',
                                                 'blob_url': 'b/904.pdf'},
                                  'bcc_addresses': [{'email': 'invoice@example.com',
                                                     'name': 'Invoice Inbox (archive)'}],
                                  'body': '<p>Pat/Zoë,</p><p>A new expense has been submitted for '
                                          'review:</p><p>Project: HE12, Oak &amp; Pine<br/>Vendor: '
                                          'Acme &lt;Supply&gt; &amp; Co<br/>Reference: '
                                          'R-77<br/>Amount: $42.00<br/>Memo: Lunch &amp; '
                                          '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                          'By: Sam Sub<br/>Submitted Date: 10/02/2026</p><table '
                                          "cellpadding='4' cellspacing='0' border='1' "
                                          "style='border-collapse:collapse; "
                                          "margin-top:6px;'><tr><th "
                                          "align='left'>Description</th><th "
                                          "align='left'>Project</th><th align='left'>Sub Cost "
                                          'Code</th><th '
                                          "align='right'>Amount</th></tr><tr><td>Studs &amp; "
                                          'plates</td><td>HE12</td><td>01-100 Framing</td><td '
                                          "style='text-align:right;'>$1,000.50</td></tr><tr><td></td><td>Oak "
                                          '&amp; Pine</td><td>None Drywall &lt;L5&gt;</td><td '
                                          "style='text-align:right;'>$200.00</td></tr><tr><td>&lt;b&gt;freight&lt;/b&gt;</td><td></td><td></td><td "
                                          "style='text-align:right;'>$0.00</td></tr><tr><td>misc</td><td></td><td></td><td "
                                          "style='text-align:right;'>$0.00</td></tr><tr><td>unknown "
                                          'project</td><td></td><td></td><td '
                                          "style='text-align:right;'>$34.00</td></tr></table><p>Attached: "
                                          '<strong>expense.pdf</strong></p><p>When you have a '
                                          'moment, will you please reply for approval with Sub '
                                          'Cost Code and Description, or non-approval?</p>',
                                  'body_type': 'HTML',
                                  'cc_addresses': [{'email': 'olive@example.com',
                                                    'name': 'Olive Own'},
                                                   {'email': 'noname@example.com',
                                                    'name': 'noname@example.com'}],
                                  'entity_public_id': 'exp-pub-66',
                                  'entity_type': 'Expense',
                                  'mode': 'draft',
                                  'review_id': 9,
                                  'subject': '[Review] Acme <Supply> & Co — Expense R-77 — HE12, '
                                             'Oak & Pine — $42.00',
                                  'to_addresses': [{'email': 'pat@example.com', 'name': 'Pat Lee'},
                                                   {'email': 'blank@example.com',
                                                    'name': '   Blank'},
                                                   {'email': 'zoe@example.com', 'name': 'Zoë'}]}],
                                ['status.read_all', [], {}],
                                ['status.get_next_intermediate_status',
                                 [10],
                                 {'statuses': [{'ns': {'id': 1,
                                                       'name': 'Submitted',
                                                       'sort_order': 10}},
                                               {'ns': {'id': 2,
                                                       'name': 'In Review',
                                                       'sort_order': 20}}]}],
                                ['review.create',
                                 [],
                                 {'comments': None,
                                  'created_by_user_id': 9001,
                                  'email_message_id': None,
                                  'expense_id': 66,
                                  'review_status_id': 2,
                                  'user_id': 27}]],
                      'logs': [['WARNING',
                                'review_notification.unreachable_recipients '
                                'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                'reason=no_contact_email',
                                False],
                               ['INFO',
                                'review_notification.attachment_skipped_non_pdf '
                                'expense_public_id=exp-pub-66 attachment_public_id=att-901 '
                                'content_type=image/JPEG',
                                False],
                               ['INFO',
                                'review_notification.enqueued expense_public_id=exp-pub-66 '
                                'outbox_public_id=ob-1 mode=draft to=3 cc=2 bcc=1 '
                                'attachment=expense.pdf',
                                False],
                               ['INFO',
                                'review_notification.in_review_advanced '
                                'expense_public_id=exp-pub-66',
                                False]]},
             'in_review_missing': {'result': None,
                                   'calls': [['recipients.expense',
                                              [],
                                              {'exclude_user_id': 27, 'expense_id': 66}],
                                             ['vendor.read_by_id', [8], {}],
                                             ['expense_lines.read_by_expense_id', [66], {}],
                                             ['user.read_by_id', [27], {}],
                                             ['outbox.enqueue_send_mail',
                                              [],
                                              {'attachment': None,
                                               'bcc_addresses': [{'email': 'invoice@example.com',
                                                                  'name': 'Invoice Inbox '
                                                                          '(archive)'}],
                                               'body': '<p>Pat/Zoë,</p><p>A new expense has been '
                                                       'submitted for review:</p><p>Project: (no '
                                                       'project)<br/>Vendor: Acme &lt;Supply&gt; '
                                                       '&amp; Co<br/>Reference: R-77<br/>Amount: '
                                                       '$42.00<br/>Memo: Lunch &amp; '
                                                       '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                                       'By: Sam Sub<br/>Submitted Date: '
                                                       '10/02/2026</p><p>When you have a moment, '
                                                       'will you please reply for approval with '
                                                       'Sub Cost Code and Description, or '
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
                                               'subject': '[Review] Acme <Supply> & Co — Expense '
                                                          'R-77 — (no project) — $42.00',
                                               'to_addresses': [{'email': 'pat@example.com',
                                                                 'name': 'Pat Lee'},
                                                                {'email': 'blank@example.com',
                                                                 'name': '   Blank'},
                                                                {'email': 'zoe@example.com',
                                                                 'name': 'Zoë'}]}],
                                             ['status.read_all', [], {}],
                                             ['status.get_next_intermediate_status',
                                              [10],
                                              {'statuses': [{'ns': {'id': 1,
                                                                    'name': 'Submitted',
                                                                    'sort_order': 10}},
                                                            {'ns': {'id': 2,
                                                                    'name': 'In Review',
                                                                    'sort_order': 20}}]}]],
                                   'logs': [['WARNING',
                                             'review_notification.unreachable_recipients '
                                             'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                             'reason=no_contact_email',
                                             False],
                                            ['INFO',
                                             'review_notification.enqueued '
                                             'expense_public_id=exp-pub-66 outbox_public_id=ob-1 '
                                             'mode=draft to=3 cc=2 bcc=1 attachment=(none)',
                                             False],
                                            ['INFO',
                                             'review_notification.in_review_status_missing '
                                             'expense_public_id=exp-pub-66 no active non-declined '
                                             'non-final ReviewStatus after sort_order=10',
                                             False]]},
             'mode_setting_draft': {'result': None,
                                    'calls': [['recipients.expense',
                                               [],
                                               {'exclude_user_id': 27, 'expense_id': 66}],
                                              ['vendor.read_by_id', [8], {}],
                                              ['expense_lines.read_by_expense_id', [66], {}],
                                              ['project.read_by_id', [10], {}],
                                              ['user.read_by_id', [27], {}],
                                              ['elia.read_by_expense_line_item_id',
                                               [],
                                               {'expense_line_item_public_id': 'li-pub-1'}],
                                              ['scc.read_by_id', [100], {}],
                                              ['outbox.enqueue_send_mail',
                                               [],
                                               {'attachment': None,
                                                'bcc_addresses': [{'email': 'invoice@example.com',
                                                                   'name': 'Invoice Inbox '
                                                                           '(archive)'}],
                                                'body': '<p>Pat/Zoë,</p><p>A new expense has been '
                                                        'submitted for review:</p><p>Project: '
                                                        'HE12<br/>Vendor: Acme &lt;Supply&gt; '
                                                        '&amp; Co<br/>Reference: R-77<br/>Amount: '
                                                        '$42.00<br/>Memo: Lunch &amp; '
                                                        '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                                        'By: Sam Sub<br/>Submitted Date: '
                                                        "10/02/2026</p><table cellpadding='4' "
                                                        "cellspacing='0' border='1' "
                                                        "style='border-collapse:collapse; "
                                                        "margin-top:6px;'><tr><th "
                                                        "align='left'>Description</th><th "
                                                        "align='left'>Sub Cost Code</th><th "
                                                        "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                        'Framing</td><td '
                                                        "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                        'you have a moment, will you please reply '
                                                        'for approval with Sub Cost Code and '
                                                        'Description, or non-approval?</p>',
                                                'body_type': 'HTML',
                                                'cc_addresses': [{'email': 'olive@example.com',
                                                                  'name': 'Olive Own'},
                                                                 {'email': 'noname@example.com',
                                                                  'name': 'noname@example.com'}],
                                                'entity_public_id': 'exp-pub-66',
                                                'entity_type': 'Expense',
                                                'mode': 'draft',
                                                'review_id': 9,
                                                'subject': '[Review] Acme <Supply> & Co — Expense '
                                                           'R-77 — HE12 — $42.00',
                                                'to_addresses': [{'email': 'pat@example.com',
                                                                  'name': 'Pat Lee'},
                                                                 {'email': 'blank@example.com',
                                                                  'name': '   Blank'},
                                                                 {'email': 'zoe@example.com',
                                                                  'name': 'Zoë'}]}],
                                              ['status.read_all', [], {}],
                                              ['status.get_next_intermediate_status',
                                               [10],
                                               {'statuses': [{'ns': {'id': 1,
                                                                     'name': 'Submitted',
                                                                     'sort_order': 10}},
                                                             {'ns': {'id': 2,
                                                                     'name': 'In Review',
                                                                     'sort_order': 20}}]}],
                                              ['review.create',
                                               [],
                                               {'comments': None,
                                                'created_by_user_id': 9001,
                                                'email_message_id': None,
                                                'expense_id': 66,
                                                'review_status_id': 2,
                                                'user_id': 27}]],
                                    'logs': [['WARNING',
                                              'review_notification.unreachable_recipients '
                                              'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                              'reason=no_contact_email',
                                              False],
                                             ['INFO',
                                              'review_notification.enqueued '
                                              'expense_public_id=exp-pub-66 outbox_public_id=ob-1 '
                                              'mode=draft to=3 cc=2 bcc=1 attachment=(none)',
                                              False],
                                             ['INFO',
                                              'review_notification.in_review_advanced '
                                              'expense_public_id=exp-pub-66',
                                              False]]},
             'no_attachment_single_project': {'result': None,
                                              'calls': [['recipients.expense',
                                                         [],
                                                         {'exclude_user_id': 27, 'expense_id': 66}],
                                                        ['vendor.read_by_id', [8], {}],
                                                        ['expense_lines.read_by_expense_id',
                                                         [66],
                                                         {}],
                                                        ['project.read_by_id', [10], {}],
                                                        ['user.read_by_id', [27], {}],
                                                        ['elia.read_by_expense_line_item_id',
                                                         [],
                                                         {'expense_line_item_public_id': 'li-pub-1'}],
                                                        ['scc.read_by_id', [100], {}],
                                                        ['outbox.enqueue_send_mail',
                                                         [],
                                                         {'attachment': None,
                                                          'bcc_addresses': [{'email': 'invoice@example.com',
                                                                             'name': 'Invoice '
                                                                                     'Inbox '
                                                                                     '(archive)'}],
                                                          'body': '<p>Pat/Zoë,</p><p>A new expense '
                                                                  'has been submitted for '
                                                                  'review:</p><p>Project: '
                                                                  'HE12<br/>Vendor: Acme '
                                                                  '&lt;Supply&gt; &amp; '
                                                                  'Co<br/>Reference: '
                                                                  'R-77<br/>Amount: '
                                                                  '$42.00<br/>Memo: Lunch &amp; '
                                                                  '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                                                  'By: Sam Sub<br/>Submitted Date: '
                                                                  '10/02/2026</p><table '
                                                                  "cellpadding='4' cellspacing='0' "
                                                                  "border='1' "
                                                                  "style='border-collapse:collapse; "
                                                                  "margin-top:6px;'><tr><th "
                                                                  "align='left'>Description</th><th "
                                                                  "align='left'>Sub Cost "
                                                                  'Code</th><th '
                                                                  "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                                  'Framing</td><td '
                                                                  "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                                  'you have a moment, will you '
                                                                  'please reply for approval with '
                                                                  'Sub Cost Code and Description, '
                                                                  'or non-approval?</p>',
                                                          'body_type': 'HTML',
                                                          'cc_addresses': [{'email': 'olive@example.com',
                                                                            'name': 'Olive Own'},
                                                                           {'email': 'noname@example.com',
                                                                            'name': 'noname@example.com'}],
                                                          'entity_public_id': 'exp-pub-66',
                                                          'entity_type': 'Expense',
                                                          'mode': 'draft',
                                                          'review_id': 9,
                                                          'subject': '[Review] Acme <Supply> & Co '
                                                                     '— Expense R-77 — HE12 — '
                                                                     '$42.00',
                                                          'to_addresses': [{'email': 'pat@example.com',
                                                                            'name': 'Pat Lee'},
                                                                           {'email': 'blank@example.com',
                                                                            'name': '   Blank'},
                                                                           {'email': 'zoe@example.com',
                                                                            'name': 'Zoë'}]}],
                                                        ['status.read_all', [], {}],
                                                        ['status.get_next_intermediate_status',
                                                         [10],
                                                         {'statuses': [{'ns': {'id': 1,
                                                                               'name': 'Submitted',
                                                                               'sort_order': 10}},
                                                                       {'ns': {'id': 2,
                                                                               'name': 'In Review',
                                                                               'sort_order': 20}}]}],
                                                        ['review.create',
                                                         [],
                                                         {'comments': None,
                                                          'created_by_user_id': 9001,
                                                          'email_message_id': None,
                                                          'expense_id': 66,
                                                          'review_status_id': 2,
                                                          'user_id': 27}]],
                                              'logs': [['WARNING',
                                                        'review_notification.unreachable_recipients '
                                                        'expense_public_id=exp-pub-66 user_ids=[2, '
                                                        '6] reason=no_contact_email',
                                                        False],
                                                       ['INFO',
                                                        'review_notification.enqueued '
                                                        'expense_public_id=exp-pub-66 '
                                                        'outbox_public_id=ob-1 mode=draft to=3 '
                                                        'cc=2 bcc=1 attachment=(none)',
                                                        False],
                                                       ['INFO',
                                                        'review_notification.in_review_advanced '
                                                        'expense_public_id=exp-pub-66',
                                                        False]]},
             'outbox_refused': {'result': None,
                                'calls': [['recipients.expense',
                                           [],
                                           {'exclude_user_id': 27, 'expense_id': 66}],
                                          ['vendor.read_by_id', [8], {}],
                                          ['expense_lines.read_by_expense_id', [66], {}],
                                          ['project.read_by_id', [10], {}],
                                          ['user.read_by_id', [27], {}],
                                          ['elia.read_by_expense_line_item_id',
                                           [],
                                           {'expense_line_item_public_id': 'li-pub-1'}],
                                          ['scc.read_by_id', [100], {}],
                                          ['outbox.enqueue_send_mail',
                                           [],
                                           {'attachment': None,
                                            'bcc_addresses': [{'email': 'invoice@example.com',
                                                               'name': 'Invoice Inbox (archive)'}],
                                            'body': '<p>Pat/Zoë,</p><p>A new expense has been '
                                                    'submitted for review:</p><p>Project: '
                                                    'HE12<br/>Vendor: Acme &lt;Supply&gt; &amp; '
                                                    'Co<br/>Reference: R-77<br/>Amount: '
                                                    '$42.00<br/>Memo: Lunch &amp; '
                                                    '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                                    'By: Sam Sub<br/>Submitted Date: '
                                                    "10/02/2026</p><table cellpadding='4' "
                                                    "cellspacing='0' border='1' "
                                                    "style='border-collapse:collapse; "
                                                    "margin-top:6px;'><tr><th "
                                                    "align='left'>Description</th><th "
                                                    "align='left'>Sub Cost Code</th><th "
                                                    "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                    'Framing</td><td '
                                                    "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                    'you have a moment, will you please reply for '
                                                    'approval with Sub Cost Code and Description, '
                                                    'or non-approval?</p>',
                                            'body_type': 'HTML',
                                            'cc_addresses': [{'email': 'olive@example.com',
                                                              'name': 'Olive Own'},
                                                             {'email': 'noname@example.com',
                                                              'name': 'noname@example.com'}],
                                            'entity_public_id': 'exp-pub-66',
                                            'entity_type': 'Expense',
                                            'mode': 'draft',
                                            'review_id': 9,
                                            'subject': '[Review] Acme <Supply> & Co — Expense R-77 '
                                                       '— HE12 — $42.00',
                                            'to_addresses': [{'email': 'pat@example.com',
                                                              'name': 'Pat Lee'},
                                                             {'email': 'blank@example.com',
                                                              'name': '   Blank'},
                                                             {'email': 'zoe@example.com',
                                                              'name': 'Zoë'}]}]],
                                'logs': [['WARNING',
                                          'review_notification.unreachable_recipients '
                                          'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                          'reason=no_contact_email',
                                          False],
                                         ['INFO',
                                          'review_notification.enqueue_refused '
                                          'expense_public_id=exp-pub-66 reason=ms_writes_gate',
                                          False]]},
             'owners_only': {'result': None,
                             'calls': [['recipients.expense',
                                        [],
                                        {'exclude_user_id': 27, 'expense_id': 66}],
                                       ['vendor.read_by_id', [8], {}],
                                       ['expense_lines.read_by_expense_id', [66], {}],
                                       ['project.read_by_id', [10], {}],
                                       ['user.read_by_id', [27], {}],
                                       ['elia.read_by_expense_line_item_id',
                                        [],
                                        {'expense_line_item_public_id': 'li-pub-1'}],
                                       ['scc.read_by_id', [100], {}],
                                       ['outbox.enqueue_send_mail',
                                        [],
                                        {'attachment': None,
                                         'bcc_addresses': [],
                                         'body': '<p>A new expense has been submitted for '
                                                 'review:</p><p>Project: HE12<br/>Vendor: Acme '
                                                 '&lt;Supply&gt; &amp; Co<br/>Reference: '
                                                 'R-77<br/>Amount: $42.00<br/>Memo: Lunch &amp; '
                                                 '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                                 'By: Sam Sub<br/>Submitted Date: '
                                                 "10/02/2026</p><table cellpadding='4' "
                                                 "cellspacing='0' border='1' "
                                                 "style='border-collapse:collapse; "
                                                 "margin-top:6px;'><tr><th "
                                                 "align='left'>Description</th><th "
                                                 "align='left'>Sub Cost Code</th><th "
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
                                         'to_addresses': []}],
                                       ['status.read_all', [], {}],
                                       ['status.get_next_intermediate_status',
                                        [10],
                                        {'statuses': [{'ns': {'id': 1,
                                                              'name': 'Submitted',
                                                              'sort_order': 10}},
                                                      {'ns': {'id': 2,
                                                              'name': 'In Review',
                                                              'sort_order': 20}}]}],
                                       ['review.create',
                                        [],
                                        {'comments': None,
                                         'created_by_user_id': 9001,
                                         'email_message_id': None,
                                         'expense_id': 66,
                                         'review_status_id': 2,
                                         'user_id': 27}]],
                             'logs': [['WARNING',
                                       'review_notification.unreachable_recipients '
                                       'expense_public_id=exp-pub-66 user_ids=[6] '
                                       'reason=no_contact_email',
                                       False],
                                      ['WARNING',
                                       'review_notification.no_to_recipient '
                                       'expense_public_id=exp-pub-66 '
                                       'reason=no_project_manager_with_email — sending anyway (BCC '
                                       'archive still active).',
                                       False],
                                      ['WARNING',
                                       'review_notification.bcc_archive_skipped '
                                       'expense_public_id=exp-pub-66 '
                                       'reason=invoice_inbox_email_not_configured',
                                       False],
                                      ['INFO',
                                       'review_notification.enqueued expense_public_id=exp-pub-66 '
                                       'outbox_public_id=ob-1 mode=draft to=0 cc=2 bcc=0 '
                                       'attachment=(none)',
                                       False],
                                      ['INFO',
                                       'review_notification.in_review_advanced '
                                       'expense_public_id=exp-pub-66',
                                       False]]},
             'sparse_entity': {'result': None,
                               'calls': [['recipients.expense',
                                          [],
                                          {'exclude_user_id': 27, 'expense_id': 66}],
                                         ['expense_lines.read_by_expense_id', [66], {}],
                                         ['user.read_by_id', [27], {}],
                                         ['outbox.enqueue_send_mail',
                                          [],
                                          {'attachment': None,
                                           'bcc_addresses': [{'email': 'invoice@example.com',
                                                              'name': 'Invoice Inbox (archive)'}],
                                           'body': '<p>Pat/Zoë,</p><p>A new expense has been '
                                                   'submitted for review:</p><p>Project: (no '
                                                   'project)<br/>Vendor: (unknown '
                                                   'vendor)<br/>Reference: (no '
                                                   'reference)<br/>Amount: $0.00</p><p>Submitted '
                                                   'By: User 27<br/>Submitted Date: '
                                                   'garbage</p><p>When you have a moment, will you '
                                                   'please reply for approval with Sub Cost Code '
                                                   'and Description, or non-approval?</p>',
                                           'body_type': 'HTML',
                                           'cc_addresses': [{'email': 'olive@example.com',
                                                             'name': 'Olive Own'},
                                                            {'email': 'noname@example.com',
                                                             'name': 'noname@example.com'}],
                                           'entity_public_id': 'exp-pub-66',
                                           'entity_type': 'Expense',
                                           'mode': 'draft',
                                           'review_id': 9,
                                           'subject': '[Review] (unknown vendor) — Expense (no '
                                                      'reference) — (no project) — $0.00',
                                           'to_addresses': [{'email': 'pat@example.com',
                                                             'name': 'Pat Lee'},
                                                            {'email': 'blank@example.com',
                                                             'name': '   Blank'},
                                                            {'email': 'zoe@example.com',
                                                             'name': 'Zoë'}]}],
                                         ['status.read_all', [], {}],
                                         ['status.get_next_intermediate_status',
                                          [10],
                                          {'statuses': [{'ns': {'id': 1,
                                                                'name': 'Submitted',
                                                                'sort_order': 10}},
                                                        {'ns': {'id': 2,
                                                                'name': 'In Review',
                                                                'sort_order': 20}}]}],
                                         ['review.create',
                                          [],
                                          {'comments': None,
                                           'created_by_user_id': 9001,
                                           'email_message_id': None,
                                           'expense_id': 66,
                                           'review_status_id': 2,
                                           'user_id': 27}]],
                               'logs': [['WARNING',
                                         'review_notification.unreachable_recipients '
                                         'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                         'reason=no_contact_email',
                                         False],
                                        ['INFO',
                                         'review_notification.enqueued '
                                         'expense_public_id=exp-pub-66 outbox_public_id=ob-1 '
                                         'mode=draft to=3 cc=2 bcc=1 attachment=(none)',
                                         False],
                                        ['INFO',
                                         'review_notification.in_review_advanced '
                                         'expense_public_id=exp-pub-66',
                                         False]]},
             'status_unresolved': {'result': None,
                                   'calls': [['recipients.expense',
                                              [],
                                              {'exclude_user_id': 27, 'expense_id': 66}],
                                             ['vendor.read_by_id', [8], {}],
                                             ['expense_lines.read_by_expense_id', [66], {}],
                                             ['user.read_by_id', [27], {}],
                                             ['outbox.enqueue_send_mail',
                                              [],
                                              {'attachment': None,
                                               'bcc_addresses': [{'email': 'invoice@example.com',
                                                                  'name': 'Invoice Inbox '
                                                                          '(archive)'}],
                                               'body': '<p>Pat/Zoë,</p><p>A new expense has been '
                                                       'submitted for review:</p><p>Project: (no '
                                                       'project)<br/>Vendor: Acme &lt;Supply&gt; '
                                                       '&amp; Co<br/>Reference: R-77<br/>Amount: '
                                                       '$42.00<br/>Memo: Lunch &amp; '
                                                       '&lt;tools&gt;<br/>second<br/>third<br/>fourth</p><p>Submitted '
                                                       'By: Sam Sub<br/>Submitted Date: '
                                                       '10/02/2026</p><p>When you have a moment, '
                                                       'will you please reply for approval with '
                                                       'Sub Cost Code and Description, or '
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
                                               'subject': '[Review] Acme <Supply> & Co — Expense '
                                                          'R-77 — (no project) — $42.00',
                                               'to_addresses': [{'email': 'pat@example.com',
                                                                 'name': 'Pat Lee'},
                                                                {'email': 'blank@example.com',
                                                                 'name': '   Blank'},
                                                                {'email': 'zoe@example.com',
                                                                 'name': 'Zoë'}]}],
                                             ['status.read_all', [], {}]],
                                   'logs': [['WARNING',
                                             'review_notification.unreachable_recipients '
                                             'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                             'reason=no_contact_email',
                                             False],
                                            ['INFO',
                                             'review_notification.enqueued '
                                             'expense_public_id=exp-pub-66 outbox_public_id=ob-1 '
                                             'mode=draft to=3 cc=2 bcc=1 attachment=(none)',
                                             False],
                                            ['INFO',
                                             'review_notification.current_status_unresolved '
                                             'expense_public_id=exp-pub-66 review_status_id=404',
                                             False]]},
             'submitter_missing': {'result': None,
                                   'calls': [['recipients.expense',
                                              [],
                                              {'exclude_user_id': 27, 'expense_id': 66}],
                                             ['vendor.read_by_id', [8], {}],
                                             ['expense_lines.read_by_expense_id', [66], {}],
                                             ['project.read_by_id', [10], {}],
                                             ['elia.read_by_expense_line_item_id',
                                              [],
                                              {'expense_line_item_public_id': 'li-pub-1'}],
                                             ['scc.read_by_id', [100], {}],
                                             ['outbox.enqueue_send_mail',
                                              [],
                                              {'attachment': None,
                                               'bcc_addresses': [{'email': 'invoice@example.com',
                                                                  'name': 'Invoice Inbox '
                                                                          '(archive)'}],
                                               'body': '<p>Pat/Zoë,</p><p>A new expense has been '
                                                       'submitted for review:</p><p>Project: '
                                                       'HE12<br/>Vendor: (unknown '
                                                       'vendor)<br/>Reference: R-77<br/>Amount: '
                                                       '$42.00</p><p>Submitted By: User '
                                                       'None<br/>Submitted Date: '
                                                       "10/03/2026</p><table cellpadding='4' "
                                                       "cellspacing='0' border='1' "
                                                       "style='border-collapse:collapse; "
                                                       "margin-top:6px;'><tr><th "
                                                       "align='left'>Description</th><th "
                                                       "align='left'>Sub Cost Code</th><th "
                                                       "align='right'>Amount</th></tr><tr><td>Studs</td><td>01-100 "
                                                       'Framing</td><td '
                                                       "style='text-align:right;'>$12.30</td></tr></table><p>When "
                                                       'you have a moment, will you please reply '
                                                       'for approval with Sub Cost Code and '
                                                       'Description, or non-approval?</p>',
                                               'body_type': 'HTML',
                                               'cc_addresses': [{'email': 'olive@example.com',
                                                                 'name': 'Olive Own'},
                                                                {'email': 'noname@example.com',
                                                                 'name': 'noname@example.com'}],
                                               'entity_public_id': 'exp-pub-66',
                                               'entity_type': 'Expense',
                                               'mode': 'draft',
                                               'review_id': 9,
                                               'subject': '[Review] (unknown vendor) — Expense '
                                                          'R-77 — HE12 — $42.00',
                                               'to_addresses': [{'email': 'pat@example.com',
                                                                 'name': 'Pat Lee'},
                                                                {'email': 'blank@example.com',
                                                                 'name': '   Blank'},
                                                                {'email': 'zoe@example.com',
                                                                 'name': 'Zoë'}]}],
                                             ['status.read_all', [], {}],
                                             ['status.get_next_intermediate_status',
                                              [10],
                                              {'statuses': [{'ns': {'id': 1,
                                                                    'name': 'Submitted',
                                                                    'sort_order': 10}},
                                                            {'ns': {'id': 2,
                                                                    'name': 'In Review',
                                                                    'sort_order': 20}}]}],
                                             ['review.create',
                                              [],
                                              {'comments': None,
                                               'created_by_user_id': 9001,
                                               'email_message_id': None,
                                               'expense_id': 66,
                                               'review_status_id': 2,
                                               'user_id': None}]],
                                   'logs': [['WARNING',
                                             'review_notification.unreachable_recipients '
                                             'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                             'reason=no_contact_email',
                                             False],
                                            ['INFO',
                                             'review_notification.enqueued '
                                             'expense_public_id=exp-pub-66 outbox_public_id=ob-1 '
                                             'mode=draft to=3 cc=2 bcc=1 attachment=(none)',
                                             False],
                                            ['INFO',
                                             'review_notification.in_review_advanced '
                                             'expense_public_id=exp-pub-66',
                                             False]]},
             'unreachable_only_no_bcc': {'result': None,
                                         'calls': [['recipients.expense',
                                                    [],
                                                    {'exclude_user_id': 27, 'expense_id': 66}]],
                                         'logs': [['WARNING',
                                                   'review_notification.unreachable_recipients '
                                                   'expense_public_id=exp-pub-66 user_ids=[2, 6] '
                                                   'reason=no_contact_email',
                                                   False],
                                                  ['WARNING',
                                                   'review_notification.no_to_recipient '
                                                   'expense_public_id=exp-pub-66 '
                                                   'reason=no_project_manager_with_email — sending '
                                                   'anyway (BCC archive still active).',
                                                   False],
                                                  ['INFO',
                                                   'review_notification.no_cc_recipient '
                                                   'expense_public_id=exp-pub-66 '
                                                   'reason=no_owner_with_email',
                                                   False],
                                                  ['WARNING',
                                                   'review_notification.bcc_archive_skipped '
                                                   'expense_public_id=exp-pub-66 '
                                                   'reason=invoice_inbox_email_not_configured',
                                                   False],
                                                  ['ERROR',
                                                   'review_notification.skipped '
                                                   'expense_public_id=exp-pub-66 '
                                                   'reason=no_recipients_on_any_line',
                                                   False]]},
             'zero_recipients_no_bcc': {'result': None,
                                        'calls': [['recipients.expense',
                                                   [],
                                                   {'exclude_user_id': 27, 'expense_id': 66}]],
                                        'logs': [['WARNING',
                                                  'review_notification.no_to_recipient '
                                                  'expense_public_id=exp-pub-66 '
                                                  'reason=no_project_manager_with_email — sending '
                                                  'anyway (BCC archive still active).',
                                                  False],
                                                 ['INFO',
                                                  'review_notification.no_cc_recipient '
                                                  'expense_public_id=exp-pub-66 '
                                                  'reason=no_owner_with_email',
                                                  False],
                                                 ['WARNING',
                                                  'review_notification.bcc_archive_skipped '
                                                  'expense_public_id=exp-pub-66 '
                                                  'reason=invoice_inbox_email_not_configured',
                                                  False],
                                                 ['ERROR',
                                                  'review_notification.skipped '
                                                  'expense_public_id=exp-pub-66 '
                                                  'reason=no_recipients_on_any_line',
                                                  False]]}}}
# fmt: on

