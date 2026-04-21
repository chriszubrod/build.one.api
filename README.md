# build.one
Repository for the build.one application.

## Architecture

- **Backend**: Python / FastAPI
- **Database**: SQL Server (stored procedures, pyodbc)
- **Templates**: Jinja2
- **Frontend**: HTML/CSS/JavaScript

## Entity Modules

| Module | Path | Description |
|--------|------|-------------|
| Bill | `entities/bill/` | Invoice management, inline email display, QBO sync |
| Bill Line Item | `entities/bill_line_item/` | Bill line items with SubCostCode mapping and QBO Item sync |
| Expense | `entities/expense/` | Expense management, SharePoint upload, Excel sync, QBO sync |
| Expense Line Item | `entities/expense_line_item/` | Expense line items with SubCostCode mapping and attachment support |
| Expense Line Item Attachment | `entities/expense_line_item_attachment/` | 1-1 attachment link for expense line items (Azure Blob + Attachment record) |
| Company | `entities/company/` | Company/organization management |
| Cost Code | `entities/cost_code/` | Cost code hierarchy |
| Sub Cost Code | `entities/sub_cost_code/` | Sub cost codes with alias matching support |
| Contract Labor | `entities/contract_labor/` | Subcontractor time tracking — Excel import, status workflow (pending_review→ready→billed), bill generation grouped by vendor+project, PDF generation with billable/non-billable line items |
| Project | `entities/project/` | Project management |
| Role | `entities/role/` | RBAC roles for user authorization |
| User | `entities/user/` | User management with inline role assignment |
| User Role | `entities/user_role/` | User-to-role assignment (join table) |
| Role Module | `entities/role_module/` | Role-to-module access mapping (join table) |
| Contact | `entities/contact/` | Contact details (email, phone, fax) — inline on User, Company, Customer, Project, Vendor |
| Vendor | `entities/vendor/` | Vendor management |
| Invoice | `entities/invoice/` | Client invoices — draft/complete lifecycle, billable item loading, PDF packet generation |
| Invoice Line Item | `entities/invoice_line_item/` | Polymorphic line items linking to BillLineItem, ExpenseLineItem, or BillCreditLineItem |
| Invoice Attachment | `entities/invoice_attachment/` | Invoice-level attachment links (PDF packets stored here) |
| Invoice Line Item Attachment | `entities/invoice_line_item_attachment/` | Per-line-item attachment links for packet source PDFs |

## Integrations

| Integration | Path | Description |
|-------------|------|-------------|
| Intuit QBO | `integrations/intuit/qbo/` | QuickBooks Online sync (bills, items, accounts, vendors) |
| Microsoft SharePoint | `integrations/ms/sharepoint/` | File storage and drive item management |

## AI / Inbox

The AI layer (Anthropic, Azure OpenAI, Document Intelligence, AI Search,
embeddings, and the project-resolution agent) was removed, and the inbox
surface (`entities/inbox`, `entities/email_thread`,
`entities/classification_override`, `entities/review_entry`, the email-intake
workflow, and all related templates) has now been removed as well. Bills and
expenses are created manually through the web UI / API; there is no automated
email-driven intake. `integrations/ms/mail` is left in place for future use.

## Running

```bash
# Run the application
uvicorn app:app --reload

# Run SQL migrations
python scripts/run_sql.py path/to/file.sql
```
