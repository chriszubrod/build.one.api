"""Verify Anthropic prompt-caching markers on a registered agent's request body.

Pure-offline regression check: resolves the agent's tools, builds a
representative `/v1/messages` body via `to_anthropic_request`, and asserts
that `cache_control: {type: ephemeral}` is attached to:
  - The single `system` text block.
  - The LAST entry of the `tools` array (caches the entire tools block).
  - No other tool entry.

Caching is wired at the canonical-message-conversion layer
(intelligence/messages/convert.py), shipped 2026-04-22 in commit e4848e7.
This script exists so any future refactor that breaks the markers fails
loudly here instead of silently regressing prod cost.

Run:
    .venv/bin/python scripts/dry_run_cache_markers.py              # email_specialist
    .venv/bin/python scripts/dry_run_cache_markers.py scout
    .venv/bin/python scripts/dry_run_cache_markers.py bill_specialist

Exits 0 on PASS, 1 on FAIL. No DB, no network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intelligence.messages.convert import to_anthropic_request
from intelligence.messages.types import Message, Text
from intelligence.registry import agents as agent_registry
from intelligence.tools.registry import get as get_tool


def _import_agent_modules() -> None:
    """Force registration by importing every agent definition module.

    Each agent's __init__.py registers itself into the agent + tool
    registries on import; we don't try to be selective here since the
    list is short and the imports are cheap.
    """
    import intelligence.agents.scout                  # noqa: F401
    import intelligence.agents.sub_cost_code_specialist  # noqa: F401
    import intelligence.agents.cost_code_specialist   # noqa: F401
    import intelligence.agents.customer_specialist    # noqa: F401
    import intelligence.agents.project_specialist     # noqa: F401
    import intelligence.agents.vendor_specialist      # noqa: F401
    import intelligence.agents.bill_specialist        # noqa: F401
    import intelligence.agents.bill_credit_specialist  # noqa: F401
    import intelligence.agents.expense_specialist     # noqa: F401
    import intelligence.agents.invoice_specialist     # noqa: F401
    import intelligence.agents.email_specialist       # noqa: F401


def verify(agent_name: str) -> int:
    _import_agent_modules()
    agent = agent_registry.get(agent_name)
    if agent is None:
        print(f"FAIL: agent {agent_name!r} not registered")
        return 1

    tool_schemas = []
    for name in agent.tools:
        t = get_tool(name)
        if t is None:
            print(f"FAIL: tool {name!r} declared by {agent_name} not registered")
            return 1
        tool_schemas.append(t.to_anthropic_schema())

    messages = [Message(role="user", content=[Text(text="dry-run probe")])]
    body = to_anthropic_request(
        messages,
        model=agent.model,
        system=agent.system_prompt,
        tools=tool_schemas,
    )

    failures: list[str] = []

    # System: must be a list of one text block with ephemeral cache_control.
    sys_blocks = body.get("system")
    if not isinstance(sys_blocks, list) or len(sys_blocks) != 1:
        failures.append(
            f"system: expected list[1], got {type(sys_blocks).__name__} "
            f"len={len(sys_blocks) if isinstance(sys_blocks, list) else 'n/a'}"
        )
    else:
        block = sys_blocks[0]
        if block.get("type") != "text":
            failures.append(f"system[0].type: expected 'text', got {block.get('type')!r}")
        if block.get("cache_control") != {"type": "ephemeral"}:
            failures.append(
                f"system[0].cache_control: expected ephemeral, got {block.get('cache_control')!r}"
            )

    # Tools: last entry has cache_control; nothing earlier does.
    tools = body.get("tools") or []
    if not tools:
        failures.append("tools: expected non-empty list")
    else:
        last = tools[-1]
        if last.get("cache_control") != {"type": "ephemeral"}:
            failures.append(
                f"tools[-1] {last.get('name')}: expected ephemeral cache_control, "
                f"got {last.get('cache_control')!r}"
            )
        for i, t in enumerate(tools[:-1]):
            if t.get("cache_control") is not None:
                failures.append(
                    f"tools[{i}] {t.get('name')}: unexpected cache_control "
                    f"{t.get('cache_control')!r} (only the last tool should be cached)"
                )

    print(f"=== Cache-marker verification: {agent_name} ===")
    print(f"  model       : {agent.model}")
    print(f"  provider    : {agent.provider}")
    print(f"  system size : {len(agent.system_prompt)} chars")
    print(f"  tool count  : {len(tools)}")
    if tools:
        cached_names = [t["name"] for t in tools if t.get("cache_control")]
        print(f"  cached tools: {cached_names}")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "email_specialist"
    sys.exit(verify(name))
