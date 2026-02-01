# Task Module Refactor — Claude Code Prompt

Copy and paste everything below the line into a new Claude Code session.

---

## Context

This is **Build.One**, a FastAPI construction management application with Azure SQL Server, Jinja2 templates, and an AI-powered workflow engine. The codebase lives at the repo root and follows a consistent module pattern:

```
services/{module_name}/
├── api/router.py, schemas.py
├── business/service.py, model.py
├── persistence/repo.py
├── sql/dbo.{table}.sql
└── web/controller.py
```

Other modules to reference for patterns: `services/vendor/`, `services/bill/`, `services/project/`.

The app also has an `agents/` (or `workflows/`) directory containing a workflow orchestration engine with state machines, AI agents, and email integration. This engine is backend infrastructure and should not be modified except for the specific hooks described below.

## Your Task

Refactor the `services/tasks/` module according to the plan in `docs/task-module-refactor-plan.md`. Read that file first — it contains the full phased implementation plan.

## Key Points

1. **The Tasks module already exists** at `services/tasks/` with a working model, repo, service, API, web controller, templates, and CSS. You are refactoring it, not building from scratch.

2. **The core problem:** The current `TaskService` is hardcoded to workflows. It imports `WorkflowRepository`, `WorkflowAdmin`, `BillIntakeExecutor`, and `WorkflowScheduler` directly. The API has 16+ workflow-specific endpoints. The goal is to make Tasks a generic, source-agnostic hub.

3. **Work in phases.** The plan has 6 phases. Complete each phase fully before moving to the next. After each phase, verify the changes don't break existing functionality.

4. **Follow existing patterns.** Before writing any code, read the reference modules (`services/vendor/`, `services/bill/`) to match their patterns for repo methods, service structure, router conventions, and template layout.

5. **Preserve workflow functionality.** All existing workflow actions (approve, reject, retry, confirm-type, process-bill, inbox browse, poll) must continue to work. They get reorganized under clearer URL paths but the underlying calls to `WorkflowAdmin` and `BillIntakeExecutor` stay the same.

6. **Lazy imports for workflow bridge.** The service layer should NOT import workflow modules at the top level. Import them inside the bridge methods (`_resolve_workflow_detail`, `create_from_workflow`, `sync_status_from_workflow`) so the Task module doesn't have a hard dependency on the agents module.

7. **CSS rename.** The current `static/css/task.css` uses `.agent-*` class prefixes. Rename these to `.task-*` and update all template references.

## Phase Order

1. Data model expansion (SQL + model + repo)
2. Service layer decoupling (generic CRUD + resolver pattern)
3. API router restructure (generic endpoints + workflow sub-path + upload)
4. Web controller + templates (pluggable sections)
5. Workflow bridge (hooks in executor + backfill script)
6. Data upload source (upload endpoint + parser service)

## Important Files to Read First

- `docs/task-module-refactor-plan.md` — the full plan
- `services/tasks/business/service.py` — current service (to understand what needs decoupling)
- `services/tasks/api/router.py` — current endpoints (to understand the restructure)
- `services/tasks/sql/dbo.task.sql` — current schema (to understand column additions)
- `services/tasks/business/model.py` — current model
- `services/tasks/persistence/repo.py` — current repo
- `services/vendor/persistence/repo.py` — reference pattern for repo layer
- `services/vendor/api/router.py` — reference pattern for API routes
- `templates/task/view.html` — current detail template (to extract into sections)
- `app.py` — to verify router registration
