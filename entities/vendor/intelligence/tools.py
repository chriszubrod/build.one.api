"""Agent-facing tools for the Vendor entity.

Read tools (no approval):
  search_vendors                  → GET  /api/v1/get/vendor/search?q=...
  find_vendor_for_invoice         → GET  /api/v1/get/vendor/find-for-invoice?vendor_name=...&sender_domain=...
  read_vendor_by_public_id        → GET  /api/v1/get/vendor/{public_id}

Write tools (user approval required):
  create_vendor                   → POST   /api/v1/create/vendor
  update_vendor                   → PUT    /api/v1/update/vendor/{public_id}
  delete_vendor                   → DELETE /api/v1/delete/vendor/{public_id}
                                    (server-side soft-delete: sets IsDeleted=true)

Intentionally NO `list_vendors` tool — the catalog is ~1100 rows and
listing all of it would dominate context. Search is the only read-many
path.

Tools self-register on import.
"""
from typing import Optional
from urllib.parse import quote

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shapes ──────────────────────────────────────────────────────────

class _PublicIdArgs(BaseModel):
    public_id: str = Field(description="The Vendor's public_id (UUID).")


class _SearchArgs(BaseModel):
    query: str = Field(
        description=(
            "Case-insensitive substring matched against name and "
            "abbreviation. Examples: `acme`, `home depot`, `1stdibs`. "
            "Prefix matches rank above substring matches; soft-deleted "
            "rows are excluded."
        ),
    )
    limit: int = Field(default=10, description="Max matches (1-100).")


# ─── Read tools ──────────────────────────────────────────────────────────

async def _search_vendors(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _SearchArgs(**args)
    return await ctx.call_api(
        "GET",
        f"/api/v1/get/vendor/search?q={quote(parsed.query)}&limit={parsed.limit}",
    )


search_vendors = Tool(
    name="search_vendors",
    description=(
        "Find vendors by partial match against name or abbreviation. "
        "Substring search; prefix matches rank first.\n\n"
        "For invoice classification (binding a DI-extracted vendor name "
        "to an existing Vendor), prefer `find_vendor_for_invoice` — it "
        "runs a multi-strategy ranked lookup in one call instead of "
        "retrying with progressively-shorter substrings."
    ),
    input_schema=input_schema_from(_SearchArgs),
    handler=_search_vendors,
)


class _FindVendorForInvoiceArgs(BaseModel):
    vendor_name: str = Field(
        description=(
            "Vendor name to look up — typically the DI-extracted vendor "
            "name from an invoice header (e.g. `\"WALKER LUMBER & SUPPLY\"`). "
            "Real-world invoice vendor names rarely match DB Vendor.Name "
            "exactly; this tool handles common variations (case, suffix "
            "drift, prefix match) automatically."
        ),
    )
    sender_domain: Optional[str] = Field(
        default=None,
        description=(
            "Optional sender email domain (e.g. `walkerlumber.com`) — "
            "raises confidence when a Vendor has a Contact row whose Email "
            "ends in `@<domain>`. Pass when you have it; the tool still "
            "works on vendor_name alone if you don't."
        ),
    )


async def _find_vendor_for_invoice(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _FindVendorForInvoiceArgs(**args)
    qs = f"vendor_name={quote(parsed.vendor_name)}"
    if parsed.sender_domain:
        qs += f"&sender_domain={quote(parsed.sender_domain)}"
    return await ctx.call_api("GET", f"/api/v1/get/vendor/find-for-invoice?{qs}")


find_vendor_for_invoice = Tool(
    name="find_vendor_for_invoice",
    description=(
        "Multi-strategy ranked vendor lookup for invoice classification. "
        "Returns up to 5 candidates with `strategy` + `confidence` labels. "
        "Use this — not `search_vendors` — when you're trying to bind a "
        "fresh invoice's vendor name to an existing Vendor row.\n\n"
        "Strategies (descending confidence):\n"
        "  • 1.00  domain_contact       — Vendor has a Contact whose Email ends in @<sender_domain>\n"
        "  • 0.95  exact_name           — case-insensitive Name match\n"
        "  • 0.90  exact_abbreviation   — case-insensitive Abbreviation match\n"
        "  • 0.85  prefix_name          — Name starts with first 2 words of vendor_name\n"
        "  • 0.75  substring_two_words  — Name contains first 2 words of vendor_name\n"
        "  • 0.65  substring_first_word — Name contains first word of vendor_name\n\n"
        "Pick the highest-confidence match (typically index 0). If two "
        "candidates have similar confidence and look like different "
        "vendors, surface the ambiguity to the human (don't guess)."
    ),
    input_schema=input_schema_from(_FindVendorForInvoiceArgs),
    handler=_find_vendor_for_invoice,
)


async def _read_vendor_by_public_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/vendor/{parsed.public_id}"
    )


read_vendor_by_public_id = Tool(
    name="read_vendor_by_public_id",
    description=(
        "Fetch one vendor by its public_id (UUID). Use when you "
        "already have the public_id from an earlier tool result."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_read_vendor_by_public_id,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateVendorArgs(BaseModel):
    name: str = Field(
        description="Vendor name (1-450 chars). Must not duplicate an existing vendor name.",
    )
    abbreviation: Optional[str] = Field(
        default=None, description="Optional abbreviation (<=255 chars)."
    )
    taxpayer_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional UUID of a Taxpayer record to link to this vendor."
        ),
    )
    vendor_type_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional UUID of a VendorType record (e.g. 'subcontractor', "
            "'supplier'). Only ~2 vendor types exist today; ask the user "
            "if you're unsure which to assign."
        ),
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description=(
            "Whether the vendor is a draft. Defaults to true for new "
            "creations — once the user has confirmed all details are "
            "correct, an update can flip this to false."
        ),
    )
    is_contract_labor: Optional[bool] = Field(
        default=False,
        description="Whether the vendor is eligible for contract-labor records.",
    )


