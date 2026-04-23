"""Tool definitions for the intelligence layer.

A Tool is pure data: name, description, input_schema (JSON Schema), and a
handler coroutine that takes parsed args + a ToolContext and returns a
ToolResult. State belongs in ToolContext (session-scoped) or module globals
(app-scoped), not on the Tool itself.

ToolContext carries the agent's identity (its own user in the system, with
its own auth token) and two helpers that every DB-backed tool will otherwise
re-implement:

  call_api(method, path, body=None)  → hit an internal API endpoint, get a
                                       ToolResult back with the JSON body
                                       or an is_error=True for 4xx/5xx.

  call_sync(fn, *args, **kwargs)     → run a sync function (e.g. a pyodbc
                                       service call) in a worker thread.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Union

from pydantic import BaseModel

from intelligence.messages.types import OutputBlock
from intelligence.transport.internal_api import call_internal_api


class ToolResult(BaseModel):
    """The business-level output of a single tool invocation.

    `content` is either a plain string (the common case — JSON body or any
    text) or a list of OutputBlocks when a tool returns vision-relevant
    output (rendered chart, PDF preview, etc.). The loop translates this
    into a canonical ToolResult content block when appending the invocation
    to message history.
    """
    content: Union[str, list[OutputBlock]]
    is_error: bool = False


@dataclass(frozen=True)
class ToolContext:
    """Session-scoped context passed to every tool invocation.

    Each agent has its own user record and credentials; `auth_token` holds
    the agent's bearer JWT, obtained by logging in at run start. This means
    every tool call routes through RBAC under the agent's identity, not
    the human initiator's.

    `requesting_user_id` records the human who asked the agent to run, for
    attribution purposes. RBAC does not use it.
    """
    agent_id: Optional[str] = None          # the agent user's public_id (sub)
    auth_token: Optional[str] = None        # agent's bearer JWT
    session_id: Optional[str] = None
    requesting_user_id: Optional[str] = None  # human who initiated the run

    async def call_api(
        self,
        method: str,
        path: str,
        body: Any = None,
    ) -> "ToolResult":
        """Call an internal API endpoint. Returns a ToolResult.

        4xx/5xx responses become ToolResult(is_error=True) with the HTTP
        status and body text. 200/201/202 return the body as-is — FastAPI
        already serialized Decimal/datetime/UUID etc. at the HTTP boundary.
        """
        status, text = await call_internal_api(
            method, path, auth_token=self.auth_token, body=body
        )
        if status == 404:
            return ToolResult(content="Not found", is_error=True)
        if status >= 400:
            return ToolResult(
                content=f"HTTP {status}: {text[:2000]}",
                is_error=True,
            )
        return ToolResult(content=text)

    @staticmethod
    async def call_sync(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a sync function (e.g. a pyodbc service call) in a worker thread.

        Use sparingly — prefer `call_api` so tool actions go through RBAC
        and audit logging. `call_sync` is the escape hatch for cases where
        calling a service directly is genuinely the right choice.
        """
        return await asyncio.to_thread(fn, *args, **kwargs)


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


ApprovalSummary = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    # When True, the loop runner pauses BEFORE executing this tool and
    # emits an ApprovalRequest LoopEvent. Execution only proceeds after
    # the user POSTs a decision via /runs/{id}/approve.
    requires_approval: bool = False
    # Optional callable that turns the proposed input into a one-line
    # human-readable summary for the approval card. If omitted, the card
    # falls back to a generic "run <tool_name>" label.
    approval_summary: Optional[ApprovalSummary] = None

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Render for the Anthropic /v1/messages `tools` field."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def describe_for_approval(self, proposed_input: dict[str, Any]) -> str:
        """Human-readable summary for the approval card."""
        if self.approval_summary is not None:
            try:
                return self.approval_summary(proposed_input)
            except Exception:
                # A buggy summary function shouldn't block the flow.
                pass
        return f"Run {self.name}"
