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
| Bill | `entities/bill/` | Invoice management with BillAgent automation, inline email display, QBO sync |
| Bill Line Item | `entities/bill_line_item/` | Bill line items with SubCostCode mapping and QBO Item sync |
| Inbox | `entities/inbox/` | Email inbox integration (MS Graph) with bill/expense extraction |
| Company | `entities/company/` | Company/organization management |
| Cost Code | `entities/cost_code/` | Cost code hierarchy |
| Sub Cost Code | `entities/sub_cost_code/` | Sub cost codes with alias matching support |
| Contract Labor | `entities/contract_labor/` | Contract labor tracking |
| Project | `entities/project/` | Project management |
| Role | `entities/role/` | RBAC roles for user authorization |
| User | `entities/user/` | User management with inline role assignment |
| User Role | `entities/user_role/` | User-to-role assignment (join table) |
| Role Module | `entities/role_module/` | Role-to-module access mapping (join table) |
| Contact | `entities/contact/` | Contact details (email, phone, fax) — inline on User, Company, Customer, Project, Vendor |
| Vendor | `entities/vendor/` | Vendor management |

## Integrations

| Integration | Path | Description |
|-------------|------|-------------|
| Intuit QBO | `integrations/intuit/qbo/` | QuickBooks Online sync (bills, items, accounts, vendors) |
| Microsoft SharePoint | `integrations/ms/sharepoint/` | File storage and drive item management |
| Azure Document Intelligence | -- | OCR for PDF invoice processing |

## AI Agents

| Agent | Path | Description |
|-------|------|-------------|
| BillAgent | `core/ai/agents/bill_agent/` | Automated PDF invoice processing from SharePoint |
| CopilotAgent | `core/ai/agents/copilot_agent/` | Conversational agent for inbox processing and bill creation |
| ExtractionAgent | `core/ai/agents/extraction_agent/` | LangGraph agent for invoice data extraction |
| ExpenseAgent | `core/ai/agents/expense_agent/` | Expense processing automation |

## Running

```bash
# Run the application
uvicorn app:app --reload

# Run SQL migrations
python scripts/run_sql.py path/to/file.sql
```
