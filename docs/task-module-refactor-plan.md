# Tasks Module Refactor Plan

## Goal

Transform the existing `services/tasks/` module from a thin workflow wrapper into a general-purpose, user-facing task/work-item hub that consolidates all user-initiated work — email workflows, data uploads, manual entry — into one place.

## Current State

- `services/tasks/` exists with model, repo, service, API, web controller, templates, and CSS
- The `dbo.Task` table has: id, public_id, row_version, timestamps, tenant_id, task_type, reference_id, title, status, source_type, source_id
- `TaskService` is hardcoded to workflows — imports `WorkflowRepository`, `WorkflowAdmin`, `BillIntakeExecutor`, `WorkflowScheduler`
- API router has 16+ endpoints, most workflow-specific (approve, reject, retry, inbox browse, etc.)
- Templates render email conversations, entity type confirmation, approval status
- CSS uses `.agent-*` prefix

## Design Principles

- Task is the primary user-facing entity; sources (email, upload, manual) are pluggable
- Task detail view has a generic header + type-specific pluggable sections
- Basic ownership (created_by) but no assignment workflow
- No priority or due dates yet
- Existing workflow functionality must not break
- The `agents/` workflow engine stays intact as backend infrastructure

---

## Phase 1: Data Model Expansion

**Modify:** `services/tasks/sql/dbo.task.sql`

Add columns to the existing `dbo.Task` table:

- `Description` NVARCHAR(MAX) NULL
- `CreatedByUserId` BIGINT NULL (who created the task)
- `WorkflowId` INT NULL (optional link to `agents.Workflow.Id`)
- `VendorId` BIGINT NULL (denormalized for querying)
- `ProjectId` BIGINT NULL (denormalized for querying)
- `BillId` BIGINT NULL (denormalized for querying)
- `Context` NVARCHAR(MAX) NULL (JSON blob for type-specific data)

Retain existing columns: `TaskType`, `ReferenceId`, `SourceType`, `SourceId`, `Title`, `Status`.

Update stored procedures: `CreateTask`, `UpdateTask`, `ReadTasks`, `ReadTaskByPublicId`, `ReadTaskById` to include new columns.

Add new stored procedure: `ReadTaskByWorkflowId` — lookup by workflow link.

Add index: `IX_Task_WorkflowId` on `(WorkflowId)`.

**Modify:** `services/tasks/business/model.py`

Add fields to the `Task` dataclass:

- `description: Optional[str]`
- `created_by_user_id: Optional[int]`
- `workflow_id: Optional[int]`
- `vendor_id: Optional[int]`
- `project_id: Optional[int]`
- `bill_id: Optional[int]`
- `context: Optional[dict]` (parsed from JSON in repo layer)

**Modify:** `services/tasks/persistence/repo.py`

- Update `_from_db()` to map new columns, parse `Context` JSON
- Update `create()` to accept new parameters
- Update `update()` to handle new fields
- Add `read_by_workflow_id()` method

---

## Phase 2: Service Layer Decoupling

**Modify:** `services/tasks/business/service.py`

This is the core refactor. The service currently imports and directly calls workflow internals. Replace with a clean separation.

### 2a. Generic Task CRUD

Add standard methods that don't depend on workflows:

- `create_task()` — generic task creation from any source
- `update_task()` — generic update
- `update_status()` — status-only update
- `get_tasks()` — list with filters (status, task_type, source_type, open_only)
- `get_task_detail()` — load task + resolve type-specific detail

### 2b. Task Type Resolver Pattern

Introduce a resolver that fetches type-specific detail based on `task_type`:

```python
def get_task_detail(self, public_id: str) -> Optional[dict]:
    task = self.repo.read_by_public_id(public_id)
    if not task:
        return None
    result = {"task": task.to_dict()}
    # Resolve type-specific detail
    if task.task_type == "workflow" and task.workflow_id:
        result["workflow"] = self._resolve_workflow_detail(task)
    elif task.task_type == "data_upload":
        result["upload"] = self._resolve_upload_detail(task)
    return result
```

