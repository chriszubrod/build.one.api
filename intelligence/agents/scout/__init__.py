"""Scout agent package.

Importing this package triggers registration of scout and its tools:

  1. Entity tool modules self-register their tools when imported.
  2. scout's definition self-registers the agent.

Callers that want scout available should `import intelligence.agents.scout`
(or rely on run_agent() which imports it via the registry lookup path).
"""
# Import entity tool modules so their tools land in the tool registry.
# Add imports here as scout's scope expands to additional entities.
import entities.sub_cost_code.intelligence.tools  # noqa: F401
import entities.cost_code.intelligence.tools  # noqa: F401

# Register the scout agent.
from intelligence.agents.scout.definition import scout  # noqa: F401
