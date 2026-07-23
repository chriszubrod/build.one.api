"""Frozen debt baseline for the U-107 default-ON sproc single-source ratchet.

This ledger is the U-107 ratchet's frozen debt baseline; entries may ONLY be
deleted (when a dup is single-sourced or a home-less sproc is homed into a base
file) or shrunk; NEVER add or extend an entry — new drift must be fixed, not
ledgered.
"""

SPROC_DRIFT_LEDGER: dict[str, frozenset[str]] = {
    "AggregateTimeEntryOnSubmit": frozenset({
        'entities/time_entry/sql/migrations/001_2026_05_27_aggregate_on_submit.sql',
        'entities/time_entry/sql/migrations/002_2026_05_27_aggregate_contract_labor_lineage.sql',
        'entities/time_entry/sql/migrations/008_2026_05_28_aggregate_with_line_items.sql',
        'entities/time_entry/sql/migrations/009_2026_06_03_aggregate_parent_per_time_entry.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "CountInvoices": frozenset({
        'entities/invoice/sql/dbo.invoice.sql',
        'scripts/migrations/gap1_list_sprocs_scoped.sql',
    }),  # known-dup, home=entities/invoice/sql/dbo.invoice.sql
    "CountMsOutboxByEntity": frozenset({
        'entities/time_entry/sql/migrations/011_2026_06_16_time_entry_digest.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "CreateAttachment": frozenset({
        'entities/attachment/sql/dbo.attachment.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/attachment/sql/dbo.attachment.sql
    "CreateBillLineItemAttachment": frozenset({
        'entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql
    "CreateCompany": frozenset({
        'entities/company/sql/dbo.company.sql',
        'entities/company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/company/sql/dbo.company.sql
    "CreateContractLabor": frozenset({
        'entities/contract_labor/sql/dbo.contract_labor.sql',
        'scripts/migrations/gap2_core_threading.sql',
    }),  # known-dup, home=entities/contract_labor/sql/dbo.contract_labor.sql
    "CreateContractLaborLineItem": frozenset({
        'entities/contract_labor/sql/dbo.contract_labor.sql',
        'scripts/migrations/gap2_core_threading.sql',
    }),  # known-dup, home=entities/contract_labor/sql/dbo.contract_labor.sql
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
    "CreateInvoice": frozenset({
        'entities/invoice/sql/dbo.invoice.sql',
        'scripts/migrations/gap2_core_threading.sql',
    }),  # known-dup, home=entities/invoice/sql/dbo.invoice.sql
    "CreateInvoiceLineItem": frozenset({
        'entities/invoice_line_item/sql/dbo.invoice_line_item.sql',
        'entities/invoice_line_item/sql/migrations/001_2026_05_27_employee_labor_source.sql',
    }),  # known-dup, home=entities/invoice_line_item/sql/dbo.invoice_line_item.sql
    "CreateInvoiceLineItemAttachment": frozenset({
        'entities/invoice_line_item_attachment/sql/dbo.invoice_line_item_attachment.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/invoice_line_item_attachment/sql/dbo.invoice_line_item_attachment.sql
    "CreateOrganization": frozenset({
        'entities/organization/sql/dbo.organization.sql',
        'entities/organization/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/organization/sql/dbo.organization.sql
    "CreatePaymentTerm": frozenset({
        'entities/payment_term/sql/dbo.payment_term.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/payment_term/sql/dbo.payment_term.sql
    "CreateProjectAddress": frozenset({
        'entities/project_address/sql/dbo.project_address.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/project_address/sql/dbo.project_address.sql
    "CreateReview": frozenset({
        'entities/review/sql/dbo.review.sql',
        'entities/review/sql/migrations/005_review_sprocs_contract_labor.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/review/sql/dbo.review.sql
    "CreateReviewStatus": frozenset({
        'entities/review_status/sql/dbo.review_status.sql',
        'scripts/migrations/gap2_adjacent_threading.sql',
    }),  # known-dup, home=entities/review_status/sql/dbo.review_status.sql
    "CreateSubCostCode": frozenset({
        'entities/sub_cost_code/sql/dbo.subcostcode.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/sub_cost_code/sql/dbo.subcostcode.sql
    "CreateUser": frozenset({
        'entities/user/sql/dbo.user.sql',
        'entities/user/sql/migrations/003_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user/sql/dbo.user.sql
    "CreateUserCompany": frozenset({
        'entities/user_company/sql/dbo.usercompany.sql',
        'entities/user_company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user_company/sql/dbo.usercompany.sql
    "CreateUserModule": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "CreateUserProject": frozenset({
        'entities/user_project/sql/dbo.userproject.sql',
        'entities/user_project/sql/migrations/002_phase1_attribution_sprocs.sql',
        'entities/user_project/sql/migrations/004_role_qualifier_sprocs.sql',
    }),  # known-dup, home=entities/user_project/sql/dbo.userproject.sql
    "CreateUserRole": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "CreateVendor": frozenset({
        'entities/vendor/sql/dbo.vendor.sql',
        'entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql',
        'entities/vendor/sql/migrations/003_2026_07_19_track_compliance.sql',
        'scripts/migrations/gap2_reference_threading.sql',
    }),  # known-dup, home=entities/vendor/sql/dbo.vendor.sql
    "CreateVendorCreditBillCredit": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql',
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql, integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
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
    "DeleteCompanyById": frozenset({
        'entities/company/sql/dbo.company.sql',
        'entities/company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/company/sql/dbo.company.sql
    "DeleteOrganizationById": frozenset({
        'entities/organization/sql/dbo.organization.sql',
        'entities/organization/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/organization/sql/dbo.organization.sql
    "DeleteQboVendorCreditByQboId": frozenset({
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
        'scripts/migrations/qbo_vendorcredit_reconcile_deletes.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "DeleteQboVendorCreditLineById": frozenset({
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
        'scripts/migrations/qbo_vendorcredit_upsert_inplace.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "DeleteReviewsByContractLaborId": frozenset({
        'entities/review/sql/migrations/005_review_sprocs_contract_labor.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "DeleteUserById": frozenset({
        'entities/user/sql/dbo.user.sql',
        'entities/user/sql/migrations/003_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user/sql/dbo.user.sql
    "DeleteUserCompanyById": frozenset({
        'entities/user_company/sql/dbo.usercompany.sql',
        'entities/user_company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user_company/sql/dbo.usercompany.sql
    "DeleteUserModuleById": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "DeleteUserProjectById": frozenset({
        'entities/user_project/sql/dbo.userproject.sql',
        'entities/user_project/sql/migrations/002_phase1_attribution_sprocs.sql',
        'entities/user_project/sql/migrations/004_role_qualifier_sprocs.sql',
    }),  # known-dup, home=entities/user_project/sql/dbo.userproject.sql
    "DeleteUserRoleById": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "DeleteVendorCreditBillCreditByQboVendorCreditId": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql',
        'scripts/migrations/qbo_vendorcredit_reconcile_deletes.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql
    "DeleteVendorCreditLineItemBillCreditLineItemById": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql',
        'scripts/migrations/qbo_vendorcredit_mapping_sprocs_dbo.sql',
        'scripts/migrations/qbo_vendorcredit_upsert_inplace.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql
    "FindContractLaborForReviewerReply": frozenset({
        'entities/contract_labor/sql/dbo.contract_labor.sql',
        'scripts/migrations/2026_05_27_find_contract_labor_for_reviewer_reply.sql',
    }),  # known-dup, home=entities/contract_labor/sql/dbo.contract_labor.sql
    "FindContractLaborVendorByEmail": frozenset({
        'entities/vendor/sql/dbo.vendor.sql',
        'entities/vendor/sql/migrations/001_find_contract_labor_vendor_by_email.sql',
    }),  # known-dup, home=entities/vendor/sql/dbo.vendor.sql
    "IsTimeEntryDownstreamLocked": frozenset({
        'entities/time_entry/sql/migrations/003_2026_05_27_downstream_lock_check.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadAttachmentByCategory": frozenset({
        'entities/attachment/sql/dbo.attachment.sql',
        'entities/attachment/sql/update_procedures_with_extraction.sql',
    }),  # known-dup, home=entities/attachment/sql/dbo.attachment.sql, entities/attachment/sql/update_procedures_with_extraction.sql
    "ReadAttachmentByHash": frozenset({
        'entities/attachment/sql/dbo.attachment.sql',
        'entities/attachment/sql/update_procedures_with_extraction.sql',
    }),  # known-dup, home=entities/attachment/sql/dbo.attachment.sql, entities/attachment/sql/update_procedures_with_extraction.sql
    "ReadAttachmentById": frozenset({
        'entities/attachment/sql/dbo.attachment.sql',
        'entities/attachment/sql/update_procedures_with_extraction.sql',
    }),  # known-dup, home=entities/attachment/sql/dbo.attachment.sql, entities/attachment/sql/update_procedures_with_extraction.sql
    "ReadAttachmentByPublicId": frozenset({
        'entities/attachment/sql/dbo.attachment.sql',
        'entities/attachment/sql/update_procedures_with_extraction.sql',
    }),  # known-dup, home=entities/attachment/sql/dbo.attachment.sql, entities/attachment/sql/update_procedures_with_extraction.sql
    "ReadAttachments": frozenset({
        'entities/attachment/sql/dbo.attachment.sql',
        'entities/attachment/sql/update_procedures_with_extraction.sql',
    }),  # known-dup, home=entities/attachment/sql/dbo.attachment.sql, entities/attachment/sql/update_procedures_with_extraction.sql
    "ReadCompanies": frozenset({
        'entities/company/sql/dbo.company.sql',
        'entities/company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/company/sql/dbo.company.sql
    "ReadCompanyById": frozenset({
        'entities/company/sql/dbo.company.sql',
        'entities/company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/company/sql/dbo.company.sql
    "ReadCompanyByName": frozenset({
        'entities/company/sql/dbo.company.sql',
        'entities/company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/company/sql/dbo.company.sql
    "ReadCompanyByPublicId": frozenset({
        'entities/company/sql/dbo.company.sql',
        'entities/company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/company/sql/dbo.company.sql
    "ReadContractLaborByNaturalKey": frozenset({
        'entities/contract_labor/sql/ReadContractLaborByNaturalKey.sql',
        'entities/contract_labor/sql/dbo.contract_labor.sql',
    }),  # known-dup, home=entities/contract_labor/sql/ReadContractLaborByNaturalKey.sql, entities/contract_labor/sql/dbo.contract_labor.sql
    "ReadContractLaborByPublicId": frozenset({
        'entities/contract_labor/sql/dbo.contract_labor.sql',
        'entities/contract_labor/sql/migrations/2026_05_28_read_source_time_entry_public_id.sql',
    }),  # known-dup, home=entities/contract_labor/sql/dbo.contract_labor.sql
    "ReadContractLaborDailySummary": frozenset({
        'entities/contract_labor/sql/ReadContractLaborDailySummary.sql',
        'entities/contract_labor/sql/dbo.contract_labor.sql',
    }),  # known-dup, home=entities/contract_labor/sql/ReadContractLaborDailySummary.sql, entities/contract_labor/sql/dbo.contract_labor.sql
    "ReadContractLaborLineItemsByContractLaborId": frozenset({
        'entities/contract_labor/sql/dbo.contract_labor.sql',
        'entities/contract_labor/sql/migrations/2026_06_03_line_items_ordered_by_clockin.sql',
    }),  # known-dup, home=entities/contract_labor/sql/dbo.contract_labor.sql
    "ReadCurrentReviewByContractLaborId": frozenset({
        'entities/review/sql/migrations/005_review_sprocs_contract_labor.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadCurrentTimeEntryStatusesByTimeEntryIds": frozenset({
        'entities/time_entry/sql/migrations/013_2026_06_16_read_current_time_entry_statuses_by_ids.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadDistinctProjectIdsByTimeEntryIds": frozenset({
        'entities/time_entry/sql/migrations/010_2026_06_03_distinct_project_ids_by_time_entry_ids.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
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
    "ReadInboxTaskCounts": frozenset({
        'entities/review/sql/migrations/002_inbox_tasks.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadInboxTasks": frozenset({
        'entities/review/sql/migrations/002_inbox_tasks.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadInvoiceLineItemById": frozenset({
        'entities/invoice_line_item/sql/dbo.invoice_line_item.sql',
        'entities/invoice_line_item/sql/migrations/001_2026_05_27_employee_labor_source.sql',
    }),  # known-dup, home=entities/invoice_line_item/sql/dbo.invoice_line_item.sql
    "ReadInvoiceLineItemByPublicId": frozenset({
        'entities/invoice_line_item/sql/dbo.invoice_line_item.sql',
        'entities/invoice_line_item/sql/migrations/001_2026_05_27_employee_labor_source.sql',
    }),  # known-dup, home=entities/invoice_line_item/sql/dbo.invoice_line_item.sql
    "ReadInvoiceLineItems": frozenset({
        'entities/invoice_line_item/sql/dbo.invoice_line_item.sql',
        'entities/invoice_line_item/sql/migrations/001_2026_05_27_employee_labor_source.sql',
    }),  # known-dup, home=entities/invoice_line_item/sql/dbo.invoice_line_item.sql
    "ReadInvoiceLineItemsByInvoiceId": frozenset({
        'entities/invoice_line_item/sql/dbo.invoice_line_item.sql',
        'entities/invoice_line_item/sql/migrations/001_2026_05_27_employee_labor_source.sql',
    }),  # known-dup, home=entities/invoice_line_item/sql/dbo.invoice_line_item.sql
    "ReadInvoices": frozenset({
        'entities/invoice/sql/dbo.invoice.sql',
        'scripts/migrations/gap1_list_sprocs_scoped.sql',
    }),  # known-dup, home=entities/invoice/sql/dbo.invoice.sql
    "ReadInvoicesPaginated": frozenset({
        'entities/invoice/sql/dbo.invoice.sql',
        'scripts/migrations/gap1_list_sprocs_scoped.sql',
    }),  # known-dup, home=entities/invoice/sql/dbo.invoice.sql
    "ReadOrganizationById": frozenset({
        'entities/organization/sql/dbo.organization.sql',
        'entities/organization/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/organization/sql/dbo.organization.sql
    "ReadOrganizationByName": frozenset({
        'entities/organization/sql/dbo.organization.sql',
        'entities/organization/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/organization/sql/dbo.organization.sql
    "ReadOrganizationByPublicId": frozenset({
        'entities/organization/sql/dbo.organization.sql',
        'entities/organization/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/organization/sql/dbo.organization.sql
    "ReadOrganizations": frozenset({
        'entities/organization/sql/dbo.organization.sql',
        'entities/organization/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/organization/sql/dbo.organization.sql
    "ReadProjectsByUserId": frozenset({
        'entities/project/sql/dbo.project.sql',
        'entities/project/sql/migrations/003_read_projects_by_user_id_admin_bypass.sql',
    }),  # known-dup, home=entities/project/sql/dbo.project.sql
    "ReadQboVendorCreditLineByVendorCreditIdAndQboLineId": frozenset({
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
        'scripts/migrations/qbo_vendorcredit_upsert_inplace.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "ReadReviewsByContractLaborId": frozenset({
        'entities/review/sql/migrations/005_review_sprocs_contract_labor.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadTimeEntriesForDigestByWorkDate": frozenset({
        'entities/time_entry/sql/migrations/011_2026_06_16_time_entry_digest.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadTimeEntryBilledLineage": frozenset({
        'entities/time_entry/sql/migrations/004_2026_05_28_billed_lineage.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadTimeLogsByTimeEntryIds": frozenset({
        'entities/time_entry/sql/migrations/012_2026_06_16_read_time_logs_by_time_entry_ids.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadUserByFirstname": frozenset({
        'entities/user/sql/dbo.user.sql',
        'entities/user/sql/migrations/002_phase0_read_sprocs_extended.sql',
        'entities/user/sql/migrations/005_2026_05_27_worker_links.sql',
    }),  # known-dup, home=entities/user/sql/dbo.user.sql
    "ReadUserById": frozenset({
        'entities/user/sql/dbo.user.sql',
        'entities/user/sql/migrations/002_phase0_read_sprocs_extended.sql',
        'entities/user/sql/migrations/005_2026_05_27_worker_links.sql',
    }),  # known-dup, home=entities/user/sql/dbo.user.sql
    "ReadUserByLastname": frozenset({
        'entities/user/sql/dbo.user.sql',
        'entities/user/sql/migrations/002_phase0_read_sprocs_extended.sql',
        'entities/user/sql/migrations/005_2026_05_27_worker_links.sql',
    }),  # known-dup, home=entities/user/sql/dbo.user.sql
    "ReadUserByPublicId": frozenset({
        'entities/user/sql/dbo.user.sql',
        'entities/user/sql/migrations/002_phase0_read_sprocs_extended.sql',
        'entities/user/sql/migrations/005_2026_05_27_worker_links.sql',
    }),  # known-dup, home=entities/user/sql/dbo.user.sql
    "ReadUserCompanies": frozenset({
        'entities/user_company/sql/dbo.usercompany.sql',
        'entities/user_company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user_company/sql/dbo.usercompany.sql
    "ReadUserCompaniesByUserId": frozenset({
        'entities/user_company/sql/dbo.usercompany.sql',
        'entities/user_company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user_company/sql/dbo.usercompany.sql
    "ReadUserCompanyById": frozenset({
        'entities/user_company/sql/dbo.usercompany.sql',
        'entities/user_company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user_company/sql/dbo.usercompany.sql
    "ReadUserCompanyByPublicId": frozenset({
        'entities/user_company/sql/dbo.usercompany.sql',
        'entities/user_company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user_company/sql/dbo.usercompany.sql
    "ReadUserCompanyByUserId": frozenset({
        'entities/user_company/sql/dbo.usercompany.sql',
        'entities/user_company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user_company/sql/dbo.usercompany.sql
    "ReadUserModuleById": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "ReadUserModuleByModuleId": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "ReadUserModuleByPublicId": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "ReadUserModuleByUserId": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "ReadUserModules": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "ReadUserModulesByUserId": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "ReadUserModulesByUserIdAndCompanyId": frozenset({
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadUserProjectById": frozenset({
        'entities/user_project/sql/dbo.userproject.sql',
        'entities/user_project/sql/migrations/002_phase1_attribution_sprocs.sql',
        'entities/user_project/sql/migrations/004_role_qualifier_sprocs.sql',
    }),  # known-dup, home=entities/user_project/sql/dbo.userproject.sql
    "ReadUserProjectByProjectId": frozenset({
        'entities/user_project/sql/dbo.userproject.sql',
        'entities/user_project/sql/migrations/002_phase1_attribution_sprocs.sql',
        'entities/user_project/sql/migrations/004_role_qualifier_sprocs.sql',
    }),  # known-dup, home=entities/user_project/sql/dbo.userproject.sql
    "ReadUserProjectByPublicId": frozenset({
        'entities/user_project/sql/dbo.userproject.sql',
        'entities/user_project/sql/migrations/002_phase1_attribution_sprocs.sql',
        'entities/user_project/sql/migrations/004_role_qualifier_sprocs.sql',
    }),  # known-dup, home=entities/user_project/sql/dbo.userproject.sql
    "ReadUserProjectByUserId": frozenset({
        'entities/user_project/sql/dbo.userproject.sql',
        'entities/user_project/sql/migrations/002_phase1_attribution_sprocs.sql',
        'entities/user_project/sql/migrations/004_role_qualifier_sprocs.sql',
    }),  # known-dup, home=entities/user_project/sql/dbo.userproject.sql
    "ReadUserProjects": frozenset({
        'entities/user_project/sql/dbo.userproject.sql',
        'entities/user_project/sql/migrations/002_phase1_attribution_sprocs.sql',
        'entities/user_project/sql/migrations/004_role_qualifier_sprocs.sql',
    }),  # known-dup, home=entities/user_project/sql/dbo.userproject.sql
    "ReadUserRoleById": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "ReadUserRoleByPublicId": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "ReadUserRoleByRoleId": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "ReadUserRoleByUserId": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "ReadUserRoles": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "ReadUserRolesByUserId": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "ReadUserRolesByUserIdAndCompanyId": frozenset({
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "ReadUsers": frozenset({
        'entities/user/sql/dbo.user.sql',
        'entities/user/sql/migrations/002_phase0_read_sprocs_extended.sql',
        'entities/user/sql/migrations/004_phase4_include_agents_filter.sql',
        'entities/user/sql/migrations/005_2026_05_27_worker_links.sql',
    }),  # known-dup, home=entities/user/sql/dbo.user.sql
    "ReadVendorById": frozenset({
        'entities/vendor/sql/dbo.vendor.sql',
        'entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql',
        'entities/vendor/sql/migrations/003_2026_07_19_track_compliance.sql',
    }),  # known-dup, home=entities/vendor/sql/dbo.vendor.sql
    "ReadVendorByName": frozenset({
        'entities/vendor/sql/dbo.vendor.sql',
        'entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql',
        'entities/vendor/sql/migrations/003_2026_07_19_track_compliance.sql',
    }),  # known-dup, home=entities/vendor/sql/dbo.vendor.sql
    "ReadVendorByPublicId": frozenset({
        'entities/vendor/sql/dbo.vendor.sql',
        'entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql',
        'entities/vendor/sql/migrations/003_2026_07_19_track_compliance.sql',
    }),  # known-dup, home=entities/vendor/sql/dbo.vendor.sql
    "ReadVendorCreditBillCreditByBillCreditId": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql',
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql, integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "ReadVendorCreditBillCreditByQboVendorCreditId": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql',
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql, integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "ReadVendorCreditLineItemBillCreditLineItemByBillCreditLineItemId": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql',
        'scripts/migrations/qbo_vendorcredit_mapping_sprocs_dbo.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql
    "ReadVendorCreditLineItemBillCreditLineItemByQboLineId": frozenset({
        'integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql',
        'scripts/migrations/qbo_vendorcredit_mapping_sprocs_dbo.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql
    "ReadVendors": frozenset({
        'entities/vendor/sql/dbo.vendor.sql',
        'entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql',
        'entities/vendor/sql/migrations/003_2026_07_19_track_compliance.sql',
    }),  # known-dup, home=entities/vendor/sql/dbo.vendor.sql
    "ReadWorkers": frozenset({
        'entities/user/sql/migrations/2026_06_10_read_workers.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "RecoverStuckProcessingEmailMessages": frozenset({
        'entities/email_message/sql/dbo.email_message.sql',
        'entities/email_message/sql/migrations/001_recovery_processing_reset.sql',
        'entities/email_message/sql/migrations/007_recover_stuck_max_rows.sql',
    }),  # known-dup, home=entities/email_message/sql/dbo.email_message.sql
    "RevokeAllAuthRefreshTokensByAuthId": frozenset({
        'entities/auth/sql/migrations/001_gap3_revoke_all_refresh_tokens.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "SetUserLastCompanyId": frozenset({
        'entities/user/sql/migrations/002_phase0_read_sprocs_extended.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "StampTimeEntryReview": frozenset({
        'entities/time_entry/sql/migrations/006_stamp_review_sproc.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "TimeoutLongRunningAgentSessions": frozenset({
        'intelligence/persistence/sql/dbo.agent_session.sql',
        'intelligence/persistence/sql/migrations/001_timeout_long_running_sessions.sql',
        'intelligence/persistence/sql/migrations/002_timeout_max_rows.sql',
    }),  # known-dup, home=intelligence/persistence/sql/dbo.agent_session.sql
    "UpdateCompanyById": frozenset({
        'entities/company/sql/dbo.company.sql',
        'entities/company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/company/sql/dbo.company.sql
    "UpdateContractLaborAggregates": frozenset({
        'entities/contract_labor/sql/migrations/2026_06_09_update_contract_labor_aggregates.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "UpdateInvoiceLineItemById": frozenset({
        'entities/invoice_line_item/sql/dbo.invoice_line_item.sql',
        'entities/invoice_line_item/sql/migrations/001_2026_05_27_employee_labor_source.sql',
    }),  # known-dup, home=entities/invoice_line_item/sql/dbo.invoice_line_item.sql
    "UpdateOrganizationById": frozenset({
        'entities/organization/sql/dbo.organization.sql',
        'entities/organization/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/organization/sql/dbo.organization.sql
    "UpdateQboVendorCreditLineById": frozenset({
        'integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql',
        'scripts/migrations/qbo_vendorcredit_upsert_inplace.sql',
    }),  # known-dup, home=integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql
    "UpdateUserById": frozenset({
        'entities/user/sql/dbo.user.sql',
        'entities/user/sql/migrations/003_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user/sql/dbo.user.sql
    "UpdateUserCompanyById": frozenset({
        'entities/user_company/sql/dbo.usercompany.sql',
        'entities/user_company/sql/migrations/002_phase1_attribution_sprocs.sql',
    }),  # known-dup, home=entities/user_company/sql/dbo.usercompany.sql
    "UpdateUserModuleById": frozenset({
        'entities/user_module/sql/dbo.usermodule.sql',
        'entities/user_module/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_module/sql/dbo.usermodule.sql
    "UpdateUserProjectById": frozenset({
        'entities/user_project/sql/dbo.userproject.sql',
        'entities/user_project/sql/migrations/002_phase1_attribution_sprocs.sql',
        'entities/user_project/sql/migrations/004_role_qualifier_sprocs.sql',
    }),  # known-dup, home=entities/user_project/sql/dbo.userproject.sql
    "UpdateUserRoleById": frozenset({
        'entities/user_role/sql/dbo.userrole.sql',
        'entities/user_role/sql/migrations/002_phase1_company_scoped_sprocs.sql',
    }),  # known-dup, home=entities/user_role/sql/dbo.userrole.sql
    "UpdateUserWorkerLink": frozenset({
        'entities/user/sql/migrations/005_2026_05_27_worker_links.sql',
    }),  # HOME-LESS — must home (U-107 follow-up)
    "UpdateVendorById": frozenset({
        'entities/vendor/sql/dbo.vendor.sql',
        'entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql',
        'entities/vendor/sql/migrations/003_2026_07_19_track_compliance.sql',
    }),  # known-dup, home=entities/vendor/sql/dbo.vendor.sql
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
}
