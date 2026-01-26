# Agent capabilities

from agents.capabilities.base import Capability, CapabilityResult
from agents.capabilities.registry import CapabilityRegistry, get_capability_registry

__all__ = [
    "Capability",
    "CapabilityResult",
    "CapabilityRegistry",
    "get_capability_registry",
]
