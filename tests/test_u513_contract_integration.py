"""U-513: the contract seam, verified across package boundaries.

WHY THIS FILE EXISTS
--------------------
U-513 was built by four agents working in PARALLEL in isolated worktrees. The
three consumer packages (customer, vendor, company_info) were written against a
specified signature for `PhysicalAddressAddressConnector.sync_address_from_external`
while a fourth agent was still implementing it, so every consumer test MOCKS the
connector -- correctly, since the real method did not exist in their worktrees.

That leaves one failure mode no individual suite can catch, and it is the exact
failure mode parallel development produces: a consumer passing a keyword the real
method does not accept. A `Mock` swallows `qbo_id=`, `qboid=`, `realm=` and a
missing required argument identically and reports success; the real call raises
TypeError at runtime, in a QBO pull, in prod.

These tests bind every production call site against the REAL signature.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from integrations.intuit.qbo.physical_address.connector.business.service import (
    PhysicalAddressAddressConnector,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
QBO_ROOT = REPO_ROOT / "integrations" / "intuit" / "qbo"
CONTRACT = inspect.signature(PhysicalAddressAddressConnector.sync_address_from_external)


def _call_sites():
    """Every production call to sync_address_from_external, as (file, line, kwargs)."""
    sites = []
    for path in QBO_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "sync_address_from_external":
                continue
            if any(kw.arg is None for kw in node.keywords):   # **kwargs splat
                continue
            sites.append((path.relative_to(REPO_ROOT), node.lineno,
                          tuple(kw.arg for kw in node.keywords), len(node.args)))
    return sites


def test_the_call_sites_were_actually_found():
    """ANTI-VACUITY. If the AST walk finds nothing, every test below passes
    trivially -- which is precisely how a cross-package contract break would
    slip through unnoticed."""
    sites = _call_sites()
    # U-513 ph3b: `physical_address` dropped out of the expected set. It used to
    # appear because the connector's own `sync_from_qbo_to_address` wrapper called
    # the contract internally; that wrapper died with `qbo.PhysicalAddress`, so the
    # DEFINING package is no longer also a calling one. The three CONSUMER packages
    # are what this file was always really guarding.
    assert len(sites) >= 3, (
        f"expected a call site in each of customer/vendor/company_info, "
        f"found {len(sites)}: {[str(s[0]) for s in sites]}"
    )
    packages = {str(s[0]).split("/")[3] for s in sites}
    assert {"customer", "vendor", "company_info"} <= packages, (
        f"a consumer package has no call site -- its work is inert: {sorted(packages)}"
    )


@pytest.mark.parametrize("site", _call_sites(), ids=lambda s: f"{s[0]}:{s[1]}")
def test_every_call_site_binds_against_the_real_signature(site):
    """THE point of this file: each consumer's actual kwargs must satisfy the
    real method. A Mock accepts anything; `Signature.bind` does not."""
    path, lineno, kwargs, n_positional = site
    assert n_positional == 0, (
        f"{path}:{lineno} passes {n_positional} positional arg(s); the contract is "
        f"keyword-only, so this is a TypeError at runtime"
    )
    try:
        CONTRACT.bind(None, **{k: None for k in kwargs})
    except TypeError as exc:
        pytest.fail(
            f"{path}:{lineno} does not satisfy the contract: {exc}\n"
            f"  passes:   {sorted(kwargs)}\n"
            f"  contract: {sorted(p for p in CONTRACT.parameters if p != 'self')}"
        )


def test_contract_parameters_are_exactly_as_specified():
    """The signature three packages were written against, frozen.

    Changing it is not a local edit to physical_address -- it silently breaks
    customer, vendor and company_info, whose tests all mock this method and
    would stay green.
    """
    params = [p for p in CONTRACT.parameters if p != "self"]
    assert params == [
        "qbo_id", "realm_id", "line1", "line2", "city",
        "country_sub_division_code", "postal_code", "source_ref",
    ], f"contract drift: {params}"
    for name, p in CONTRACT.parameters.items():
        if name == "self":
            continue
        assert p.kind is p.KEYWORD_ONLY, f"{name} must stay keyword-only"
    assert CONTRACT.parameters["source_ref"].default is None
    for required in ("qbo_id", "realm_id", "line1", "city", "postal_code"):
        assert CONTRACT.parameters[required].default is inspect.Parameter.empty, (
            f"{required} gained a default; a consumer omitting it would then fail "
            f"silently rather than loudly"
        )


def test_project_records_seam_is_untouched():
    """U-513's design turns on threading the payload via a CLOSURE rather than
    widening the shared seam. `project_records` has 10 call sites across 8 other
    QBO families; a signature change there is a blast radius this unit refused."""
    from integrations.intuit.qbo.base import sync_outcome

    params = list(inspect.signature(sync_outcome.project_records).parameters)
    assert params == ["records", "outcome", "label", "project_one", "logger"], (
        f"project_records was widened: {params}. Eight other families ride this seam."
    )
