# Agentic Workflow Framework — Implementation Plan

## Overview

A hybrid agentic workflow framework for automating multi-step business processes, starting with bill intake via email. Agents use LLMs for reasoning and decision-making, with human approval before execution.

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Polling Scheduler                             │
│                    (Daily batch, on-demand triggers)                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Workflow Orchestrator                           │
│              (State machine, event routing, step execution)             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
     ┌────────────┐          ┌────────────┐          ┌────────────┐
     │   Email    │          │  Document  │          │  Approval  │
     │   Triage   │          │  Extract   │          │   Parser   │
     │   Agent    │          │   Agent    │          │   Agent    │
     └────────────┘          └────────────┘          └────────────┘
            │                       │                       │
            └───────────────────────┴───────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Capabilities Layer                              │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐ │
│  │   LLM   │ │Document │ │  Email  │ │ Entity  │ │  Sync   │ │ Share  │ │
│  │         │ │  Intel  │ │         │ │         │ │  (QBO)  │ │ Point  │ │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Existing Services                                │
│         (BillService, VendorService, ProjectService, MsMailService)     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

| Concept | Decision |
|---------|----------|
| Triggers | Polling-only (no webhooks), daily batch + on-demand |
| Event routing | Direct routing to workflow handlers |
| Email filtering | Attachments + known vendor domains, manual flag override |
| Workflow identity | Parent/child model (intake spawns per-bill children) |
| Correlation | Conversation ID primary, loose LLM matching for orphans |
| Context storage | SQL columns (queryable) + JSON blob (flexible) |
| Attachments | Copy to Azure Blob Storage, store URL references |
| Human interaction | Email-only, send as user (delegated), LLM parses replies |
| Failure handling | Retry transient errors, flag unreadable docs for manual input |
| Timeouts | 3-day reminder, 7-day second reminder, 30-day abandon |
| Observability | Daily summary email, WorkflowEvent audit table |
| Agent architecture | Two-layer (capabilities + agents), declarative workflow definitions |
| Tool calling | OpenAI function calling (not MCP initially) |
| Rollback strategy | Forward recovery, no rollback, idempotent steps |

---

## Directory Structure

```
agents/
├── __init__.py
├── orchestrator.py          # Workflow engine, state transitions
├── scheduler.py             # Polling scheduler, batch triggers
├── models.py                # Workflow, WorkflowEvent dataclasses
├── exceptions.py            # WorkflowError, StepFailedError
│
├── persistence/
│   ├── __init__.py
│   ├── repo.py              # WorkflowRepo, WorkflowEventRepo
│   └── sql/
│       ├── agents.Workflow.sql
│       └── agents.WorkflowEvent.sql
│
├── definitions/
│   ├── __init__.py
│   ├── base.py              # WorkflowDefinition, StateDefinition
│   └── bill_intake.py       # BillIntakeWorkflow definition
│
├── capabilities/
│   ├── __init__.py
│   ├── registry.py          # CapabilityRegistry
│   ├── llm.py               # LlmCapabilities (classify, parse_reply)
│   ├── document.py          # DocumentCapabilities (extract)
│   ├── email.py             # EmailCapabilities (send_as, get_thread)
│   ├── entity.py            # EntityCapabilities (create_bill, match_vendor)
│   ├── sync.py              # SyncCapabilities (push_to_qbo)
│   ├── sharepoint.py        # SharePointCapabilities (upload, worksheet)
│   └── storage.py           # StorageCapabilities (save_blob)
│
├── runners/
│   ├── __init__.py
│   ├── base.py              # Agent ABC, AgentResult
│   ├── email_triage.py      # EmailTriageAgent
│   ├── document_extract.py  # DocumentExtractAgent
│   ├── approval_parser.py   # ApprovalParserAgent
│   └── correlation.py       # CorrelationAgent (orphan matching)
│
├── prompts/
│   ├── __init__.py
│   ├── classification.py    # Email classification prompt
│   ├── extraction.py        # Data extraction prompt
│   ├── approval_parse.py    # Approval reply parsing prompt
│   └── correlation.py       # Orphan email matching prompt
│
└── notifications/
    ├── __init__.py
    ├── daily_summary.py     # Daily status email generator
    └── templates/
        ├── approval_request.html
        ├── reminder.html
        ├── daily_summary.html
        └── stuck_workflow.html
```

