"""U-797c4a: the BillCredit review-submit email (behaviour).

Mirrors the Bill review email. Everything is faked at the source-module seams the
service imports lazily. No DB, no Graph, no network.

Names from production code are read at call time (`ns.X`), not imported at module
top, so each test fails on its own when the code is missing.
"""

from contextlib import ExitStack, contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business import notification_service as ns
from entities.review.business import service as review_service_mod
from entities.review.business.recipient_model import ResolvedRecipient
from entities.review.business.recipient_service import ReviewRecipientService
from entities.review.persistence.recipient_repo import ReviewRecipientRepository
from integrations.ms.outbox.business.worker import MsOutboxWorker

SYSTEM_ACTOR = 4242
STATUS_SUBMITTED = SimpleNamespace(id=1, sort_order=10, name="Submitted")
STATUS_IN_REVIEW = SimpleNamespace(id=2, sort_order=20, name="In Review")
STATUSES = [STATUS_SUBMITTED, STATUS_IN_REVIEW]
ARCHIVE = "invoice@example.com"


def _credit(**over):
    base = dict(
        id=9, public_id="bc-pub-1", vendor_id=3, credit_number="CR-100",
        total_amount=Decimal("125.00"), created_datetime="2026-11-20 10:00:00",
        memo=None, is_draft=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _review(**over):
    base = dict(id=55, user_id=7, review_status_id=1)
    base.update(over)
    return SimpleNamespace(**base)


def _rec(uid, first, last, email, role, project_id=10):
    return ResolvedRecipient(
        user_id=uid, firstname=first, lastname=last, email=email,
        role_name=role, project_id=project_id,
    )


PM = _rec(1, "Pat", "Lee", "pat@example.com", "Project Manager")
OWNER = _rec(5, "Olive", "Own", "olive@example.com", "Owner")
LINE = SimpleNamespace(
    public_id="li-pub-1", project_id=10, sub_cost_code_id=4,
    description="Return", amount=Decimal("125.00"),
)


def _instance(**members):
    """A fake service instance. A callable value becomes side_effect, anything else return_value."""
    inst = MagicMock()
    for name, value in members.items():
        method = getattr(inst, name)
        if callable(value) and not isinstance(value, SimpleNamespace):
            method.side_effect = value
        else:
            method.return_value = value
    return inst


def _labels(pid):
    return {
        10: SimpleNamespace(abbreviation="HE12", name="Hidden Elm 12"),
        11: SimpleNamespace(abbreviation="OAK", name="Oak Ln"),
    }.get(pid)


# ─── pure builders: subject / body goldens ──────────────────────────────────


def _subject(*, number="CR-100", project="HE12", amount=Decimal("125.00"),
             spec=None, vendor="Acme"):
    return ns.ReviewNotificationService._build_subject(
        spec=spec or ns.BILL_CREDIT_SPEC, vendor_name=vendor, number=number,
        project_label=project, total_amount=amount,
    )


def _body(*, parent, project="HE12", to=(), lines=(), scc=None, attachment=None,
          vendor="Acme", submitter="Sam Sub", spec=None):
    return ns.ReviewNotificationService._build_html_body(
        spec=spec or ns.BILL_CREDIT_SPEC, parent=parent, vendor_name=vendor,
        project_label=project, submitter_name=submitter, line_items=list(lines),
        scc_label_by_id=scc or {}, to_recipients=list(to), attachment_filename=attachment,
    )


def test_spec_identity():
    spec = ns.BILL_CREDIT_SPEC
    assert spec.kind == "bill_credit"
    assert spec.entity_type == "BillCredit"
    assert spec.review_parent_kwarg == "bill_credit_id"
    assert spec.outbox_parent_kwarg is None
    assert spec.number_attr == "credit_number"
    assert spec.subject_number_prefix == "Credit #"
    assert spec.body_number_label == "Credit #"
    assert spec.renders_memo is False
    assert spec.noun == "bill credit"


def test_credit_subject_golden():
    assert _subject() == "[Review] Acme — Credit #CR-100 — HE12 — $125.00"


def test_credit_subject_multi_project_label():
    assert _subject(project="HE12, OAK") == "[Review] Acme — Credit #CR-100 — HE12, OAK — $125.00"


def test_credit_subject_no_project_label():
    assert _subject(project="(no project)") == "[Review] Acme — Credit #CR-100 — (no project) — $125.00"


def test_credit_subject_missing_number_placeholder():
    assert _subject(number=None) == "[Review] Acme — Credit #(no number) — HE12 — $125.00"


def test_credit_body_golden_with_greeting_and_noun():
    body = _body(parent=_credit(total_amount=Decimal("125.00")), to=[SimpleNamespace(firstname="Pat")])
    assert body == (
        "<p>Pat,</p>"
        "<p>A new bill credit has been submitted for review:</p>"
        "<p>Project: HE12<br/>Vendor: Acme<br/>Credit #: CR-100<br/>Amount: $125.00</p>"
        "<p>Submitted By: Sam Sub<br/>Submitted Date: 11/20/2026</p>"
        + ns.BILL_EXPENSE_ASK
    )


def test_credit_body_amount_uses_bill_formatting():
    body = _body(parent=_credit(total_amount=Decimal("125.00")))
    assert "Amount: $125.00" in body


def test_credit_body_line_amounts_are_escaped():
    line = SimpleNamespace(public_id="li-1", project_id=None, sub_cost_code_id=4,
                           description="Return <A>", amount=Decimal("10"))
    body = _body(parent=_credit(), project="HE12", lines=[line], scc={4: "01 General"})
    assert "<td>Return &lt;A&gt;</td><td>01 General</td><td style='text-align:right;'>$10.00</td>" in body


def test_credit_body_missing_number_placeholder():
    body = _body(parent=_credit(credit_number=None))
    assert "Credit #: (no number)<br/>" in body


def test_bill_and_expense_subject_and_noun_unchanged():
    assert _subject(spec=ns.BILL_SPEC, number="B-1", amount=Decimal("125")) == \
        "[Review] Acme — Bill #B-1 — HE12 — $125.00"
    assert _subject(spec=ns.EXPENSE_SPEC, number="E-1", amount=Decimal("125")) == \
        "[Review] Acme — Expense E-1 — HE12 — $125.00"

    bill = SimpleNamespace(bill_number="B-1", total_amount=Decimal("125"),
                           created_datetime="2026-11-20 10:00:00", memo=None)
    expense = SimpleNamespace(reference_number="E-1", total_amount=Decimal("125"),
                              created_datetime="2026-11-20 10:00:00", memo=None)
    assert "<p>A new bill has been submitted for review:</p>" in _body(parent=bill, spec=ns.BILL_SPEC)
    assert "<p>A new expense has been submitted for review:</p>" in _body(parent=expense, spec=ns.EXPENSE_SPEC)


# ─── end to end through enqueue_for_bill_credit, all collaborators faked ────


@contextmanager
def _world(*, recipients=(PM, OWNER), lines=(LINE,), inbox=ARCHIVE, linked=True,
           next_status=STATUS_IN_REVIEW):
    settings = MagicMock()
    settings.invoice_inbox_email = inbox

    outbox = _instance(enqueue_send_mail=SimpleNamespace(public_id="ob-9", id=99))
    records = _instance(
        try_record_for_review=SimpleNamespace(Id=77),
        try_attach_outbox=True,
        try_release=True,
    )
    recipient_repo = _instance(resolve_for_bill_credit=list(recipients))
    review_create = MagicMock(name="ReviewService.create")
    review_svc = _instance(create=review_create)
    status_svc = _instance(read_all=list(STATUSES), get_next_intermediate_status=next_status)
    link_svc = _instance(read_by_bill_credit_line_item_id=(
        SimpleNamespace(attachment_id=21) if linked else None))
    attach_svc = _instance(read_by_id=SimpleNamespace(
        blob_url="https://blob.example/credit.pdf", content_type="application/pdf",
        filename="credit.pdf", public_id="att-21"))

    with ExitStack() as stack:
        stack.enter_context(patch("config.Settings", MagicMock(return_value=settings)))
        stack.enter_context(patch(
            "entities.review.business.recipient_service.ReviewRecipientRepository",
            MagicMock(return_value=recipient_repo)))
        stack.enter_context(patch(
            "entities.bill_credit_line_item.business.service.BillCreditLineItemService",
            MagicMock(return_value=_instance(read_by_bill_credit_id=list(lines)))))
        stack.enter_context(patch(
            "entities.bill_credit_line_item_attachment.business.service.BillCreditLineItemAttachmentService",
            MagicMock(return_value=link_svc)))
        stack.enter_context(patch(
            "entities.attachment.business.service.AttachmentService",
            MagicMock(return_value=attach_svc)))
        stack.enter_context(patch(
            "entities.vendor.business.service.VendorService",
            MagicMock(return_value=_instance(read_by_id=SimpleNamespace(name="Acme")))))
        stack.enter_context(patch(
            "entities.project.business.service.ProjectService",
            MagicMock(return_value=_instance(read_by_id=_labels))))
        stack.enter_context(patch(
            "entities.sub_cost_code.business.service.SubCostCodeService",
            MagicMock(return_value=_instance(read_by_id=SimpleNamespace(number="01", name="General")))))
        stack.enter_context(patch(
            "entities.user.business.service.UserService",
            MagicMock(return_value=_instance(read_by_id=SimpleNamespace(firstname="Sam", lastname="Sub")))))
        stack.enter_context(patch(
            "integrations.ms.outbox.business.service.writes_allowed", return_value=True))
        stack.enter_context(patch(
            "integrations.ms.outbox.business.service.MsOutboxService",
            MagicMock(return_value=outbox)))
        stack.enter_context(patch(
            "entities.review.business.review_notification_service.ReviewNotificationRecordService",
            MagicMock(return_value=records)))
        stack.enter_context(patch(
            "entities.review_status.business.service.ReviewStatusService",
            MagicMock(return_value=status_svc)))
        stack.enter_context(patch(
            "entities.review.business.service.ReviewService",
            MagicMock(return_value=review_svc)))
        stack.enter_context(patch("shared.authz.system_actor_user_id", return_value=SYSTEM_ACTOR))
        yield SimpleNamespace(outbox=outbox, records=records, review_create=review_create)


def _enqueue(credit=None, review=None, exclude_user_id=None):
    ns.ReviewNotificationService().enqueue_for_bill_credit(
        bill_credit=credit or _credit(),
        review=review or _review(),
        exclude_user_id=exclude_user_id,
    )


def test_enqueue_payload_is_a_bill_credit_draft():
    with _world() as w:
        _enqueue(exclude_user_id=7)
    kw = w.outbox.enqueue_send_mail.call_args.kwargs
    assert kw["entity_type"] == "BillCredit"
    assert kw["entity_public_id"] == "bc-pub-1"
    assert kw["subject"] == "[Review] Acme — Credit #CR-100 — HE12 — $125.00"
    assert kw["to_addresses"] == [{"email": "pat@example.com", "name": "Pat Lee"}]
    assert kw["cc_addresses"] == [{"email": "olive@example.com", "name": "Olive Own"}]
    assert kw["bcc_addresses"] == [{"email": ARCHIVE, "name": ns.BILL_EXPENSE_ARCHIVE_NAME}]
    assert kw["mode"] == "draft"
    assert kw["body_type"] == "HTML"
    assert kw["review_id"] == 55
    assert kw["review_notification_id"] == 77
    assert kw["attachment"] == {
        "name": "credit.pdf", "content_type": "application/pdf",
        "blob_url": "https://blob.example/credit.pdf",
    }
    assert "bill_id" not in kw
    assert "Amount: $125.00" in kw["body"]
    assert "A new bill credit has been submitted for review:" in kw["body"]


def test_record_is_written_as_bill_credit_kind_with_bill_credit_target():
    with _world() as w:
        _enqueue()
    kw = w.records.try_record_for_review.call_args.kwargs
    assert kw["kind"] == "bill_credit"
    assert kw["review_id"] == 55
    assert kw["targets"] == [("bill_credit", 9)]
    assert kw["subject"] == "[Review] Acme — Credit #CR-100 — HE12 — $125.00"


def test_auto_advances_to_in_review_when_recipients_exist():
    with _world() as w:
        _enqueue(exclude_user_id=7)
    w.review_create.assert_called_once()
    kw = w.review_create.call_args.kwargs
    assert kw["review_status_id"] == STATUS_IN_REVIEW.id
    assert kw["bill_credit_id"] == 9
    assert "bill_id" not in kw
    assert kw["user_id"] == 7
    assert kw["created_by_user_id"] == SYSTEM_ACTOR
    assert kw["email_message_id"] is None
    assert kw["comments"] is None


def test_archive_only_sends_bcc_and_does_not_advance():
    with _world(recipients=()) as w:
        _enqueue()
    kw = w.outbox.enqueue_send_mail.call_args.kwargs
    assert kw["to_addresses"] == []
    assert kw["cc_addresses"] == []
    assert kw["bcc_addresses"] == [{"email": ARCHIVE, "name": ns.BILL_EXPENSE_ARCHIVE_NAME}]
    w.review_create.assert_not_called()


def test_no_recipients_and_no_archive_enqueues_nothing_and_does_not_advance():
    with _world(recipients=(), inbox=None) as w:
        _enqueue()
    w.outbox.enqueue_send_mail.assert_not_called()
    w.review_create.assert_not_called()


def test_no_project_label_when_no_line_item_has_a_project():
    orphan = SimpleNamespace(public_id="li-pub-1", project_id=None, sub_cost_code_id=None,
                             description="x", amount=Decimal("1"))
    with _world(lines=(orphan,)) as w:
        _enqueue()
    kw = w.outbox.enqueue_send_mail.call_args.kwargs
    assert kw["subject"] == "[Review] Acme — Credit #CR-100 — (no project) — $125.00"


def test_multi_project_label_in_subject():
    second = SimpleNamespace(public_id="li-pub-2", project_id=11, sub_cost_code_id=4,
                             description="y", amount=Decimal("5"))
    with _world(lines=(LINE, second)) as w:
        _enqueue()
    kw = w.outbox.enqueue_send_mail.call_args.kwargs
    assert kw["subject"] == "[Review] Acme — Credit #CR-100 — HE12, OAK — $125.00"


def test_no_attachment_linked_sends_without_attachment():
    with _world(linked=False) as w:
        _enqueue()
    kw = w.outbox.enqueue_send_mail.call_args.kwargs
    assert kw["attachment"] is None


def test_enqueue_for_bill_credit_never_raises():
    with patch.object(ns.ReviewNotificationService, "_do_enqueue", side_effect=RuntimeError("boom")):
        assert ns.ReviewNotificationService().enqueue_for_bill_credit(
            bill_credit=_credit(), review=_review()) is None


# ─── ReviewService.create hook ──────────────────────────────────────────────


def _create(*, status_id=1, first_id=1, bill_credit_id=9, enqueue_error=None, read_error=None):
    repo = _instance(create=_review(review_status_id=status_id))
    status_svc = _instance(get_first_status=SimpleNamespace(id=first_id))
    svc = review_service_mod.ReviewService(repo=repo, review_status_service=status_svc)

    credit_svc = MagicMock()
    if read_error is not None:
        credit_svc.return_value.read_by_id.side_effect = read_error
    else:
        credit_svc.return_value.read_by_id.return_value = _credit()
    notif = MagicMock()
    if enqueue_error is not None:
        notif.return_value.enqueue_for_bill_credit.side_effect = enqueue_error

    with patch("entities.bill_credit.business.service.BillCreditService", credit_svc), \
         patch("entities.review.business.notification_service.ReviewNotificationService", notif):
        out = svc.create(
            review_status_id=status_id, user_id=7, bill_credit_id=bill_credit_id,
            created_by_user_id=7,
        )
    return SimpleNamespace(out=out, status=status_svc, notif=notif, credit=credit_svc)


def test_hook_fires_on_initial_submit():
    r = _create(status_id=1, first_id=1)
    r.status.get_first_status.assert_called_once()
    r.notif.return_value.enqueue_for_bill_credit.assert_called_once()
    kw = r.notif.return_value.enqueue_for_bill_credit.call_args.kwargs
    assert kw["bill_credit"] == _credit()
    assert kw["review"].id == 55
    assert kw["exclude_user_id"] == 7


def test_hook_does_not_fire_on_later_transitions():
    r = _create(status_id=2, first_id=1)
    r.notif.return_value.enqueue_for_bill_credit.assert_not_called()


def test_hook_swallows_a_read_failure():
    r = _create(read_error=RuntimeError("db down"))
    assert r.out.id == 55
    r.notif.return_value.enqueue_for_bill_credit.assert_not_called()


def test_hook_swallows_an_enqueue_failure():
    r = _create(enqueue_error=RuntimeError("boom"))
    assert r.out.id == 55


# ─── worker stamp set ───────────────────────────────────────────────────────


def test_worker_stamp_set_includes_bill_credit_and_keeps_existing_types():
    types = MsOutboxWorker._REVIEW_STAMP_ENTITY_TYPES
    assert "BillCredit" in types
    assert {"Bill", "Expense", "ContractLabor"} <= set(types)


def test_worker_stamps_a_bill_credit_row_without_a_payload_id():
    worker = MsOutboxWorker.__new__(MsOutboxWorker)
    row = SimpleNamespace(id=5, entity_type="BillCredit")
    with patch("entities.review.business.review_notification_service.stamp_review_notification") as stamp:
        worker._stamp_review_notification(row, {"conversation_id": "c-1"}, "g-1")
    stamp.assert_called_once()
    assert stamp.call_args.kwargs["outbox_id"] == 5


# ─── recipients ─────────────────────────────────────────────────────────────


def test_repo_uses_the_bill_credit_sproc():
    repo = ReviewRecipientRepository()
    with patch.object(ReviewRecipientRepository, "_resolve", return_value=["r"]) as resolve:
        out = repo.resolve_for_bill_credit(bill_credit_id=9, exclude_user_id=7)
    resolve.assert_called_once_with(
        sproc="ResolveReviewRecipientsByBillCreditId",
        id_key="BillCreditId",
        parent_id=9,
        exclude_user_id=7,
    )
    assert out == ["r"]


def test_service_buckets_project_managers_to_and_owners_cc():
    repo = MagicMock()
    repo.resolve_for_bill_credit.return_value = [PM, OWNER]
    env = ReviewRecipientService(repo=repo).resolve_for_bill_credit(bill_credit_id=9)
    repo.resolve_for_bill_credit.assert_called_once_with(bill_credit_id=9, exclude_user_id=None)
    assert env["to"] == [PM]
    assert env["cc"] == [OWNER]


def test_bill_credit_envelope_excludes_the_submitter(monkeypatch):
    # Mutation M09: the envelope must pass the submitter through so the resolver can exclude them.
    from types import SimpleNamespace
    from entities.review.business import notification_service as _ns
    from entities.review.business.recipient_service import ReviewRecipientService

    seen = {}

    def fake(self, *, bill_credit_id, exclude_user_id=None):
        seen.update(bill_credit_id=bill_credit_id, exclude_user_id=exclude_user_id)
        return {"to": [], "cc": []}

    monkeypatch.setattr(ReviewRecipientService, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(ReviewRecipientService, "resolve_for_bill_credit", fake)
    assert _ns._bill_credit_envelope(SimpleNamespace(id=5), 9) == {"to": [], "cc": []}
    assert seen == {"bill_credit_id": 5, "exclude_user_id": 9}
