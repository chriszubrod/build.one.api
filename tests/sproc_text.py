"""Text helpers for asserting on stored-procedure definitions in .sql files.

Not a test module — imported by the sproc contract tests, alongside
`sproc_drift_ledger.py`. These exist because the v1 harness is pure-logic /
no-live-DB, so sproc contracts are pinned against file content.

The load-bearing one is `sproc_params`. Asserting a param name appears
somewhere in a sproc BODY is not enough: the regression shape these tests
guard is a sproc that still *references* `@CreatedByUserId` in its VALUES
clause while no longer *declaring* it as a parameter. A body-substring check
passes happily against exactly that bug — confirmed by mutation testing on
2026-09-08, where dropping the declaration left the body assertion green.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def sproc_body(sql_path: Path, name: str) -> str:
    """Return the text of `name`'s definition, from CREATE up to the next GO.

    Raises AssertionError if the sproc is not defined in that file at all, so a
    pin can never pass vacuously against a renamed or deleted sproc.
    """
    text = sql_path.read_text()
    match = re.search(
        rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?{name}\b(.*?)^GO\s*$",
        text,
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )
    assert match is not None, f"{name} is not defined in {sql_path.name}"
    return match.group(1)


def sproc_params(sql_path: Path, name: str) -> str:
    """Return ONLY the declared parameter list, between the name and `AS`.

    See the module docstring for why this is not interchangeable with
    `sproc_body`.
    """
    body = sproc_body(sql_path, name)
    match = re.search(r"\((.*?)\)\s*AS\b", body, re.DOTALL)
    assert match is not None, f"{name} has no parameter list in {sql_path.name}"
    return match.group(1)


def split_top_level(items: str) -> list[str]:
    """Split a SQL list on commas that are not nested inside parentheses.

    `COALESCE(@CreatedByUserId, 17)` is ONE value expression; a naive
    `.split(",")` counts it as two and makes an arity check lie.
    """
    parts, depth, current = [], 0, ""
    for ch in items:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def defines_sproc(sql_path: Path, name: str) -> bool:
    """True if the file carries a CREATE OR ALTER body for `name`."""
    return bool(
        re.search(
            rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?{name}\b",
            sql_path.read_text(),
            re.IGNORECASE,
        )
    )
