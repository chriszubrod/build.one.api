# Python Standard Library Imports
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Union


@dataclass
class Transition:
    """
    Represents a state transition in a workflow.
    """
    trigger: str  # Name of the trigger/event
    source: Union[str, List[str]]  # Source state(s)
    dest: str  # Destination state
    conditions: list[str] = field(default_factory=list)  # Condition function names
    before: list[str] = field(default_factory=list)  # Callbacks to run before transition
    after: list[str] = field(default_factory=list)  # Callbacks to run after transition


@dataclass
class StateDefinition:
    """
    Represents a state in a workflow with optional entry/exit callbacks.
    """
    name: str
    on_enter: Optional[str] = None  # Callback when entering state
    on_exit: Optional[str] = None  # Callback when exiting state
    timeout_days: Optional[int] = None  # Auto-timeout after N days
    timeout_trigger: Optional[str] = None  # Trigger to fire on timeout
    is_final: bool = False  # If True, workflow is considered complete


@dataclass
class StepDefinition:
    """
    Represents a step to execute within a state.
    """
    name: str
    capability: Optional[str] = None  # Capability to invoke (e.g., 'entity.create_bill')
    required: bool = True  # If True, failure blocks workflow
    retry_count: int = 3  # Number of retries on failure


@dataclass
class WorkflowDefinition:
    """
    Defines a workflow type with states, transitions, and steps.
    
    This is a declarative definition that the orchestrator uses
    to drive workflow execution.
    """
    name: str
    initial_state: str
    states: list[StateDefinition]
    transitions: list[Transition]
    steps: dict[str, list[StepDefinition]] = field(default_factory=dict)  # State -> steps to execute
    
    def get_state(self, name: str) -> Optional[StateDefinition]:
        """Get a state definition by name."""
        for state in self.states:
            if state.name == name:
                return state
        return None
    
    def get_steps_for_state(self, state_name: str) -> list[StepDefinition]:
        """Get steps to execute for a given state."""
        return self.steps.get(state_name, [])
    
    def get_final_states(self) -> list[str]:
        """Get all final state names."""
        return [s.name for s in self.states if s.is_final]
    
    def get_timeout_states(self) -> list[StateDefinition]:
        """Get all states with timeout configured."""
        return [s for s in self.states if s.timeout_days is not None]
    
    def to_transitions_config(self) -> dict:
        """
        Convert to configuration dict for the `transitions` library.
        """
        return {
            "states": [s.name for s in self.states],
            "initial": self.initial_state,
            "transitions": [
                {
                    "trigger": t.trigger,
                    "source": t.source,
                    "dest": t.dest,
                    "conditions": t.conditions if t.conditions else None,
                    "before": t.before if t.before else None,
                    "after": t.after if t.after else None,
                }
                for t in self.transitions
            ],
        }


# Common states used across workflows
COMMON_STATES = {
    "completed": StateDefinition(name="completed", is_final=True),
    "abandoned": StateDefinition(name="abandoned", is_final=True),
    "cancelled": StateDefinition(name="cancelled", is_final=True),
    "needs_review": StateDefinition(name="needs_review"),
}
