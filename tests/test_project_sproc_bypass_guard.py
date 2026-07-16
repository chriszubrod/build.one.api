"""U-050 guard: Project RBAC read sprocs are fail-closed and single-sourced.

Scope is deliberately the 4 RBAC read sprocs, NOT every sproc in the project
base file: `ReadProjectsByUserId` is still legitimately duplicated in
migrations/003_read_projects_by_user_id_admin_bypass.sql (currently
byte-identical to the base, so not yet a drift). Project therefore cannot join
`ENTITY_BASE_FILES` in tests/test_sproc_single_source.py until that copy is
stubbed — see U-051.

The sproc-header sub-patterns below are deliberately COPIED from that sibling
guard rather than imported: cross-importing between test modules is not a
pattern this suite uses, and a copy of a two-line regex is cheaper than the
coupling. They must match its capability (bracketed `[dbo].[Name]` forms are
live house style — a weaker regex would silently miss a re-added duplicate).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PROJECT_BASE = REPO_ROOT / "entities" / "project" / "sql" / "dbo.project.sql"
PROJECT_SQL_DIR = PROJECT_BASE.parent

RBAC_READ_SPROCS = (
    "ReadProjects",
    "ReadProjectById",
    "ReadProjectByPublicId",
    "ReadProjectByName",
)

_SCHEMA = r"(?:\[\w+\]|\w+)\s*\.\s*"
_SPROC_NAME = r"(?:\[(\w+)\]|(\w+))"

_SPROC_HEADER = re.compile(
    rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:{_SCHEMA})?{_SPROC_NAME}",
    re.IGNORECASE,
)
_ACTOR_NULL_BYPASS = re.compile(r"@ActorUserId\s+IS\s+NULL", re.IGNORECASE)


def _strip_line_comments(text: str) -> str:
    """Drop `--` comments so prose mentioning the bypass isn't read as SQL.

    Load-bearing: the SUPERSEDED stub headers in migrations 001/002 quote the
    bypass by name, and the base file documents the fail-closed rule.
    """
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def _sproc_name(match: re.Match) -> str:
    return match.group(1) or match.group(2)


def _sproc_bodies(text: str) -> dict[str, str]:
    """Map sproc name -> its body, sliced from each header to the next."""
    headers = list(_SPROC_HEADER.finditer(text))
    return {
        _sproc_name(match): text[
            match.start() : headers[i + 1].start() if i + 1 < len(headers) else len(text)
        ]
        for i, match in enumerate(headers)
    }


def test_no_executable_actor_user_id_null_bypass_under_project_sql():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in PROJECT_SQL_DIR.rglob("*.sql")
        if _ACTOR_NULL_BYPASS.search(_strip_line_comments(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], (
        "executable @ActorUserId IS NULL bypass must not remain under "
        f"entities/project/sql/**; found in: {', '.join(offenders)}"
    )


def test_rbac_read_sprocs_defined_once_in_project_base_file():
    definitions: dict[str, list[Path]] = {}
    for path in REPO_ROOT.rglob("*.sql"):
        if ".venv" in path.parts:
            continue
        for match in _SPROC_HEADER.finditer(path.read_text(encoding="utf-8")):
            definitions.setdefault(_sproc_name(match), []).append(path)

    for name in RBAC_READ_SPROCS:
        found = definitions.get(name, [])
        assert found == [PROJECT_BASE], (
            f"{name} must be defined exactly once, in "
            f"{PROJECT_BASE.relative_to(REPO_ROOT)}; found in: "
            + (", ".join(str(p.relative_to(REPO_ROOT)) for p in found) or "nowhere")
        )


def test_rbac_read_sprocs_select_notes_column():
    bodies = _sproc_bodies(PROJECT_BASE.read_text(encoding="utf-8"))
    for name in RBAC_READ_SPROCS:
        body = bodies.get(name)
        assert body is not None, f"missing {name} in {PROJECT_BASE.relative_to(REPO_ROOT)}"
        assert re.search(r"\[Notes\]", body), (
            f"{name} must SELECT [Notes]; migration 002 omitted it and must not recur"
        )
