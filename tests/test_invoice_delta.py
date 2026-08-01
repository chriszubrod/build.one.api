"""Pure unit tests for invoice draw delta / untag planning."""

from entities.invoice.business.delta import build_untag_plan, partition_removal_candidates


def test_partition_confident_vs_ambiguous():
    stale_z = "AAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    current = {"still-linked-z"}
    already_tagged = [
        {
            "row": 10,
            "z": stale_z,
            "ref": "INV-1",
            "vendor": "Acme",
            "description": "stale tag",
            "ws_total": 100.0,
        },
        {
            "row": 11,
            "z": "",
            "ref": "INV-2",
            "vendor": "Beta",
            "description": "no z",
            "ws_total": 50.0,
        },
        {
            "row": 12,
            "z": None,
            "ref": "INV-3",
            "vendor": "Gamma",
            "description": "null z",
            "ws_total": 25.0,
        },
    ]
    result = partition_removal_candidates(already_tagged, current)
    assert len(result["confident"]) == 1
    assert result["confident"][0]["row"] == 10
    assert result["confident"][0]["z"] == stale_z
    assert len(result["ambiguous"]) == 2
    assert {e["row"] for e in result["ambiguous"]} == {11, 12}


def test_partition_still_linked_row_dropped():
    linked_z = "11111111-2222-3333-4444-555555555555"
    stale_z = "99999999-8888-7777-6666-555555555555"
    already_tagged = [
        {
            "row": 5,
            "z": linked_z,
            "ref": "X",
            "vendor": "V",
            "description": "still on invoice",
            "ws_total": 1.0,
        },
        {
            "row": 6,
            "z": stale_z,
            "ref": "Y",
            "vendor": "V",
            "description": "orphan tag",
            "ws_total": 2.0,
        },
    ]
    result = partition_removal_candidates(
        already_tagged,
        [linked_z.upper()],
    )
    assert result["confident"] == [already_tagged[1]]
    assert result["ambiguous"] == []


def test_partition_empty_input():
    result = partition_removal_candidates([], set())
    assert result == {"confident": [], "ambiguous": []}


def test_partition_all_ambiguous():
    rows = [
        {"row": 1, "z": "", "ref": "a", "vendor": "v", "description": "d", "ws_total": 0.0},
        {"row": 2, "z": None, "ref": "b", "vendor": "v", "description": "d", "ws_total": 0.0},
    ]
    result = partition_removal_candidates(rows, {"anything"})
    assert result["confident"] == []
    assert result["ambiguous"] == rows


def test_build_untag_plan_dedupes_preserving_order():
    z1 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    z2 = "ffffffff-1111-2222-3333-444444444444"
    confident = [
        {"row": 20, "z": z1, "ref": "r1", "vendor": "v", "description": "d", "ws_total": 1.0},
        {"row": 21, "z": z1.upper(), "ref": "r2", "vendor": "v", "description": "d", "ws_total": 2.0},
        {"row": 20, "z": z2, "ref": "r3", "vendor": "v", "description": "d", "ws_total": 3.0},
    ]
    plan = build_untag_plan(confident)
    assert plan["box_keys"] == [z1.lower(), z2.lower()]


def test_build_untag_plan_empty():
    assert build_untag_plan([]) == {"box_keys": []}


def test_apply_removals_halts_when_both_writes_disabled(monkeypatch):
    """The mutating apply must fail closed BEFORE any DB/worksheet access when the
    write gates are off — no worksheet read, no clear, just a halt."""
    monkeypatch.delenv("ALLOW_MS_WRITES", raising=False)
    monkeypatch.delenv("ALLOW_BOX_WRITES", raising=False)
    from entities.invoice.business.delta import InvoiceDrawDeltaService

    result = InvoiceDrawDeltaService().apply_removals("any-invoice-public-id")
    assert result["status"] == "halt"
    assert result["reason"] == "writes_disabled"
    assert result["ms_writes"] is False and result["box_writes"] is False


def test_apply_removals_halts_when_only_one_gate_enabled(monkeypatch):
    """BOTH ALLOW_MS_WRITES and ALLOW_BOX_WRITES are required — one is not enough
    (a one-sided clear would desync SharePoint vs Box)."""
    monkeypatch.setenv("ALLOW_MS_WRITES", "true")
    monkeypatch.delenv("ALLOW_BOX_WRITES", raising=False)
    from entities.invoice.business.delta import InvoiceDrawDeltaService

    result = InvoiceDrawDeltaService().apply_removals("any-invoice-public-id")
    assert result["status"] == "halt"
    assert result["reason"] == "writes_disabled"
    assert result["ms_writes"] is True and result["box_writes"] is False
