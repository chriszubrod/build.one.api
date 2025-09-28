"""Shared integration adapter mappings and naming utilities."""

from __future__ import annotations

import re
from typing import Type, TypeVar

T = TypeVar('T')

_ADAPTER_NAME_PATTERN = re.compile(r'^Map[A-Z][A-Za-z0-9]*To[A-Z][A-Za-z0-9]*$')


def register_adapter(cls: Type[T]) -> Type[T]:
    """Validate adapter naming and return the class.

    Adapter classes must follow the ``Map<Source>To<Target>`` naming pattern so
    that the same persistence layer can be shared across integration providers.
    The decorator raises ``ValueError`` if the class name does not conform.
    """

    if not _ADAPTER_NAME_PATTERN.match(cls.__name__):
        raise ValueError(
            f"Adapter class '{cls.__name__}' must match pattern Map<Source>To<Target>."
        )
    return cls


__all__ = [
    'register_adapter',
]
