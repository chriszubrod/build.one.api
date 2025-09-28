"""Utilities for creating legacy module alias packages."""

from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Sequence


def ensure_package(fullname: str) -> ModuleType:
    """Ensure a namespace package exists in :mod:`sys.modules`."""
    module = sys.modules.get(fullname)
    if module is None:
        module = ModuleType(fullname)
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[fullname] = module
    return module


def alias_module(fullname: str, target: ModuleType) -> ModuleType:
    """Register *target* under *fullname* in :mod:`sys.modules`."""
    sys.modules[fullname] = target
    parent_name, _, attr = fullname.rpartition(".")
    if parent_name:
        parent = ensure_package(parent_name)
        setattr(parent, attr, target)
    return target


def import_alias(package: str, module: str, aliases: Sequence[Sequence[str]]) -> ModuleType:
    """Import a relative module and register it under additional aliases."""
    target = import_module(f".{module}", package)
    for alias in aliases:
        alias_module(".".join((package, *alias)), target)
    return target
