# AGENTS.md — BuildOne (MVP / Codex‑Ready, Simplified)

> **Goal:** minimal, unambiguous rules so Codex can generate one module end‑to‑end without extra chatter. Keep this short and opinionated for Phase 1.

---

## 1) Roles (MVP)

* **Architect (lightweight):** approves schema & endpoints; enforces tenancy and naming.
* **Backend Engineer:** FastAPI routes + service layer; Azure SQL via **pyodbc** (sync) for simplicity; Pydantic validation; JWT auth.
* **Frontend Engineer:** **Jinja2** pages with **Tailwind** + a little **vanilla JS** for form submits.

> If async is required later, we can switch to `aioodbc`, but MVP uses `pyodbc`.

---

## 2) Hard Standards (authoritative for MVP)

* **Runtime:** Python **3.11**
* **API:** FastAPI (`/api/<module>` base)
* **DB:** Azure SQL (T‑SQL) via **pyodbc**; parameterized queries or stored procedures. **No ORM**.
* **Views:** Jinja2 + Tailwind + vanilla JS
* **Auth:** JWT Bearer (`Authorization: Bearer <token>`)
* **Tenancy:** every query filtered by TenantId from JWT claim `tid`
* **Common Fields (all tables):** `Id (GUID, PK)`, `PublicId (GUID/ULID)`, `RowVersion (ROWVERSION)`, `CreatedDatetime (UTC)`, `ModifiedDatetime (UTC)`
* **Errors:** map to HTTP `400/401/403/404/409/422/500`
* **Logging:** include `x-corr-id` on request/response

---

## 3) Module Output Contract (what Codex must generate)

```
build.one/
  modules/
    <module>/
      <module>.spec.md
      persistence/
        repo.py                       # interfaces + DTOs (dataclasses)
        service.py                    # pyodbc calls into sprocs/queries
        db.py                         # connection factory, retries
      business/
        models.py                     # Pydantic models
        service.py                    # business rules + validation
      api/
        schemas.py                    # request/response models
        routes.py                     # FastAPI router for CRUD
      web/
        templates/<module>/create.html.j2
        templates/<module>/edit.html.j2
        templates/<module>/list.html.j2
        templates/<module>/view.html.j2
  sql/
    dbo.<module>.sql                  # DDL incl. common fields, indexes
    sprocs/
      sp<Module>_Upsert.sql
      sp<Module>_Delete.sql
      sp<Module>_Get.sql
      sp<Module>_List.sql
  README.md                           # how to run locally
```

**Endpoints (default CRUD):**

* `GET    /api/<module>` list/search (q, page, size)
* `GET    /api/<module>/{public_id}` get by public_id
* `POST   /api/<module>` create or update (upsert; includes RowVersion check)
* `DELETE /api/<module>/{public_id}` delete (delete by public_id)

---

## 4) Coding Rules (short)

* Use stored procedures for Upsert/Delete and complex List; simple Get can be inline parameterized SQL.
* Always pass `@TenantId` and scope queries to it.
* Use `ROWVERSION` for optimistic concurrency → return **409 Conflict** on mismatch.
* Timestamps via `SYSUTCDATETIME()`; APIs return ISO‑8601.
* Jinja templates escape by default; no inline JS events if avoidable.

---

## 5) Single Prompt (paste into Codex with the module spec)

```
Using AGENTS.md (MVP) and <module>.spec.md, generate the module under build.one/modules/<module>/.
Stack: Python 3.11 + FastAPI + Azure SQL via pyodbc (no ORM) + Jinja2 + Tailwind + vanilla JS.
Produce the exact folder/files in §3 with CRUD endpoints, sprocs, Pydantic models, tenancy scoping, RowVersion checks, error mapping.
Do not ask questions; use secure, production‑sane defaults and document them in README.md.
```
