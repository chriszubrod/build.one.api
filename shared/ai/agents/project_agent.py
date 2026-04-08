"""
Project Agent
=============
Reasoning agent for project disambiguation. Uses Claude Haiku with
tools to look up vendor billing history, search projects, and resolve
ambiguous project matches.

Called by the extraction pipeline when the fuzzy matcher finds multiple
candidate projects with similar scores.
"""
import json
import logging
import re
from typing import Optional

import anthropic
from anthropic import beta_tool

from config import Settings

logger = logging.getLogger(__name__)
settings = Settings()

PROJECT_AGENT_SYSTEM = """\
You are a project resolution agent for a construction company. Your job is to \
determine which project a vendor invoice belongs to.

You have been given extracted invoice data (vendor, amount, address, etc.) and \
a list of candidate projects that partially match. Use the tools available to:

1. Look up this vendor's billing history — which projects have they billed to before?
2. Get details on each candidate project — is it active? What's the address?
3. Reason about which project is the best match.

Rules:
- If one project clearly matches (vendor history + address + active status), return it.
- If multiple projects are equally plausible, return "ambiguous" — do not guess.
- If no projects match well, return "no_match".
- Always explain your reasoning.

Respond with JSON only:
{
  "decision": "match" | "ambiguous" | "no_match",
  "project_public_id": "uuid or null",
  "project_name": "name or null",
  "confidence": 0.0 to 1.0,
  "reasoning": "explanation of your decision"
}"""


# ── Tools ────────────────────────────────────────────────────────────

