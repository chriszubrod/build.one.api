"""U-196 guard: type-driven detector for money-coercion defects in entity API routers.

Banned shapes:
- FLOAT_ON_DECIMAL — a ``float(...)`` call with a ``Decimal``-annotated request field anywhere in
  its argument, so the laundered ``float(Decimal(str(body.X)))`` is caught too.
- TRUTHY_ON_DECIMAL — a bare truthy test on ``body.X`` when X is ``Decimal``-annotated, in any of
  the falsy-sensitive positions: an ``if`` / ternary test, a ``bool`` operand (``body.X or 0``,
  ``body.X and y``), or a ``not body.X``. ``Decimal(0)`` is falsy, so every one of these silently
  drops a genuine $0.00 / 0% markup.

Because the check is TYPE-DRIVEN, breadth here is free: a non-money truthy idiom such as
``file.content_type or "application/octet-stream"`` is never a candidate — ``content_type`` is not a
``Decimal``-annotated request field. All three positions were verified to add zero findings against
the current repo, so the guard is as wide as the defect class rather than as wide as the fixed sites.

Scope: ``entities/*/api/router.py`` only. The same falsy-Decimal defect exists in the QBO
connector/persistence layer (see TODO.md, spawned unit) and is NOT covered here.

What this detector CANNOT see:
- Request models imported under aliases then referenced as ``alias.ModelName`` param types.
- ``from module import *`` re-exports (star imports are skipped).
- Decimal fields declared only via ``model_fields`` / runtime Pydantic config, not ``AnnAssign``.
- Coercion inside helpers imported from other modules (only ``entities/*/api/router.py`` is scanned).
- A nested ``def`` that closes over an enclosing function's ``body`` param (each function is analysed
  against its own parameters only).

The allowlist only shrinks. A new entry needs written justification in its commit message.
"""

from __future__ import annotations

import ast
import functools
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_GLOB = "entities/*/api/router.py"

# (repo-relative path, Decimal-annotated field name) exempt from the guard. Only shrink.
# ``confidence`` is ``Optional[Decimal]`` inline in email_message router but is an agent
# classification score in [0, 1], NOT a financial field; AgentInquiryService.send_inquiry
# takes a float, so float() there is the correct boundary cast.
MONEY_COERCION_ALLOWLIST = frozenset({
    ("entities/email_message/api/router.py", "confidence"),
})

_SCHEMA_ROOTS = frozenset({"entities", "shared", "core"})


@dataclass(frozen=True)
class MoneyCoercionViolation:
    relpath: str
    lineno: int
    param_field: str
    rule: str


def decimal_fields(class_node: ast.ClassDef) -> set[str]:
    """Names of AnnAssign targets whose annotation source mentions ``Decimal``."""
    return {
        node.target.id
        for node in class_node.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and "Decimal" in ast.unparse(node.annotation)
    }


def _module_path_from_dotted(module: str) -> Path | None:
    parts = module.split(".")
    if not parts or parts[0] not in _SCHEMA_ROOTS:
        return None
    return REPO_ROOT.joinpath(*parts).with_suffix(".py")


