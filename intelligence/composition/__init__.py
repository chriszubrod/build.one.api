"""Agent composition — primitives for one agent invoking another.

Today: `delegate_to_agent` tool factory. A parent agent that calls
`delegate_to_X` spawns a sub-session targeting agent X, forwards the
sub's live events into the parent's channel for UI display, awaits
the sub-session's completion, and returns the sub's final assistant
text as the tool result.
"""