---

## Database Schema

### Schema Creation

```sql
CREATE SCHEMA agents;
```

### Workflow Table

```sql
CREATE TABLE agents.Workflow (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    TenantId INT NOT NULL,
    
    -- Type and state
    WorkflowType VARCHAR(50) NOT NULL,           -- 'bill_intake', 'payment_inquiry'
    State VARCHAR(50) NOT NULL,                   -- 'received', 'awaiting_approval', etc.
    
    -- Parent/child relationship
    ParentWorkflowId INT NULL REFERENCES agents.Workflow(Id),
    
    -- Correlation keys
    ConversationId NVARCHAR(200) NULL,            -- MS Graph conversation ID
    TriggerMessageId NVARCHAR(200) NULL,          -- Original email message ID
    
    -- Queryable entity references
    VendorId INT NULL,
    ProjectId INT NULL,
    BillId INT NULL,
    
    -- Flexible context
    Context NVARCHAR(MAX) NULL,                   -- JSON blob
    
    -- Timestamps
    CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CompletedAt DATETIME2 NULL,
    
    -- Concurrency
    RowVersion ROWVERSION,
    
    -- Indexes
    INDEX IX_Workflow_TenantState (TenantId, State),
    INDEX IX_Workflow_ConversationId (ConversationId),
    INDEX IX_Workflow_Parent (ParentWorkflowId)
);
```

### WorkflowEvent Table (Audit Trail)

```sql
CREATE TABLE agents.WorkflowEvent (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    WorkflowId INT NOT NULL REFERENCES agents.Workflow(Id),
    
    -- Event details
    EventType VARCHAR(50) NOT NULL,               -- 'state_changed', 'step_completed', 'error', 'human_response'
    FromState VARCHAR(50) NULL,
    ToState VARCHAR(50) NULL,
    StepName VARCHAR(100) NULL,
    
    -- Event data
    Data NVARCHAR(MAX) NULL,                      -- JSON: LLM response, error details, etc.
    
    -- Metadata
    CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CreatedBy VARCHAR(200) NULL,                  -- 'system', user email, 'agent:email_triage'
    
    INDEX IX_WorkflowEvent_Workflow (WorkflowId, CreatedAt)
);
```

---

## Phase 1: Foundation

**Goal**: Core workflow engine with state persistence.

### Tasks

1. **Database schema**
   - Create `agents` schema
   - Create `agents.Workflow` table and stored procedures (Create, Get, Update, List)
   - Create `agents.WorkflowEvent` table and stored procedures (Create, ListByWorkflow)
   - Run migrations

2. **Core models**
   - `agents/models.py`: Workflow, WorkflowEvent dataclasses
   - `agents/exceptions.py`: Custom exceptions

3. **Persistence layer**
   - `agents/persistence/repo.py`: WorkflowRepo, WorkflowEventRepo
   - Follow existing repository patterns (stored procedures, row mapping)

4. **Workflow definitions**
   - `agents/definitions/base.py`: WorkflowDefinition, StateDefinition, Transition
   - State machine logic using `transitions` library

5. **Orchestrator**
   - `agents/orchestrator.py`: WorkflowOrchestrator
   - Create workflow, transition state, execute steps, log events

### Acceptance Criteria

- [ ] Can create a workflow instance with initial state
- [ ] Can transition workflow between states
- [ ] All transitions logged to WorkflowEvent
- [ ] Can query workflows by state, tenant

---

## Phase 2: Capabilities Layer

**Goal**: Reusable capability modules that wrap external services.

### Tasks

1. **Capability registry**
   - `agents/capabilities/registry.py`: CapabilityRegistry
   - Dependency injection for testability

