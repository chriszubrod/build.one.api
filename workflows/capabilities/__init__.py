# Agent capabilities

from workflows.capabilities.base import Capability, CapabilityResult
from workflows.capabilities.registry import CapabilityRegistry, get_capability_registry

__all__ = [
    "Capability",
    "CapabilityResult",
    "CapabilityRegistry",
    "get_capability_registry",
]