@beta_tool
def lookup_vendor_billing_history(vendor_id: int) -> str:
    """Look up which projects a vendor has billed to recently.
    Returns a summary of the vendor's recent bills and which projects
    they were assigned to, ordered by most recent first.

    Args:
        vendor_id: The internal vendor ID to look up billing history for.
    """
    # @beta_tool passes args as a dict to the first parameter
    if isinstance(vendor_id, dict):
        vendor_id = vendor_id["vendor_id"]

    from shared.database import get_connection

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT TOP 20
                    b.BillNumber,
                    CONVERT(VARCHAR(10), b.BillDate, 120) AS BillDate,
                    b.TotalAmount,
                    p.Name AS ProjectName,
                    p.PublicId AS ProjectPublicId,
                    p.Status AS ProjectStatus
                FROM dbo.Bill b
                LEFT JOIN dbo.BillLineItem bli ON bli.BillId = b.Id
                LEFT JOIN dbo.Project p ON p.Id = bli.ProjectId
                WHERE b.VendorId = ?
                ORDER BY b.BillDate DESC
                """,
                vendor_id,
            )
            rows = cursor.fetchall()

        if not rows:
            return "No billing history found for this vendor."

        lines = []
        for r in rows:
            project = r.ProjectName or "No project assigned"
            status = f" ({r.ProjectStatus})" if r.ProjectStatus else ""
            lines.append(
                f"Bill #{r.BillNumber} | {r.BillDate} | "
                f"${float(r.TotalAmount):,.2f} | Project: {project}{status}"
            )
        return "\n".join(lines)

    except Exception as exc:
        return f"Error looking up vendor billing history: {exc}"


@beta_tool
def search_projects(query: str) -> str:
    """Search for projects by name, abbreviation, or address.
    Returns matching projects with their status (active/closed).

    Args:
        query: Search term to match against project names, abbreviations, or addresses.
    """
    # @beta_tool passes args as a dict to the first parameter
    if isinstance(query, dict):
        query = query["query"]

    from entities.project.business.service import ProjectService
    from entities.bill.business.extraction_mapper import BillExtractionMapper

    try:
        all_projects = ProjectService().read_all()
        mapper = BillExtractionMapper()

        scored = []
        for p in all_projects:
            _, score = mapper._fuzzy_match(query, [(p.public_id, p.name)])
            if score >= 0.2:
                scored.append((p, score))

        scored.sort(key=lambda x: -x[1])
        top = scored[:10]

        if not top:
            return f"No projects matching '{query}'."

        lines = []
        for p, score in top:
            abbr = f" ({p.abbreviation})" if p.abbreviation else ""
            status = p.status or "unknown"
            lines.append(
                f"{p.name}{abbr} | Status: {status} | "
                f"Match: {score:.0%} | ID: {p.public_id}"
            )
        return "\n".join(lines)

    except Exception as exc:
        return f"Error searching projects: {exc}"


@beta_tool
def get_project_details(public_id: str) -> str:
    """Get full details for a specific project including status and description.

    Args:
        public_id: The project's public ID (UUID).
    """
    # @beta_tool passes args as a dict to the first parameter
    if isinstance(public_id, dict):
        public_id = public_id["public_id"]

    from entities.project.business.service import ProjectService

    try:
        project = ProjectService().read_by_public_id(public_id)
        if not project:
            return f"Project {public_id} not found."

        parts = [
            f"Name: {project.name}",
            f"Abbreviation: {project.abbreviation or 'none'}",
            f"Status: {project.status or 'unknown'}",
            f"Description: {project.description or 'none'}",
            f"Public ID: {project.public_id}",
        ]
        return "\n".join(parts)

    except Exception as exc:
        return f"Error getting project details: {exc}"


# ── Agent entry point ────────────────────────────────────────────────

def resolve_project(
    vendor_name: str,
    vendor_id: Optional[int],
    project_hint: str,
    ship_to_address: Optional[str] = None,
    email_subject: Optional[str] = None,
    candidates: Optional[list[dict]] = None,
) -> dict:
    """
    Run the ProjectAgent to disambiguate a project match.

    Args:
        vendor_name: Extracted vendor name.
        vendor_id: Internal vendor ID (if resolved).
        project_hint: Claude's initial project name guess.
        ship_to_address: Ship-to address from the invoice.
        email_subject: Original email subject line.
        candidates: Pre-scored candidate projects from fuzzy match.

    Returns:
        {
            "decision": "match" | "ambiguous" | "no_match",
            "project_public_id": str | None,
            "project_name": str | None,
            "confidence": float,
            "reasoning": str,
        }
    """
    # Build context for the agent
    parts = [
        f"Vendor: {vendor_name}",
        f"Vendor ID: {vendor_id or 'not resolved'}",
        f"Project hint from invoice: {project_hint}",
    ]
    if ship_to_address:
        parts.append(f"Ship-to address: {ship_to_address}")
    if email_subject:
        parts.append(f"Email subject: {email_subject}")

    if candidates:
        parts.append("\nCandidate projects from fuzzy match:")
        for c in candidates:
            parts.append(f"  - {c['name']} (score: {c['score']:.0%}, id: {c['public_id']})")

    user_message = "\n".join(parts)

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        runner = client.beta.messages.tool_runner(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=PROJECT_AGENT_SYSTEM,
            tools=[lookup_vendor_billing_history, search_projects, get_project_details],
            messages=[{"role": "user", "content": user_message}],
        )

        # Run the agentic loop — tool runner handles tool calls automatically
        final_message = None
        for message in runner:
            final_message = message

        if not final_message:
            logger.warning("ProjectAgent returned no messages")
            return _fallback_result("Agent returned no response")

        # Extract text from final message
        raw_text = ""
        for block in final_message.content:
            if block.type == "text":
                raw_text = block.text.strip()
                break

        if not raw_text:
            return _fallback_result("Agent returned no text response")

        # Extract JSON from response — may be wrapped in markdown or explanation text
        parsed = _extract_json(raw_text)

        logger.info(
            "ProjectAgent decision=%s project=%s confidence=%.0f%% reasoning=%s",
            parsed.get("decision"),
            parsed.get("project_name"),
            parsed.get("confidence", 0) * 100,
            parsed.get("reasoning", "")[:100],
        )

        return {
            "decision": parsed.get("decision", "no_match"),
            "project_public_id": parsed.get("project_public_id"),
            "project_name": parsed.get("project_name"),
            "confidence": float(parsed.get("confidence", 0)),
            "reasoning": parsed.get("reasoning", ""),
        }

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("ProjectAgent parse error: %s", exc)
        return _fallback_result(f"Parse error: {exc}")

    except Exception as exc:
        logger.warning("ProjectAgent failed: %s", exc)
        return _fallback_result(f"Agent error: {exc}")


def _extract_json(text: str) -> dict:
    """Extract a JSON object from text that may contain markdown or prose."""
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Look for JSON inside code fences
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Look for first { ... } block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No valid JSON found in response", text, 0)


def _fallback_result(reason: str) -> dict:
    """Return an ambiguous result when the agent fails."""
    return {
        "decision": "ambiguous",
        "project_public_id": None,
        "project_name": None,
        "confidence": 0.0,
        "reasoning": f"Fallback — {reason}",
    }
