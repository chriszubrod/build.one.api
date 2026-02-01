# Unified Workflow Architecture

## Overview

A unified workflow-based architecture for all operations in the application, from simple CRUD to complex multi-step processes with human-in-the-loop approval.

**Core Principle:** Every operation flows through the workflow engine, providing consistent state persistence, event logging, resumability, and audit trails.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Entry Points                              │
│     (Web, API, Scheduler, Webhook, CLI)                         │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Trigger Router                             │
│              (Normalize input, create workflow)                  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Workflow Engine                              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ • State persistence    • Event logging    • Resumability   ││
│  │ • State machine        • Audit trail      • Error handling ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│   ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐   │
│   │  Instant  │  │   Quick   │  │   Long    │  │  Human    │   │
│   │ (1 step)  │  │ (2-5 sec) │  │ (minutes) │  │ (days)    │   │
│   └───────────┘  └───────────┘  └───────────┘  └───────────┘   │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                          Agents                                 │
│        (Decision logic, orchestrate capability calls)           │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Capabilities                               │
│  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐   │
│  │  LLM  │ │Entity │ │ Email │ │  Doc  │ │ Sync  │ │Storage│   │
│  │       │ │ CRUD  │ │       │ │Extract│ │ (QBO) │ │       │   │
│  └───────┘ └───────┘ └───────┘ └───────┘ └───────┘ └───────┘   │
│            ▲                                                     │
│   ┌────────┴────────┐                                           │
│   │ Provider Router │                                           │
│   │ (Local/Cloud)   │                                           │
│   └─────────────────┘                                           │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Existing Services                             │
│         (Module services, external APIs, database)               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer Descriptions

### 1. Entry Points

All ways into the system:
- **Web** - Form submissions, button clicks, file drag-and-drop
- **API** - REST endpoints, programmatic access
- **Scheduler** - Polling (email inbox, timeouts)
- **Webhook** - External system callbacks
- **CLI/Job** - Scripts, batch operations

### 2. Trigger Router

Normalizes all inputs into a consistent format and routes to the workflow engine.

```python
@dataclass
class TriggerContext:
    # Source identification
    trigger_type: str          # "form_submit", "file_drop", "email_poll", etc.
    trigger_source: str        # "web", "api", "scheduler", "webhook"
    
    # Tenant/user context
    tenant_id: int
    user_id: Optional[int]     # None for system triggers
    access_token: Optional[str]
    
    # Payload
    payload: Dict[str, Any]    # Form fields, email data, etc.
    attachments: List[Attachment]
    
    # Execution mode
    expects_response: bool     # Sync (wait) vs async (fire-and-forget)
    workflow_type: Optional[str]
    
    # Correlation
    conversation_id: Optional[str]
    parent_workflow_id: Optional[str]
```

**Trigger Types:**

| Type | Source | Sync/Async | Example |
|------|--------|------------|---------|
| `form_submit` | Web | Sync | Bill create form |
| `file_drop` | Web | Sync | Drag PDF onto form |
| `button_click` | Web | Sync | "Extract" button |
| `email_poll` | Scheduler | Async | New email with attachment |
| `reply_check` | Scheduler | Async | Approval response |
| `timeout_check` | Scheduler | Async | Send reminder |
| `webhook` | External | Async | QBO callback |
| `api_call` | API | Either | Programmatic trigger |

### 3. Workflow Engine

All operations create a workflow record for consistent:
- **State persistence** - Track where we are
- **Event logging** - Audit trail of what happened
- **Resumability** - Retry on failure
- **State machine** - Define valid transitions

**Workflow Types by Duration:**

| Type | Duration | Example |
|------|----------|---------|
| Instant | < 1 sec | Simple CRUD |
| Quick | 2-5 sec | Extract + validate + create |
| Long | Minutes | Batch processing |
| Human | Days | Approval workflows |

### 4. Agents

Decision-making units that orchestrate multiple capability calls:

```python
class Agent(ABC):
    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        pass
```

