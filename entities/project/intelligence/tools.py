"""Agent-facing tools for the Project entity.

Read tools (no approval):
  list_projects                    → GET  /api/v1/get/projects
  search_projects                  → GET  /api/v1/get/project/search?q=...
  read_project_by_public_id        → GET  /api/v1/get/project/{public_id}
  read_projects_by_customer_id     → GET  /api/v1/get/project/by-customer/{cid}

Write tools (user approval required):
  create_project                   → POST   /api/v1/create/project
  update_project                   → PUT    /api/v1/update/project/{public_id}
  delete_project                   → DELETE /api/v1/delete/project/{public_id}

Tools self-register on import.
"""
from typing import Optional
from urllib.parse import quote

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shapes ──────────────────────────────────────────────────────────

class _NoArgs(BaseModel):
    pass


class _PublicIdArgs(BaseModel):
    public_id: str = Field(description="The Project's public_id (UUID).")


class _SearchArgs(BaseModel):
    query: str = Field(
        description=(
            "Case-insensitive substring matched against name and "
            "abbreviation. Examples: `phase 2`, `acme`, `re-roof`."
        ),
    )
    limit: int = Field(default=10, description="Max matches (1-100).")


class _CustomerIdArgs(BaseModel):
    customer_id: int = Field(
        description=(
            "The parent Customer's internal id (BIGINT). Obtain from a "
            "Customer record's `id` field."
        ),
    )


# ─── Read tools ──────────────────────────────────────────────────────────

async def _list_projects(args: dict, ctx: ToolContext) -> ToolResult:
    return await ctx.call_api("GET", "/api/v1/get/projects")


list_projects = Tool(
    name="list_projects",
    description=(
        "List ALL projects (~130 rows). Cheap by virtue of small size, "
        "but `search_projects` is preferred for any name-based lookup."
    ),
    input_schema=input_schema_from(_NoArgs),
    handler=_list_projects,
)


async def _search_projects(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _SearchArgs(**args)
    return await ctx.call_api(
        "GET",
        f"/api/v1/get/project/search?q={quote(parsed.query)}&limit={parsed.limit}",
    )


search_projects = Tool(
    name="search_projects",
    description=(
        "Find projects by partial match against name or abbreviation. "
        "Default tool for any name-based lookup — prefer it over "
        "`list_projects` whenever the user gives a hint."
    ),
    input_schema=input_schema_from(_SearchArgs),
    handler=_search_projects,
)


async def _read_project_by_public_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/project/{parsed.public_id}"
    )


read_project_by_public_id = Tool(
    name="read_project_by_public_id",
    description=(
        "Fetch one project by its public_id (UUID). Use when you "
        "already have the public_id from an earlier tool result."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_read_project_by_public_id,
)


async def _read_projects_by_customer_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _CustomerIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/project/by-customer/{parsed.customer_id}"
    )


read_projects_by_customer_id = Tool(
    name="read_projects_by_customer_id",
    description=(
        "List all projects belonging to a Customer (BIGINT FK). Use "
        "when the user asks 'what projects does X have?' — you'll "
        "have the customer's id from a prior Customer read."
    ),
    input_schema=input_schema_from(_CustomerIdArgs),
    handler=_read_projects_by_customer_id,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateProjectArgs(BaseModel):
    name: str = Field(description="Project name (1-50 chars).")
    description: str = Field(
        description="Project description (1-500 chars). Required by the API.",
    )
    status: str = Field(
        description="Project status (1-50 chars). Required by the API.",
    )
    customer_public_id: Optional[str] = Field(
        default=None,
        description=(
            "UUID of the parent Customer. Pass null for projects with "
            "no parent. If the user names a customer, look it up first "
            "(via search_customers) to get its public_id."
        ),
    )
    abbreviation: Optional[str] = Field(
        default=None, description="Optional abbreviation (<=20 chars)."
    )


async def _create_project(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CreateProjectArgs(**args)
    return await ctx.call_api(
        "POST",
        "/api/v1/create/project",
        body=parsed.model_dump(exclude_none=False),
    )


def _summarize_create_project(args: dict) -> str:
    return f"Create project — {args.get('name') or '?'}"


create_project = Tool(
    name="create_project",
    description=(
        "Create a new project. REQUIRES USER APPROVAL. If the user "
        "names a parent customer, resolve it via `search_customers` "
        "first to get the customer's public_id."
    ),
    input_schema=input_schema_from(CreateProjectArgs),
    handler=_create_project,
    requires_approval=True,
    approval_summary=_summarize_create_project,
)


class UpdateProjectArgs(BaseModel):
    public_id: str = Field(description="UUID of the project to update.")
    row_version: str = Field(
        description=(
            "Base64 row version from your most recent read. Optimistic "
            "concurrency token — pass verbatim."
        ),
    )
    name: str = Field(description="The project's name.")
    description: str = Field(description="The project's description.")
    status: str = Field(description="The project's status.")
    customer_public_id: Optional[str] = Field(default=None)
    abbreviation: Optional[str] = Field(default=None)


async def _update_project(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateProjectArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT", f"/api/v1/update/project/{parsed.public_id}", body=body
    )


def _summarize_update_project(args: dict) -> str:
    return f"Update project — {args.get('name') or '?'}"


update_project = Tool(
    name="update_project",
    description=(
        "Modify an existing project. REQUIRES USER APPROVAL. Read "
        "the record first for all fields + `row_version`. The update "
        "endpoint takes the parent customer as `customer_public_id` "
        "(UUID), not `customer_id` (BIGINT). If you only have "
        "`customer_id` from the prior read, fetch the customer with "
        "`read_customer_by_id` first to get its public_id. Be "
        "explicit in prose about what's changing."
    ),
    input_schema=input_schema_from(UpdateProjectArgs),
    handler=_update_project,
    requires_approval=True,
    approval_summary=_summarize_update_project,
)


class DeleteProjectArgs(BaseModel):
    public_id: str = Field(description="UUID of the project to delete.")
    name: Optional[str] = Field(
        default=None, description="Project's name — display hint for the card."
    )


async def _delete_project(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteProjectArgs(**args)
    return await ctx.call_api(
        "DELETE", f"/api/v1/delete/project/{parsed.public_id}"
    )


def _summarize_delete_project(args: dict) -> str:
    name = args.get("name")
    public_id = args.get("public_id") or "?"
    return f"Delete project — {name}" if name else f"Delete project {public_id}"


delete_project = Tool(
    name="delete_project",
    description=(
        "Permanently delete a project. REQUIRES USER APPROVAL. Look "
        "up the record first and pass its `name` as a display hint."
    ),
    input_schema=input_schema_from(DeleteProjectArgs),
    handler=_delete_project,
    requires_approval=True,
    approval_summary=_summarize_delete_project,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    list_projects,
    search_projects,
    read_project_by_public_id,
    read_projects_by_customer_id,
    create_project,
    update_project,
    delete_project,
):
    register(_tool)