2. **LLM capabilities**
   - `agents/capabilities/llm.py`: LlmCapabilities
   - `classify(email_body, context) -> Classification`
   - `parse_reply(reply_body) -> ParsedReply`
   - Uses Azure OpenAI, logs full inputs/outputs

3. **Document capabilities**
   - `agents/capabilities/document.py`: DocumentCapabilities
   - `extract(blob_url) -> ExtractedData`
   - Wraps Azure Document Intelligence

4. **Email capabilities**
   - `agents/capabilities/email.py`: EmailCapabilities
   - `send_as(user_id, message) -> message_id`
   - `get_thread(conversation_id) -> List[Message]`
   - `get_new_messages(since, folder, filters) -> List[Message]`
   - Wraps existing MS Mail client

5. **Entity capabilities**
   - `agents/capabilities/entity.py`: EntityCapabilities
   - `create_bill(data) -> Bill`
   - `match_vendor(name, email) -> Vendor | List[Candidate]`
   - `match_project(name, keywords) -> Project | List[Candidate]`
   - Wraps existing services, uses `shared/ai/embeddings` for fuzzy matching

6. **SharePoint capabilities**
   - `agents/capabilities/sharepoint.py`: SharePointCapabilities
   - `upload_file(project_id, file_bytes, filename) -> file_url`
   - `append_worksheet_rows(project_id, rows) -> row_count`

7. **Storage capabilities**
   - `agents/capabilities/storage.py`: StorageCapabilities
   - `save_blob(file_bytes, path) -> blob_url`
   - Wraps existing Azure Blob client

8. **Sync capabilities**
   - `agents/capabilities/sync.py`: SyncCapabilities
   - `push_bill_to_qbo(bill_id) -> qbo_bill_id`
   - Wraps existing Intuit integration

### Acceptance Criteria

- [ ] Each capability is independently testable with mocks
- [ ] Capabilities log their inputs/outputs
- [ ] Retry logic for transient failures
- [ ] Registry provides all capabilities to agents

---

## Phase 3: Email Triage Agent

**Goal**: Agent that classifies incoming emails and extracts entities.

### Tasks

1. **Agent base class**
   - `agents/runners/base.py`: Agent ABC, AgentResult
   - Standard interface for all agents

2. **Classification prompt**
   - `agents/prompts/classification.py`
   - Input: email body, attachment text, known vendors/projects
   - Output: category, vendor guess, project guess, confidence, detected bills

3. **Email triage agent**
   - `agents/runners/email_triage.py`: EmailTriageAgent
   - Fetches email content
   - Extracts attachment via Document Intelligence (or flags for manual if fails)
   - Calls LLM for classification
   - Matches vendor/project against known entities
   - Returns structured triage result

4. **Correlation agent**
   - `agents/runners/correlation.py`: CorrelationAgent
   - For orphan emails (no conversation ID match)
   - Uses LLM to match against open workflows

### Acceptance Criteria

- [ ] Agent correctly classifies test emails (bill, payment inquiry, other)
- [ ] Agent extracts vendor and project with confidence scores
- [ ] Agent handles Document Intelligence failures gracefully
- [ ] Correlation agent matches orphan replies to existing workflows

---

## Phase 4: Bill Intake Workflow

**Goal**: Complete workflow from email receipt to entity creation.

### Tasks

1. **Workflow definition**
   - `agents/definitions/bill_intake.py`: BILL_INTAKE_WORKFLOW
   - States: received, classified, awaiting_approval, approved, creating_entities, syncing, completed, needs_review, abandoned
   - Transitions and agent/step mappings

2. **Approval request email**
   - `agents/notifications/templates/approval_request.html`
   - Includes: vendor, amount, project guess, instructions for reply
   - Send as user (delegated)

3. **Approval parser agent**
   - `agents/runners/approval_parser.py`: ApprovalParserAgent
   - `agents/prompts/approval_parse.py`
   - Parses: approved/rejected/question, project, cost code, notes