**Key Insight:** An agent is a decision orchestrator, not just an LLM caller. It may:
- Call LLM for classification
- Call entity capability for CRUD
- Call document capability for extraction
- Decide what to do based on results

**Current Agents:**
- `EmailTriageAgent` - Classify emails, extract entities
- `BillExtractionAgent` - Extract bill fields from documents
- `ApprovalParserAgent` - Parse human approval replies
- `CorrelationAgent` - Match orphan emails to workflows

### 5. Capabilities

Wrappers around external services with consistent interface:

```python
class Capability(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass
```

**Current Capabilities:**
- `LlmCapability` - AI/LLM calls (classification, extraction, reasoning)
- `DocumentCapability` - Azure Document Intelligence
- `EmailCapability` - MS Graph email operations
- `EntityCapability` - CRUD for bills, vendors, projects
- `SyncCapability` - QuickBooks Online sync
- `SharePointCapability` - File uploads, worksheets
- `StorageCapability` - Azure Blob storage

### 6. LLM Provider Router

Selects between local and cloud LLM providers based on:
- Availability (is local running?)
- Task complexity (simple → local, complex → cloud)
- Cost optimization (free local vs paid cloud)

```
┌─────────────────────────────────────────────────────────────────┐
│                    LLM Capability                                │
│         ┌───────────────────────────────────┐                   │
│         │      Provider Router              │                   │
│         │  (task type + availability)       │                   │
│         └───────────────────────────────────┘                   │
│              │                    │                              │
│    ┌─────────┴─────────┐  ┌──────┴──────────┐                  │
│    │      Local        │  │      Cloud       │                  │
│    │ ┌──────┐ ┌──────┐ │  │ ┌──────┐ ┌─────┐│                  │
│    │ │Ollama│ │LM    │ │  │ │OpenAI│ │Azure││                  │
│    │ │      │ │Studio│ │  │ │      │ │     ││                  │
│    │ └──────┘ └──────┘ │  │ └──────┘ └─────┘│                  │
│    └───────────────────┘  └─────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘
```

**Model Selection by Task:**

| Task | Model Type | Provider Preference |
|------|------------|---------------------|
| Classification | Chat (fast) | Local if available |
| Field extraction | Chat | Local or cloud |
| Complex reasoning | Reasoning (o1/o3) | Cloud |
| Similarity search | Embedding | Local |

---

## Sync vs Async Execution

The distinction is **whether the caller waits**, not how many operations:

| Aspect | Sync | Async |
|--------|------|-------|
| Caller waits? | Yes | No |
| Operations | 1 or many | 1 or many |
| Response | Final result | Workflow ID |
| Use when | UI needs data now | Work takes time or needs human input |

**Both go through workflow engine** for consistent logging and state management.

---

## Why Workflow for Everything?

Even "simple" operations benefit from workflow features:

```
"Simple" form submit actually involves:
├── Validate input (can fail)
├── Check permissions (can fail)
├── Create entity (can fail)
├── Trigger side effects (sync to QBO, send email)
├── Log what happened (audit)
└── Handle partial failures (rollback? retry?)
```

**Benefits:**
- Consistent audit trail ("who changed what when")
- Debuggable ("why did this fail")
- Resumable ("can we retry this")
- Observable ("show me the history")
- Extensible (today's form → tomorrow's approval flow)

---

## Migration Path

### Phase 1: Trigger Router
- Create `TriggerContext` model
- Create `TriggerRouter` class
- Route new operations through router

### Phase 2: Instant Workflows
- Define lightweight workflow types for CRUD
- Wrap existing service calls in workflow steps
- Log events for all operations

### Phase 3: Capability Consolidation
- Wrap existing services as capabilities
- Standardize error handling and retry logic
- Add local LLM provider support

### Phase 4: Agent Expansion
- Create agents for common operations
- Add LLM-assisted decision making where valuable
- Reuse agents across sync and async paths

---

## Related Documents

- [Agentic Workflow Implementation Plan](./agentic-workflow-implementation-plan.md) - Original email-focused design
- `workflows/` directory - Current implementation