async def _create_vendor(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CreateVendorArgs(**args)
    return await ctx.call_api(
        "POST",
        "/api/v1/create/vendor",
        body=parsed.model_dump(exclude_none=False),
    )


def _summarize_create_vendor(args: dict) -> str:
    return f"Create vendor — {args.get('name') or '?'}"


create_vendor = Tool(
    name="create_vendor",
    description=(
        "Create a new vendor. REQUIRES USER APPROVAL. Server enforces "
        "name uniqueness (case-sensitive) — if a vendor with that name "
        "already exists, the tool returns an error you must surface to "
        "the user. Defaults: is_draft=true, is_contract_labor=false."
    ),
    input_schema=input_schema_from(CreateVendorArgs),
    handler=_create_vendor,
    requires_approval=True,
    approval_summary=_summarize_create_vendor,
)


class UpdateVendorArgs(BaseModel):
    public_id: str = Field(description="UUID of the vendor to update.")
    row_version: Optional[str] = Field(
        default=None,
        description=(
            "Base64 row version from your most recent read. Optimistic "
            "concurrency token — pass verbatim. The API accepts null but "
            "you should pass it whenever you have one."
        ),
    )
    name: Optional[str] = Field(default=None)
    abbreviation: Optional[str] = Field(default=None)
    taxpayer_public_id: Optional[str] = Field(default=None)
    vendor_type_public_id: Optional[str] = Field(default=None)
    is_draft: Optional[bool] = Field(default=None)
    is_contract_labor: Optional[bool] = Field(default=None)


async def _update_vendor(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateVendorArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT", f"/api/v1/update/vendor/{parsed.public_id}", body=body
    )


def _summarize_update_vendor(args: dict) -> str:
    return f"Update vendor — {args.get('name') or '?'}"


update_vendor = Tool(
    name="update_vendor",
    description=(
        "Modify an existing vendor. REQUIRES USER APPROVAL. Read the "
        "record first for `row_version`. Only the fields you set in the "
        "tool args are sent — unset fields are ignored server-side, so "
        "you don't have to re-pass values that aren't changing. To "
        "clear an FK (taxpayer / vendor_type), pass an empty string. "
        "Be explicit in prose about what's changing."
    ),
    input_schema=input_schema_from(UpdateVendorArgs),
    handler=_update_vendor,
    requires_approval=True,
    approval_summary=_summarize_update_vendor,
)


class DeleteVendorArgs(BaseModel):
    public_id: str = Field(description="UUID of the vendor to delete.")
    name: Optional[str] = Field(
        default=None, description="Vendor's name — display hint for the card."
    )


async def _delete_vendor(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteVendorArgs(**args)
    return await ctx.call_api(
        "DELETE", f"/api/v1/delete/vendor/{parsed.public_id}"
    )


def _summarize_delete_vendor(args: dict) -> str:
    name = args.get("name")
    public_id = args.get("public_id") or "?"
    return f"Delete vendor — {name}" if name else f"Delete vendor {public_id}"


delete_vendor = Tool(
    name="delete_vendor",
    description=(
        "Soft-delete a vendor (sets IsDeleted=true server-side; row "
        "stays in the table for FK references on bills, expenses, "
        "etc.). REQUIRES USER APPROVAL. Look up the record first and "
        "pass its `name` as a display hint. Tell the user it's a soft "
        "delete — the vendor disappears from search but historical "
        "records pointing at it are preserved."
    ),
    input_schema=input_schema_from(DeleteVendorArgs),
    handler=_delete_vendor,
    requires_approval=True,
    approval_summary=_summarize_delete_vendor,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    search_vendors,
    find_vendor_for_invoice,
    read_vendor_by_public_id,
    create_vendor,
    update_vendor,
    delete_vendor,
):
    register(_tool)
