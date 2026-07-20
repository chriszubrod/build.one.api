"""Pure-logic tests for vendor compliance SharePoint folder helpers (U-095)."""
from entities.vendor_compliance_document.business.folder_helpers import (
    build_export_filename,
    is_compliance_hint,
    walk_folder_tree,
)


def test_is_compliance_hint_matches_keywords_case_insensitively():
    assert is_compliance_hint("A&A COI.pdf") is True
    assert is_compliance_hint("vendor_insurance_policy.pdf") is True
    assert is_compliance_hint("Business LICENSE scan.pdf") is True
    assert is_compliance_hint("contractor_licence.jpg") is True
    assert is_compliance_hint("Safety CERTIFICATE.pdf") is True
    assert is_compliance_hint("ACORD-25-form.pdf") is True


def test_is_compliance_hint_rejects_unrelated_names():
    assert is_compliance_hint("invoice-12345.pdf") is False
    assert is_compliance_hint("w9-form.pdf") is False
    assert is_compliance_hint("readme.txt") is False


def test_build_export_filename_differs_for_distinct_public_ids():
    original = "A&A COI.pdf"
    first = build_export_filename(original, "1234abcd-5678-90ab-cdef-1234567890ab")
    second = build_export_filename(original, "ffffeeee-dddd-cccc-bbbb-aaaaaaaaaaaa")
    assert first != second
    assert first == "A&A COI__1234abcd567890abcdef1234567890ab.pdf"
    assert second == "A&A COI__ffffeeeeddddccccbbbbaaaaaaaaaaaa.pdf"


def test_build_export_filename_differs_when_first_8_hex_chars_match():
    original = "A&A COI.pdf"
    first = build_export_filename(original, "1234abcd-1111-1111-1111-111111111111")
    second = build_export_filename(original, "1234abcd-2222-2222-2222-222222222222")
    assert first != second
    assert first == "A&A COI__1234abcd111111111111111111111111.pdf"
    assert second == "A&A COI__1234abcd222222222222222222222222.pdf"


def test_build_export_filename_is_stable_for_same_public_id():
    original = "A&A COI.pdf"
    public_id = "1234abcd-5678-90ab-cdef-1234567890ab"
    assert build_export_filename(original, public_id) == build_export_filename(original, public_id)


def test_build_export_filename_strips_sharepoint_illegal_chars_and_preserves_extension():
    result = build_export_filename('bad<>:"/\\|?*name.pdf', "abcd1234-0000-0000-0000-000000000000")
    assert result == "badname__abcd1234000000000000000000000000.pdf"
    assert "<" not in result
    assert ">" not in result
    assert ":" not in result


def test_build_export_filename_without_extension_still_appends_slug():
    result = build_export_filename("COI scan", "abcd1234-0000-0000-0000-000000000000")
    assert result == "COI scan__abcd1234000000000000000000000000"


def _fake_tree():
    """Nested tree spanning two levels below root."""
    return {
        "root": [
            {"item_id": "file-root", "name": "root-coi.pdf", "item_type": "file"},
            {
                "item_id": "folder-a",
                "name": "Policies",
                "item_type": "folder",
            },
        ],
        "folder-a": [
            {"item_id": "file-a1", "name": "insurance.pdf", "item_type": "file"},
            {
                "item_id": "folder-b",
                "name": "Archive",
                "item_type": "folder",
            },
        ],
        "folder-b": [
            {"item_id": "file-b1", "name": "old-license.pdf", "item_type": "file"},
        ],
    }


def _make_list_children(tree: dict):
    def list_children_fn(drive_id: str, item_id: str) -> dict:
        return {"items": tree.get(item_id, [])}

    return list_children_fn


def test_walk_folder_tree_flattens_nested_files_with_folder_path():
    files = walk_folder_tree(_make_list_children(_fake_tree()), "drive-1", "root")

    by_id = {f["item_id"]: f for f in files}
    assert set(by_id) == {"file-root", "file-a1", "file-b1"}
    assert by_id["file-root"]["folder_path"] == ""
    assert by_id["file-a1"]["folder_path"] == "Policies"
    assert by_id["file-b1"]["folder_path"] == "Policies/Archive"


def test_walk_folder_tree_respects_max_depth():
    files = walk_folder_tree(
        _make_list_children(_fake_tree()),
        "drive-1",
        "root",
        max_depth=1,
    )
    assert {f["item_id"] for f in files} == {"file-root"}


def test_walk_folder_tree_respects_max_items():
    tree = {
        "root": [
            {"item_id": f"file-{i}", "name": f"f{i}.pdf", "item_type": "file"}
            for i in range(5)
        ],
    }
    files = walk_folder_tree(
        _make_list_children(tree),
        "drive-1",
        "root",
        max_items=3,
    )
    assert len(files) == 3


def test_walk_folder_tree_does_not_loop_on_cycle():
    tree = {
        "root": [
            {"item_id": "folder-a", "name": "A", "item_type": "folder"},
        ],
        "folder-a": [
            {"item_id": "folder-b", "name": "B", "item_type": "folder"},
            {"item_id": "file-a", "name": "a.pdf", "item_type": "file"},
        ],
        "folder-b": [
            {"item_id": "folder-a", "name": "A-again", "item_type": "folder"},
            {"item_id": "file-b", "name": "b.pdf", "item_type": "file"},
        ],
    }
    files = walk_folder_tree(_make_list_children(tree), "drive-1", "root")
    assert {f["item_id"] for f in files} == {"file-a", "file-b"}
