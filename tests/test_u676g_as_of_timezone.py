"""U-676g: the bill-drop coding step compared an aware now() with naive DB timestamps.

Live symptom (2026-10-18 first drop): POST /bill/read-attachment returned status
"partial" — header filled, coding null — because build_candidate_sccs_for_bill raised
TypeError("can't compare offset-naive and offset-aware datetimes"). The fixtures here use
REAL dated catalog rows (the earlier tests' catalogs had none, so the comparison never ran).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.coding_suggestion.business import bill_text_suggestion_service as bts
from entities.coding_suggestion.business.bill_text_suggestion_service import CatalogSnapshot
from intelligence.cascade.bill_coding_payload import build_candidate_sccs_for_bill

_NOW_NAIVE = datetime(2026, 10, 18, 18, 0, 0)  # how SubCostCode.CreatedDatetime arrives


def _scc(number: str, created: datetime):
    return SimpleNamespace(number=number, name=f"SCC {number}", created=created)


def _catalog() -> CatalogSnapshot:
    old = _scc("01.100", _NOW_NAIVE - timedelta(days=400))
    new = _scc("02.200", _NOW_NAIVE + timedelta(days=1))  # created after the cutoff
    return CatalogSnapshot(
        scc_by_id={1: old, 2: new},
        scc_number_to_id={"01.100": 1, "02.200": 2},
        ambiguous_scc_numbers=frozenset(),
        proj_abbrev_to_id={"P1": 10},
        ambiguous_project_abbrevs=frozenset(),
        candidate_projects=[{"abbreviation": "P1", "name": "Project One"}],
        vendor_name="",
    )


def test_candidate_sccs_accept_an_aware_cutoff_like_a_naive_one():
    catalog = _catalog()
    naive = build_candidate_sccs_for_bill(
        catalog.scc_by_id, as_of=_NOW_NAIVE, ambiguous_numbers=frozenset()
    )
    aware = build_candidate_sccs_for_bill(
        catalog.scc_by_id,
        as_of=_NOW_NAIVE.replace(tzinfo=timezone.utc),
        ambiguous_numbers=frozenset(),
    )
    assert naive == aware == [{"number": "01.100", "name": "SCC 01.100"}]


def test_predict_text_coding_builds_its_prompt_with_dated_catalog_rows():
    """The drop path's call shape (aware or naive now) reaches the model with candidates."""
    sent: list[dict] = []

    async def fake_cascade(payload, **_kw):
        sent.append(payload)
        return SimpleNamespace(stopped_reason=None, accepted=False, result=None)

    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.read_vendor_scc_counts.return_value = []
    settings = MagicMock(bill_coding_text_daily_call_cap=200)
    with patch.object(bts, "load_catalog_from_db", return_value=_catalog()), patch.object(
        bts, "_read_vendor_name", return_value="Acme Supply"
    ), patch.object(bts, "run_bill_coding_suggestion", new=fake_cascade), patch.object(
        bts.config, "Settings", return_value=settings
    ):
        for as_of in (_NOW_NAIVE, _NOW_NAIVE.replace(tzinfo=timezone.utc)):
            asyncio.run(
                bts.predict_text_coding(
                    bill_text="ACME SUPPLY INVOICE",
                    vendor_id=7,
                    chosen_project_id=None,
                    as_of=as_of,
                    text_repo=repo,
                )
            )
    assert len(sent) == 2
    for payload in sent:
        assert payload["vendor_name"] == "Acme Supply"
        assert [c["number"] for c in payload["candidate_sccs"]] == ["01.100"]


def test_read_attachment_drop_path_uses_a_naive_utc_cutoff():
    """The drop path hands the coding step the same kind of as_of the tick does."""
    import inspect

    from entities.bill.business import read_attachment_service as ras

    src = inspect.getsource(ras.run_bill_read_attachment)
    assert "datetime.now(timezone.utc).replace(tzinfo=None)" in src