4. **Entity creation step**
   - Idempotent bill creation
   - Upload attachment to SharePoint
   - Append line items to worksheet
   - Handle partial failures (forward recovery)

5. **QBO sync step**
   - Push bill to QuickBooks
   - Handle validation errors (flag for manual)

6. **Parent/child workflow handling**
   - Intake workflow detects N bills → spawns N child workflows
   - Children track independently

### Acceptance Criteria

- [ ] End-to-end: email → triage → approval request → approval reply → bill created → synced
- [ ] Multi-bill emails spawn child workflows
- [ ] Approval reply correctly parsed (approved, rejected, question)
- [ ] Partial failures handled with forward recovery
- [ ] All steps logged to WorkflowEvent

---

## Phase 5: Polling & Scheduling

**Goal**: Automated email ingestion and workflow progression.

### Tasks

1. **Polling scheduler**
   - `agents/scheduler.py`: WorkflowScheduler
   - `poll_inbox()`: Fetch emails with attachments from known vendors since last poll
   - `check_replies()`: Fetch replies to approval requests, route to workflows
   - `process_timeouts()`: Find workflows awaiting response > 3 days, send reminder
   - `abandon_stale()`: Archive workflows > 30 days without response

2. **Trigger filters**
   - Filter: has attachment + sender in known vendor emails
   - Manual override: flag/category for explicit inclusion

3. **Script for manual/scheduled runs**
   - `scripts/run_agents_poll.py`
   - Can be run manually or via Azure Function / cron

4. **Idempotency**
   - Track processed message IDs to avoid duplicate workflows

### Acceptance Criteria

- [ ] Polling finds new qualifying emails
- [ ] Replies route to correct waiting workflows
- [ ] Reminders sent at 3 days
- [ ] Stale workflows archived at 30 days
- [ ] Duplicate emails do not create duplicate workflows

---

## Phase 6: Notifications & Observability

**Goal**: Daily summary and operational visibility.

### Tasks

1. **Daily summary email**
   - `agents/notifications/daily_summary.py`
   - Counts: active by state, completed today, stuck/needs attention
   - List stuck workflows with context

2. **Reminder emails**
   - `agents/notifications/templates/reminder.html`
   - Sent at 3-day and 7-day marks

3. **Stuck workflow notification**
   - Include: what succeeded, what failed, suggested action

4. **Admin query helpers**
   - Stored procedures for common queries
   - Workflows by state, by vendor, by date range

### Acceptance Criteria

- [ ] Daily summary email generated and sent
- [ ] Reminders sent on schedule
- [ ] Stuck workflows clearly identified with actionable context

---

## Testing Strategy

### Unit Tests
- Each capability in isolation (mocked external services)
- Agent logic with mocked capabilities
- Workflow state transitions

### Integration Tests
- End-to-end workflow with test email data
- Document Intelligence with sample invoices
- LLM classification with known inputs/outputs

### Manual Testing
- Real emails in test inbox
- Approval flow via email reply
- Verify bill created in DB, file in SharePoint, row in worksheet

---

## Implementation Order

| Phase | Estimated Effort | Dependencies |
|-------|------------------|--------------|
| Phase 1: Foundation | 3-4 hours | None |
| Phase 2: Capabilities | 4-6 hours | Phase 1 |
| Phase 3: Email Triage Agent | 2-3 hours | Phase 2 |
| Phase 4: Bill Intake Workflow | 4-6 hours | Phase 3 |
| Phase 5: Polling & Scheduling | 2-3 hours | Phase 4 |
| Phase 6: Notifications | 1-2 hours | Phase 5 |

**Total: ~16-24 hours of focused work**

---

## Open Questions / Future Considerations

1. **Multi-tenant isolation** — How are workflows scoped per tenant?
2. **Approval delegation** — Can approvers forward to someone else?
3. **Bulk operations** — Process multiple bills from one email in batch?
4. **Learning from feedback** — Track approval corrections to improve LLM prompts?
5. **Other workflow types** — Payment inquiry, document filing, QBO reconciliation