def _resolve_import_module(router_path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    # Relative import: resolve from the router file's package directory.
    base_parts = list(router_path.parent.parts)
    repo_parts = REPO_ROOT.parts
    if len(base_parts) < len(repo_parts):
        return None
    pkg_parts = base_parts[len(repo_parts) :]
    if node.level > len(pkg_parts):
        return None
    pkg_parts = pkg_parts[: len(pkg_parts) - node.level + 1]
    if node.module:
        pkg_parts.extend(node.module.split("."))
    return ".".join(pkg_parts)


@functools.lru_cache(maxsize=None)
def _class_decimal_map_from_file(path: Path) -> dict[str, set[str]]:
    """Cached: one schema module is imported by many routers (responses.py by all 68)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return {}
    return {
        n.name: decimal_fields(n)
        for n in tree.body
        if isinstance(n, ast.ClassDef)
    }


def build_schema_map(router_path: Path, tree: ast.Module) -> dict[str, set[str]]:
    """Map schema class name -> Decimal field names from inline classes and repo imports."""
    schema_map: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            schema_map[node.name] = decimal_fields(node)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_module(router_path, node)
            if module is None:
                continue
            schema_path = _module_path_from_dotted(module)
            if schema_path is None or not schema_path.is_file():
                continue
            imported = _class_decimal_map_from_file(schema_path)
            for alias in node.names:
                if alias.name == "*":
                    continue
                class_name = alias.asname or alias.name
                if alias.name in imported:
                    schema_map[class_name] = imported[alias.name]
    return schema_map


def repo_origin_class_names(router_path: Path, tree: ast.Module) -> set[str]:
    """Names that are supposed to BE one of our request models.

    A class defined inline in the router, or a name imported from an ``entities`` / ``shared`` /
    ``core`` module. Anything else — ``UploadFile`` from fastapi, ``dict``, ``str`` — is not one of
    our schemas, so it is never a fail-closed candidate. Without this filter the UNRESOLVED_SCHEMA
    rule fires on every ``file: UploadFile`` param that truthy-tests ``file.filename``.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_module(router_path, node)
            if module is None or _module_path_from_dotted(module) is None:
                continue
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def _function_param_schema_types(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, set[str]]:
    """Param name -> every class name appearing in its annotation.

    Deliberately not limited to a bare ``ast.Name``: FastAPI's idiomatic
    ``body: Annotated[BillUpdate, Body()]`` and ``body: Optional[BillUpdate]`` must resolve too,
    otherwise a router could evade the guard purely by how it spells its annotation. Caller
    intersects these candidates with the schema map / repo-origin set, so typing names like
    ``Annotated`` and ``Optional`` fall away harmlessly.
    """
    params: dict[str, set[str]] = {}
    all_args = (
        *func.args.posonlyargs,
        *func.args.args,
        *func.args.kwonlyargs,
    )
    for arg in all_args:
        if arg.annotation is None:
            continue
        params[arg.arg] = {
            n.id for n in ast.walk(arg.annotation) if isinstance(n, ast.Name)
        }
    return params


def _parse_param_attribute(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    return None


def _float_call_param_attributes(call: ast.Call) -> list[tuple[str, str]]:
    """Every ``param.field`` anywhere inside a ``float(...)`` argument.

    Searches the whole argument subtree, not just a direct attribute, so the laundered form
    ``float(Decimal(str(body.total_amount)))`` — which still yields a float — cannot slip past.
    """
    if not isinstance(call.func, ast.Name) or call.func.id != "float":
        return []
    hits: list[tuple[str, str]] = []
    for arg in call.args:
        for node in ast.walk(arg):
            match = _parse_param_attribute(node)
            if match:
                hits.append(match)
    return hits


def _body_visitor_skip_nested(func: ast.FunctionDef | ast.AsyncFunctionDef):
    """Yield ast nodes in func body, not descending into nested defs."""
    stack: list[ast.AST] = list(reversed(func.body))

    def push_children(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            stack.append(child)

    while stack:
        node = stack.pop()
        yield node
        push_children(node)


def _falsy_tested_nodes(node: ast.AST) -> list[ast.AST]:
    """Sub-expressions ``node`` evaluates for truthiness — the falsy-sensitive positions.

    ``if``/ternary tests, ``bool`` operands (``body.X or 0``, ``body.X and y``) and ``not body.X``,
    wherever they appear. Only a BARE attribute among these counts as a hit, so an ``ast.Compare``
    (``body.X is not None``) or an ``ast.Call`` never trips — that is why the correct sites are safe.
    """
    if isinstance(node, (ast.If, ast.IfExp)):
        return [node.test]
    if isinstance(node, ast.BoolOp):
        return list(node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return [node.operand]
    return []


def _conditional_truthy_sites(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[int, str, str]]:
    """(lineno, param, field) for every falsy-sensitive position — Rule 2.

    Overlaps (a BoolOp that IS an ``if`` test) are de-duplicated by the caller.
    """
    sites: list[tuple[int, str, str]] = []
    for node in _body_visitor_skip_nested(func):
        for tested in _falsy_tested_nodes(node):
            match = _parse_param_attribute(tested)
            if match:
                sites.append((node.lineno, *match))
    return sites


def _violations_in_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    schema_map: dict[str, set[str]],
    relpath: str,
    repo_origin: frozenset[str],
) -> list[MoneyCoercionViolation]:
    param_types = _function_param_schema_types(func)
    param_decimal_fields: dict[str, set[str]] = {}
    unresolved_params: set[str] = set()
    for param, candidates in param_types.items():
        resolved = candidates & set(schema_map)
        if resolved:
            param_decimal_fields[param] = set().union(
                *(schema_map[name] for name in resolved)
            )
        elif candidates & repo_origin:
            # Names our own packages own but we could not parse — fail closed.
            unresolved_params.add(param)

    def classify(lineno: int, param: str, field: str, rule: str):
        """Emit ``rule``, or the fail-closed rule when the param's schema was unresolvable."""
        if param in unresolved_params:
            return MoneyCoercionViolation(relpath, lineno, f"{param}.{field}", "UNRESOLVED_SCHEMA")
        if field in param_decimal_fields.get(param, ()):
            return MoneyCoercionViolation(relpath, lineno, f"{param}.{field}", rule)
        return None

    sites = [
        classify(node.lineno, param, field, "FLOAT_ON_DECIMAL")
        for node in _body_visitor_skip_nested(func)
        if isinstance(node, ast.Call)
        for param, field in _float_call_param_attributes(node)
    ] + [
        classify(lineno, param, field, "TRUTHY_ON_DECIMAL")
        for lineno, param, field in _conditional_truthy_sites(func)
    ]
    # dedup: one expression can be reached twice (e.g. `float(body.x + body.x)`).
    return list(dict.fromkeys(v for v in sites if v is not None))


def find_violations(
    tree: ast.Module,
    schema_map: dict[str, set[str]],
    relpath: str,
    repo_origin: frozenset[str] = frozenset(),
) -> list[MoneyCoercionViolation]:
    violations: list[MoneyCoercionViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.extend(
                _violations_in_function(node, schema_map, relpath, repo_origin)
            )
    return violations


def _is_allowlisted(v: MoneyCoercionViolation) -> bool:
    _, _, field = v.param_field.partition(".")
    return (v.relpath, field) in MONEY_COERCION_ALLOWLIST


def collect_money_coercion_violations(
    root: Path, glob: str = ROUTER_GLOB
) -> tuple[MoneyCoercionViolation, ...]:
    """Scan every file matching ``glob`` under ``root``.

    ``glob`` is a parameter so the detector can be pointed at another tree (the same falsy-Decimal
    defect exists in ``integrations/``) without a rewrite. Guarding that: a scanned path outside
    ``_SCHEMA_ROOTS`` would silently resolve NO schemas — both the schema map and the repo-origin
    set come back empty, so even the fail-closed rule stays quiet and the guard reports a clean
    green over a defective tree. Fail loudly instead of fail open.
    """
    all_violations: list[MoneyCoercionViolation] = []
    for path in sorted(root.glob(glob)):
        relpath = path.relative_to(root).as_posix()
        assert relpath.split("/")[0] in _SCHEMA_ROOTS, (
            f"{relpath} is outside _SCHEMA_ROOTS {sorted(_SCHEMA_ROOTS)} — its imports would "
            "resolve to nothing and the guard would pass vacuously. Add the root first."
        )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        all_violations.extend(
            find_violations(
                tree,
                build_schema_map(path, tree),
                relpath,
                frozenset(repo_origin_class_names(path, tree)),
            )
        )
    return tuple(all_violations)


@pytest.fixture(scope="session")
def repo_violations() -> tuple[MoneyCoercionViolation, ...]:
    """One repo-wide scan shared by every test that needs it (~1.2s, so do it once)."""
    return collect_money_coercion_violations(REPO_ROOT)


def _format_violation(v: MoneyCoercionViolation) -> str:
    return (
        f"{v.relpath}:{v.lineno}: {v.param_field} [{v.rule}] — "
        "route it through shared.api.money.to_decimal_or_none"
    )


def test_no_money_coercion_violations_in_entity_routers(repo_violations):
    router_paths = list(REPO_ROOT.glob(ROUTER_GLOB))
    assert len(router_paths) >= 50, (
        f"router glob matched only {len(router_paths)} files — broken ROUTER_GLOB?"
    )
    violations = [v for v in repo_violations if not _is_allowlisted(v)]
    assert not violations, "money coercion guard failed:\n" + "\n".join(
        sorted(_format_violation(v) for v in violations)
    )


def test_detector_flags_reintroduced_float_on_money_field():
    source = '''
from decimal import Decimal

class BodyModel:
    total_amount: Decimal

async def update_router(body: BodyModel):
    x = float(body.total_amount)
'''
    tree = ast.parse(source)
    schema_map = build_schema_map(Path("synthetic.py"), tree)
    hits = find_violations(tree, schema_map, "synthetic.py")
    assert len(hits) == 1
    assert hits[0].rule == "FLOAT_ON_DECIMAL"
    assert hits[0].param_field == "body.total_amount"


def test_detector_flags_reintroduced_truthy_guard_on_money_field():
    source = '''
from decimal import Decimal

class BodyModel:
    markup: Decimal

async def update_router(body: BodyModel):
    payload = {"markup": Decimal(str(body.markup)) if body.markup else None}
'''
    tree = ast.parse(source)
    schema_map = build_schema_map(Path("synthetic.py"), tree)
    hits = find_violations(tree, schema_map, "synthetic.py")
    assert len(hits) == 1
    assert hits[0].rule == "TRUTHY_ON_DECIMAL"
    assert hits[0].param_field == "body.markup"


def test_detector_flags_falsy_or_default_on_money_field():
    """``body.X or default`` is the same zero-drop outside a conditional test."""
    source = '''
from decimal import Decimal

class BodyModel:
    markup: Decimal

async def update_router(body: BodyModel):
    payload = {"markup": body.markup or Decimal("0.10")}
'''
    tree = ast.parse(source)
    hits = find_violations(tree, build_schema_map(Path("synthetic.py"), tree), "synthetic.py")
    assert len(hits) == 1
    assert hits[0].rule == "TRUTHY_ON_DECIMAL"
    assert hits[0].param_field == "body.markup"


def test_detector_flags_negated_truthy_on_money_field():
    """``not body.X`` treats a genuine $0.00 as 'missing'."""
    source = '''
from decimal import Decimal

class BodyModel:
    total_amount: Decimal

async def update_router(body: BodyModel):
    if not body.total_amount:
        raise ValueError("total required")
'''
    tree = ast.parse(source)
    hits = find_violations(tree, build_schema_map(Path("synthetic.py"), tree), "synthetic.py")
    assert len(hits) == 1
    assert hits[0].rule == "TRUTHY_ON_DECIMAL"
    assert hits[0].param_field == "body.total_amount"


def test_detector_flags_laundered_float_call():
    """Codex Pass-1 P3: ``float(Decimal(str(body.X)))`` still yields a float — must not slip past."""
    source = '''
from decimal import Decimal

class BodyModel:
    total_amount: Decimal

async def update_router(body: BodyModel):
    x = float(Decimal(str(body.total_amount)))
'''
    tree = ast.parse(source)
    hits = find_violations(tree, build_schema_map(Path("synthetic.py"), tree), "synthetic.py")
    assert len(hits) == 1
    assert hits[0].rule == "FLOAT_ON_DECIMAL"
    assert hits[0].param_field == "body.total_amount"


@pytest.mark.parametrize(
    "annotation",
    ["BodyModel", "Optional[BodyModel]", "Annotated[BodyModel, Body()]"],
)
def test_detector_resolves_wrapped_annotations(annotation):
    """Codex Pass-1 P3: the guard must not be evadable by how the annotation is spelled."""
    source = f'''
from decimal import Decimal
from typing import Annotated, Optional
from fastapi import Body

class BodyModel:
    total_amount: Decimal

async def update_router(body: {annotation}):
    x = float(body.total_amount)
'''
    tree = ast.parse(source)
    hits = find_violations(tree, build_schema_map(Path("synthetic.py"), tree), "synthetic.py")
    assert len(hits) == 1, f"{annotation} evaded the guard"
    assert hits[0].rule == "FLOAT_ON_DECIMAL"


def test_detector_ignores_is_not_none_and_non_decimal_fields():
    source = '''
class BodyModel:
    amount: Decimal
    status: str
    content_type: str
    confidence: float

async def update_router(body: BodyModel):
    a = Decimal(str(body.amount)) if body.amount is not None else None
    s = body.status.strip() if body.status else None
    t = body.content_type or "application/octet-stream"
    c = float(body.confidence)
    if not body.status:
        pass
'''
    tree = ast.parse(source)
    schema_map = build_schema_map(Path("synthetic.py"), tree)
    assert find_violations(tree, schema_map, "synthetic.py") == []


def test_detector_fails_closed_on_unresolvable_repo_schema():
    """A model our own packages own but we failed to parse must FAIL, never silently pass."""
    source = '''
from entities.ghost.api.schemas import SomeUnknownModel

async def update_router(body: SomeUnknownModel):
    x = float(body.amount)
'''
    tree = ast.parse(source)
    path = REPO_ROOT / "entities" / "ghost" / "api" / "router.py"
    schema_map = build_schema_map(path, tree)
    repo_origin = frozenset(repo_origin_class_names(path, tree))
    assert "SomeUnknownModel" in repo_origin and "SomeUnknownModel" not in schema_map
    hits = find_violations(tree, schema_map, "synthetic.py", repo_origin)
    assert len(hits) == 1
    assert hits[0].rule == "UNRESOLVED_SCHEMA"
    assert hits[0].param_field == "body.amount"


def test_detector_ignores_non_schema_params():
    """``file: UploadFile`` is a fastapi type, not one of our request models — never a candidate.

    Without the repo-origin filter the fail-closed rule fires on every ``if not file.filename``
    in entities/attachment/api/router.py.
    """
    source = '''
from fastapi import UploadFile

async def upload_router(file: UploadFile):
    name = file.filename or "unnamed"
    if not file.filename:
        raise ValueError("filename required")
    size = float(file.size)
'''
    tree = ast.parse(source)
    path = REPO_ROOT / "entities" / "attachment" / "api" / "router.py"
    repo_origin = frozenset(repo_origin_class_names(path, tree))
    assert "UploadFile" not in repo_origin
    assert find_violations(tree, build_schema_map(path, tree), "synthetic.py", repo_origin) == []


def test_allowlist_entries_are_still_live(repo_violations):
    for relpath, field in MONEY_COERCION_ALLOWLIST:
        matching = [
            v
            for v in repo_violations
            if v.relpath == relpath and v.param_field.endswith(f".{field}")
        ]
        assert matching, f"stale allowlist entry: ({relpath!r}, {field!r})"