### 2c. Workflow Bridge Methods (keep, but isolate)

Move existing workflow-specific logic into private methods:

- `_resolve_workflow_detail(task)` — fetches workflow + events (replaces current inline logic)
- `create_from_workflow(tenant_id, workflow)` — creates a Task from a Workflow
- `sync_status_from_workflow(workflow_id, workflow_state)` — maps workflow states to task statuses

### 2d. Remove Direct Workflow Imports from Top Level

The service should only import workflow types lazily within the bridge methods, not at module level. The email/inbox browsing logic should stay in the agents API — the task service does not need to fetch emails.

---

## Phase 3: API Router Restructure

**Modify:** `services/tasks/api/router.py`

### 3a. Generic Task Endpoints (keep/add)

- `GET /api/v1/tasks` — list tasks with filters (status, task_type, source_type, open_only)
- `GET /api/v1/tasks/{public_id}` — get task detail with resolved type-specific data
- `POST /api/v1/tasks` — create task (manual entry)
- `PUT /api/v1/tasks/{public_id}` — update task
- `PUT /api/v1/tasks/{public_id}/status` — quick status update
- `DELETE /api/v1/tasks/{public_id}` — delete/cancel task

### 3b. Workflow Action Endpoints (move under sub-path)

Keep existing workflow action endpoints but scope them clearly:

- `POST /api/v1/tasks/{public_id}/workflow/retry`
- `POST /api/v1/tasks/{public_id}/workflow/cancel`
- `POST /api/v1/tasks/{public_id}/workflow/approve`
- `POST /api/v1/tasks/{public_id}/workflow/reject`
- `POST /api/v1/tasks/{public_id}/workflow/confirm-type`
- `POST /api/v1/tasks/{public_id}/workflow/process-bill`
- `POST /api/v1/tasks/{public_id}/workflow/reminder`

These call through to `WorkflowAdmin`/`BillIntakeExecutor` via the task's `workflow_id`, maintaining existing functionality.

### 3c. Inbox/Poll Endpoints (keep under tasks)

Keep under `/api/v1/tasks/inbox/*` and `/api/v1/tasks/poll/*` since the Tasks module is the user-facing hub that owns email-sourced task creation.

### 3d. Upload Endpoint (new)

- `POST /api/v1/tasks/upload` — accepts file upload (CSV/XLSX), creates task with `source_type='upload'`

**Modify:** `services/tasks/api/schemas.py`

Add schemas:

- `TaskCreate` — title, description, task_type, source_type, context
- `TaskUpdate` — title, description, status, context
- `TaskStatusUpdate` — status only

---

## Phase 4: Web Controller + Templates

**Modify:** `services/tasks/web/controller.py`

Routes:

- `GET /tasks` / `GET /tasks/list` — task list with status/type filters
- `GET /tasks/browse` — email inbox browser (keep existing)
- `GET /tasks/{public_id}` — task detail with pluggable sections
- `GET /tasks/not-found` — 404 page

### 4a. Task List Template

**Modify:** `templates/task/list.html`

Update to show tasks from all sources, not just workflows:

- Add task_type filter pills (All, Email/Workflow, Upload, Manual)
- Show source_type icon alongside status
- Show created_by user name
- Keep existing status filter tabs (All, Open, In Progress, Completed)

### 4b. Task Detail Template — Pluggable Sections

**Modify:** `templates/task/view.html`

Restructure into:

1. **Generic header** — title, status badge, task_type label, created by, dates
2. **Generic metadata** — source_type, source_id
3. **Type-specific section** via Jinja2 conditional includes:

```jinja2
{% if task.task_type == 'workflow' and workflow %}
    {% include 'task/sections/workflow.html' %}
{% elif task.task_type == 'data_upload' %}
    {% include 'task/sections/upload.html' %}
{% else %}
    {% include 'task/sections/generic.html' %}
{% endif %}
```

**Create:** `templates/task/sections/workflow.html` — extract existing workflow-specific content from `view.html` (email conversation, entity type confirmation, approval status, timeline)

