"""Recurring guard (U-073/U-076): every param the repo layer sends to a stored
procedure must be DECLARED by some .sql definition of that procedure.

Why this exists: the repo calls sprocs by NAME and binds params BY NAME
(`EXEC @X = ?`). If a `call_procedure(name=..., params={...})` sends a key that the
procedure doesn't declare, SQL Server raises 8145 ("@X is not a parameter for
procedure") at runtime — a latent 500. This is the class behind the 2026-07-15
TimeEntry outage (U-037), the 2026-07-19 Bill/Expense/CL list-sproc P0 (U-089), and
the ReadTimeEntries / CreateSubCostCode findings (U-073/U-076 audit).

What this catches (pure-code, no DB): a repo `call_procedure` param that NO .sql file
declares for that sproc — i.e. code changed to send a param but the SQL was never
written. It also catches a sproc called by the repo that no .sql defines at all.

What it does NOT catch: base-file-vs-PROD drift (the .sql is correct but the deployed
sproc reverted). That needs a live `sys.parameters` sweep — see the U-073/U-076 audit
procedure in api/TODO.md; run it periodically against prod.
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _dict_string_keys(node: ast.AST) -> set[str]:
    keys: set[str] = set()
    if isinstance(node, ast.Dict):
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value)
    return keys


def _func_var_keys(fn: ast.AST) -> dict[str, set[str]]:
    """Dict-keys built per local var name (handles the `params={}; params[k]=..` pattern)."""
    vk: dict[str, set[str]] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    vk.setdefault(t.id, set()).update(_dict_string_keys(n.value))
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    if isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
                        vk.setdefault(t.value.id, set()).add(t.slice.value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "update":
            if isinstance(n.func.value, ast.Name) and n.args and isinstance(n.args[0], ast.Dict):
                vk.setdefault(n.func.value.id, set()).update(_dict_string_keys(n.args[0]))
    return vk


def _collect_callsites() -> list[tuple[str, int, str, set[str]]]:
    """(relpath, lineno, sproc, param_keys) for every call_procedure(name=..., params=...)."""
    out = []
    for path in REPO_ROOT.rglob("*.py"):
        sp = str(path)
        if "/.venv/" in sp or "/tests/" in sp:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        fn_vk = {id(fn): _func_var_keys(fn) for fn in fns}

        def enclosing_vk(lineno: int) -> dict[str, set[str]]:
            best, best_span = None, 10**9
            for fn in fns:
                end = getattr(fn, "end_lineno", fn.lineno)
                if fn.lineno <= lineno <= end and (end - fn.lineno) < best_span:
                    best, best_span = fn, end - fn.lineno
            return fn_vk.get(id(best), {}) if best else {}

        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            is_cp = (isinstance(f, ast.Name) and f.id == "call_procedure") or (
                isinstance(f, ast.Attribute) and f.attr == "call_procedure"
            )
            if not is_cp:
                continue
            name = params = None
            for kw in n.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    name = kw.value.value
                if kw.arg == "params":
                    params = kw.value
            if name is None:
                continue
            if isinstance(params, ast.Dict):
                keys = _dict_string_keys(params)
            elif isinstance(params, ast.Name):
                keys = enclosing_vk(n.lineno).get(params.id, set())
            else:
                keys = set()
            out.append((str(path.relative_to(REPO_ROOT)), n.lineno, name, {k.lower() for k in keys}))
    return out


# Handles: dbo.Name · [dbo].[Name] · [Name] · Name · qbo.Name (optional bracketed schema)
_PROC_RE = re.compile(
    r"CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+(?:\[?\w+\]?\s*\.\s*)?\[?(\w+)\]?(.*?)\bAS\b",
    re.I | re.S,
)


def _collect_sql_params() -> dict[str, set[str]]:
    """sproc name (lower) -> union of declared @param names (lower) across ALL .sql."""
    defined: dict[str, set[str]] = {}
    for path in REPO_ROOT.rglob("*.sql"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _PROC_RE.finditer(text):
            name = m.group(1).lower()
            params = {p.lower() for p in re.findall(r"@(\w+)", m.group(2))}
            defined.setdefault(name, set()).update(params)
    return defined


def test_repo_never_sends_a_param_no_sql_declares():
    defined = _collect_sql_params()
    orphans = []
    undefined_sprocs = set()
    for rel, ln, sproc, sent in _collect_callsites():
        key = sproc.lower()
        if key not in defined:
            undefined_sprocs.add((sproc, rel, ln))
            continue
        extra = sent - defined[key]
        if extra:
            orphans.append(f"{sproc} ({rel}:{ln}) sends {sorted(extra)} — declared by NO .sql")
    msg = ""
    if undefined_sprocs:
        msg += "\nSprocs called but defined in NO .sql (write the SQL or fix the name):\n  " + "\n  ".join(
            f"{n} ({r}:{l})" for n, r, l in sorted(undefined_sprocs)
        )
    if orphans:
        msg += "\nParams sent but declared by no .sql (8145-latent):\n  " + "\n  ".join(sorted(orphans))
    assert not undefined_sprocs and not orphans, msg
