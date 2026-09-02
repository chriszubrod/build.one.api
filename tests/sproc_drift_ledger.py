"""Frozen debt baseline for the U-107 default-ON sproc single-source ratchet.

This ledger is the U-107 ratchet's frozen debt baseline; entries may ONLY be
deleted (when a dup is single-sourced or a home-less sproc is homed into a base
file) or shrunk; NEVER add or extend an entry — new drift must be fixed, not
ledgered.
"""

SPROC_DRIFT_LEDGER: dict[str, frozenset[str]] = {
    "CreateBillLineItemAttachment": frozenset({
        'entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql
    "CreateCostCode": frozenset({
        'entities/cost_code/sql/dbo.costcode.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/cost_code/sql/dbo.costcode.sql
    "CreateCustomer": frozenset({
        'entities/customer/sql/dbo.customer.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/customer/sql/dbo.customer.sql
    "CreateExpenseLineItemAttachment": frozenset({
        'entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql
    "CreateInvoiceLineItemAttachment": frozenset({
        'entities/invoice_line_item_attachment/sql/dbo.invoice_line_item_attachment.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/invoice_line_item_attachment/sql/dbo.invoice_line_item_attachment.sql
    "CreatePaymentTerm": frozenset({
        'entities/payment_term/sql/dbo.payment_term.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/payment_term/sql/dbo.payment_term.sql
    "CreateProjectAddress": frozenset({
        'entities/project_address/sql/dbo.project_address.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/project_address/sql/dbo.project_address.sql
    "CreateReviewStatus": frozenset({
        'entities/review_status/sql/dbo.review_status.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/review_status/sql/dbo.review_status.sql
    "CreateSubCostCode": frozenset({
        'entities/sub_cost_code/sql/dbo.subcostcode.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/sub_cost_code/sql/dbo.subcostcode.sql
    "CreateVendorCreditLineItemBillCreditLineItem": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql',
        'scripts/migrations/qbo_vendorcredit_mapping_sprocs_dbo.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql
    "CreateWorkflow": frozenset({
        'core/workflow/sql/dbo.workflow.sql',
        'core/workflow/sql/migrations/002_phase4_attribution_sprocs.sql',
    }),  # known-dup, home=core/workflow/sql/dbo.workflow.sql
    "CreateWorkflowEvent": frozenset({
        'core/workflow_event/sql/dbo.workflow_event.sql',
        'core/workflow_event/sql/migrations/002_phase4_attribution_sprocs.sql',
    }),  # known-dup, home=core/workflow_event/sql/dbo.workflow_event.sql
    "DeleteQboVendorCreditByQboId": frozenset({
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
        'scripts/migrations/qbo_vendorcredit_reconcile_deletes.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "DeleteQboVendorCreditLineById": frozenset({
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
        'scripts/migrations/qbo_vendorcredit_upsert_inplace.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "DeleteVendorCreditLineItemBillCreditLineItemById": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql',
        'scripts/migrations/qbo_vendorcredit_mapping_sprocs_dbo.sql',
        'scripts/migrations/qbo_vendorcredit_upsert_inplace.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql
    "ReadEmailMessageByGraphMessageId": frozenset({
        'entities/email_message/sql/dbo.email_message.sql',
        'entities/email_message/sql/dbo.email_message_recipients.sql',
    }),  # known-dup, home=entities/email_message/sql/dbo.email_message.sql, entities/email_message/sql/dbo.email_message_recipients.sql
    "ReadEmailMessageById": frozenset({
        'entities/email_message/sql/dbo.email_message.sql',
        'entities/email_message/sql/dbo.email_message_recipients.sql',
    }),  # known-dup, home=entities/email_message/sql/dbo.email_message.sql, entities/email_message/sql/dbo.email_message_recipients.sql
    "ReadEmailMessageByPublicId": frozenset({
        'entities/email_message/sql/dbo.email_message.sql',
        'entities/email_message/sql/dbo.email_message_recipients.sql',
    }),  # known-dup, home=entities/email_message/sql/dbo.email_message.sql, entities/email_message/sql/dbo.email_message_recipients.sql
    "ReadEmailSenderHistory": frozenset({
        'entities/email_message/sql/dbo.email_message.sql',
        'entities/email_message/sql/migrations/002_contract_labor_timesheet_vocab.sql',
        'entities/email_message/sql/migrations/003_delegated_to_contract_labor_action_vocab.sql',
    }),  # known-dup, home=entities/email_message/sql/dbo.email_message.sql
    "ReadProjectsByUserId": frozenset({
        'entities/project/sql/dbo.project.sql',
        'entities/project/sql/migrations/003_read_projects_by_user_id_admin_bypass.sql',
    }),  # known-dup, home=entities/project/sql/dbo.project.sql
    "ReadQboVendorCreditLineByVendorCreditIdAndQboLineId": frozenset({
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
        'scripts/migrations/qbo_vendorcredit_upsert_inplace.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "ReadVendorCreditLineItemBillCreditLineItemByBillCreditLineItemId": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql',
        'scripts/migrations/qbo_vendorcredit_mapping_sprocs_dbo.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql
    "ReadVendorCreditLineItemBillCreditLineItemByQboLineId": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql',
        'scripts/migrations/qbo_vendorcredit_mapping_sprocs_dbo.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql
    "RecoverStuckProcessingEmailMessages": frozenset({
        'entities/email_message/sql/dbo.email_message.sql',
        'entities/email_message/sql/migrations/001_recovery_processing_reset.sql',
        'entities/email_message/sql/migrations/007_recover_stuck_max_rows.sql',
    }),  # known-dup, home=entities/email_message/sql/dbo.email_message.sql
    "TimeoutLongRunningAgentSessions": frozenset({
        'intelligence/persistence/sql/dbo.agent_session.sql',
        'intelligence/persistence/sql/migrations/001_timeout_long_running_sessions.sql',
        'intelligence/persistence/sql/migrations/002_timeout_max_rows.sql',
    }),  # known-dup, home=intelligence/persistence/sql/dbo.agent_session.sql
    "UpdateQboVendorCreditLineById": frozenset({
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
        'scripts/migrations/qbo_vendorcredit_upsert_inplace.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "UpsertEmailAttachment": frozenset({
        'entities/email_message/sql/dbo.email_attachment.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/email_message/sql/dbo.email_attachment.sql
    "UpsertEmailMessage": frozenset({
        'entities/email_message/sql/dbo.email_message.sql',
        'entities/email_message/sql/dbo.email_message_recipients.sql',
        'entities/email_message/sql/migrations/004_imid_merge_key.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/email_message/sql/dbo.email_message.sql, entities/email_message/sql/dbo.email_message_recipients.sql
    "InvalidateBoxFile": frozenset({
        'integrations/box/file/sql/box.file.sql',
        'scripts/migrations/u256_box_integrity.sql',
    }),  # known-dup, home=integrations/box/file/sql/box.file.sql (U-256 self-contained migration)
    "ReadBoxFileByBoxFileId": frozenset({
        'integrations/box/file/sql/box.file.sql',
        'scripts/migrations/u256_box_integrity.sql',
    }),  # known-dup, home=integrations/box/file/sql/box.file.sql (U-256 self-contained migration)
    "ReadBoxFilesByEntity": frozenset({
        'integrations/box/file/sql/box.file.sql',
        'scripts/migrations/u256_box_integrity.sql',
    }),  # known-dup, home=integrations/box/file/sql/box.file.sql (U-256 self-contained migration)
    "ReadBoxWorkbookEntityPush": frozenset({
        'integrations/box/excel/sql/box.workbook_entity_push.sql',
        'scripts/migrations/u256_box_integrity.sql',
    }),  # known-dup, home=integrations/box/excel/sql/box.workbook_entity_push.sql (U-256 self-contained migration)
    "ReadBoxWorkbookEntityPushByFile": frozenset({
        'integrations/box/excel/sql/box.workbook_entity_push.sql',
        'scripts/migrations/u256_box_integrity.sql',
    }),  # known-dup, home=integrations/box/excel/sql/box.workbook_entity_push.sql (U-256 self-contained migration)
    "ReadRecentBoxFiles": frozenset({
        'integrations/box/file/sql/box.file.sql',
        'scripts/migrations/u256_box_integrity.sql',
    }),  # known-dup, home=integrations/box/file/sql/box.file.sql (U-256 self-contained migration)
    "UpsertBoxFile": frozenset({
        'integrations/box/file/sql/box.file.sql',
        'scripts/migrations/u256_box_integrity.sql',
    }),  # known-dup, home=integrations/box/file/sql/box.file.sql (U-256 self-contained migration)
    "UpsertBoxWorkbookEntityPush": frozenset({
        'integrations/box/excel/sql/box.workbook_entity_push.sql',
        'scripts/migrations/u256_box_integrity.sql',
    }),  # known-dup, home=integrations/box/excel/sql/box.workbook_entity_push.sql (U-256 self-contained migration)
}