**Create:** `templates/task/sections/upload.html` — file info, parse status, column names, row count, preview table

**Create:** `templates/task/sections/generic.html` — description, context key-value display

### 4c. CSS Rename

**Modify:** `static/css/task.css` — rename `.agent-*` classes to `.task-*` throughout, update all template references accordingly.

---

## Phase 5: Workflow Bridge (Connect Existing Workflows to Tasks)

**Modify:** wherever `BillIntakeExecutor` lives (agents/executor.py or workflows/executor.py)

After workflow creation in `start_from_email()`, add a non-fatal call to create a Task:

```python
try:
    from services.tasks.business.service import TaskService
    TaskService().create_from_workflow(tenant_id=tenant_id, workflow=workflow)
except Exception:
    logger.warning("Failed to create task for workflow", exc_info=True)
```

After workflow state transitions, sync task status:

```python
try:
    from services.tasks.business.service import TaskService
    TaskService().sync_status_from_workflow(workflow.id, workflow.state)
except Exception:
    logger.warning("Failed to sync task status", exc_info=True)
```

**Create:** `scripts/backfill_tasks_from_workflows.py` — one-time script to create Task records for existing workflows that don't have linked tasks.

---

## Phase 6: Data Upload Source

**Modify:** `services/tasks/api/router.py` — add upload endpoint

**Create:** `services/tasks/business/upload_service.py`

```python
class UploadParserService:
    def parse_upload(self, task_public_id: str):
        # 1. Read task, get attachment info from context
        # 2. Download file from blob storage
        # 3. Parse with openpyxl (XLSX) or csv module
        # 4. Store summary in task.context: row_count, column_names, preview_rows, parse_status
        # 5. Update task status
```

Uses existing `shared/storage.py` for blob access and `modules/attachment/` for file records. Uses `openpyxl` (already in requirements.txt) for Excel parsing.

---

## Files Summary

### Files to Modify

| File | Changes |
|------|---------|
| `services/tasks/sql/dbo.task.sql` | Add columns, update stored procs |
| `services/tasks/business/model.py` | Add new fields to dataclass |
| `services/tasks/persistence/repo.py` | Map new columns, add `read_by_workflow_id()` |
| `services/tasks/business/service.py` | Decouple from workflows, add resolver pattern, add generic CRUD |
| `services/tasks/api/router.py` | Restructure endpoints, add upload endpoint |
| `services/tasks/api/schemas.py` | Add TaskCreate, TaskUpdate, TaskStatusUpdate |
| `services/tasks/web/controller.py` | Update detail route for pluggable sections |
| `templates/task/list.html` | Add multi-source support, type filters |
| `templates/task/view.html` | Restructure into generic header + pluggable sections |
| `static/css/task.css` | Rename `.agent-*` to `.task-*` |
| `app.py` | Ensure task routers registered (may already be done) |
| `agents/executor.py` | Add task creation hook after workflow creation |

### Files to Create

| File | Purpose |
|------|---------|
| `templates/task/sections/workflow.html` | Workflow-specific detail section |
| `templates/task/sections/upload.html` | Upload-specific detail section |
| `templates/task/sections/generic.html` | Generic/manual detail section |
| `services/tasks/business/upload_service.py` | CSV/Excel parsing service |
| `scripts/backfill_tasks_from_workflows.py` | Migration script for existing workflows |

---

## Verification

After implementation, verify:

1. `GET /tasks/list` renders tasks from all sources (or empty state if no tasks)
2. `POST /api/v1/tasks` creates a manual task, visible in list
3. `GET /tasks/{public_id}` shows generic header + correct type-specific section
4. Existing workflow creation (via poll or start) auto-creates a linked Task
5. Workflow state changes propagate to Task status
6. `POST /api/v1/tasks/upload` accepts a CSV file, creates task, parses in background
7. Upload task detail shows file info and parse results
8. All existing workflow action endpoints (approve, reject, retry) still function
9. Email browse page still works
10. No imports of workflow internals at the service module level (only inside bridge methods)
