"""U-535b: map a Box project-folder name / filename prefix to a dbo.Project (pure, no I/O).

Project abbreviation = ``Project.Abbreviation`` when set, else the ``Name`` prefix before
`` - ``. A folder name loses a leading ``YYYY--N--`` prefix and its first token is matched
against the abbreviations: a unique match wins; several are broken by street number (first
3-5 digit token with an optional letter) in the folder name vs the project name; otherwise
the folder is unmapped. A filename's own project prefix (e.g. ``OHR2-GUEST``) resolves only
on an exact, unique abbreviation. The matcher takes the UNION: a bill matches when its
projects include either the prefix project or the folder project (sub-units share the parent
folder).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

_FOLDER_PREFIX = re.compile(r"^\s*\d{4}--\d+--\s*")
_STREET_NUMBER = re.compile(r"\b(\d{3,5}[A-Za-z]?)\b")


@dataclass(frozen=True)
class ProjectRef:
    id: int
    name: Optional[str]
    abbreviation: Optional[str] = None


def project_abbreviation(project: ProjectRef) -> str:
    abbr = (project.abbreviation or "").strip()
    if not abbr:
        abbr = (project.name or "").split(" - ", 1)[0].strip()
    return abbr.upper()


def street_number(text: Optional[str]) -> Optional[str]:
    m = _STREET_NUMBER.search(text or "")
    return m.group(1).upper() if m else None


def folder_token(folder_name: str) -> str:
    rest = _FOLDER_PREFIX.sub("", folder_name or "").strip()
    if not rest:
        return ""
    token = re.split(r"\s+", rest, maxsplit=1)[0]
    return token.strip(" -").upper()


class ProjectMap:
    def __init__(self, projects: Iterable[ProjectRef]):
        self._by_abbr: dict[str, list[ProjectRef]] = {}
        self._folder_cache: dict[str, Optional[int]] = {}
        for project in projects:
            abbr = project_abbreviation(project)
            if abbr:
                self._by_abbr.setdefault(abbr, []).append(project)

    def map_folder(self, folder_name: str) -> Optional[int]:
        if folder_name not in self._folder_cache:
            self._folder_cache[folder_name] = self._map_folder(folder_name)
        return self._folder_cache[folder_name]

    def _map_folder(self, folder_name: str) -> Optional[int]:
        token = folder_token(folder_name)
        if not token:
            return None
        matches = self._by_abbr.get(token, [])
        if len(matches) == 1:
            return matches[0].id
        if len(matches) > 1:
            folder_street = street_number(_FOLDER_PREFIX.sub("", folder_name or ""))
            if folder_street is None:
                return None
            by_street = [p for p in matches if street_number(p.name) == folder_street]
            if len(by_street) == 1:
                return by_street[0].id
        return None

    def resolve_prefix(self, prefix: Optional[str]) -> Optional[int]:
        key = (prefix or "").strip().upper()
        matches = self._by_abbr.get(key, []) if key else []
        return matches[0].id if len(matches) == 1 else None
