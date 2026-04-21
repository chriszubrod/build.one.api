"""Quick DB inspector for a persisted agent session.

Usage:
    python scripts/inspect_agent_session.py <session_public_id>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intelligence.persistence.session_repo import (
    AgentSessionRepo,
    AgentToolCallRepo,
    AgentTurnRepo,
)


def main(public_id: str) -> int:
    session_repo = AgentSessionRepo()
    turn_repo = AgentTurnRepo()
    tool_repo = AgentToolCallRepo()

    session = session_repo.read_by_public_id(public_id)
    if session is None:
        print(f"No session found for public_id={public_id}")
        return 1

    print(f"━━━ Session {session.public_id} ━━━")
    print(f"  agent:    {session.agent_name}")
    print(f"  model:    {session.model} (provider={session.provider})")
    print(f"  status:   {session.status}")
    print(f"  reason:   {session.termination_reason}")
    print(f"  tokens:   in={session.total_input_tokens}  out={session.total_output_tokens}")
    print(f"  started:  {session.started_at}")
    print(f"  ended:    {session.completed_at}")
    print(f"  prompt:   {session.user_message!r}")

    turns = turn_repo.read_by_session_id(session.id)
    for t in turns:
        print(f"\n  ── turn {t.turn_number} ──")
        print(f"    model:         {t.model}")
        print(f"    tokens:        in={t.input_tokens}  out={t.output_tokens}")
        print(f"    stop_reason:   {t.stop_reason}")
        if t.assistant_text:
            print(f"    assistant:     {t.assistant_text[:200]}")

        tool_calls = tool_repo.read_by_turn_id(t.id)
        for tc in tool_calls:
            marker = "✗" if tc.is_error else "✓"
            print(
                f"    {marker} tool: {tc.tool_name}  "
                f"input={tc.tool_input}  "
                f"output={tc.tool_output[:100] if tc.tool_output else None}"
            )

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
