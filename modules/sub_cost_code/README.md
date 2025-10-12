# Sub Cost Code Module

This module implements CRUD operations for managing **Sub Cost Codes**, which are child entries scoped to a parent Cost Code. The upstream specification was unavailable in the repository, so the following secure defaults were assumed:

- Every sub cost code belongs to a single parent identified by the parent's `PublicId`.
- `Number` must be unique per parent cost code and is limited to 50 characters.
- `Name` is required (255 characters max) and `Description` is optional.
- Optimistic concurrency is enforced via SQL `ROWVERSION`; conflicts propagate as HTTP 409 responses.
- Hard deletes are used until retention requirements are provided.

The rest of the stack mirrors the existing Organization/Cost Code modules: FastAPI for both API and HTML routes, `pyodbc` stored procedure access, Bootstrap-based templates, and dataclasses that expose helper methods for `RowVersion` handling. Update the stored procedures if the upstream schema introduces additional fields or soft-delete requirements.
