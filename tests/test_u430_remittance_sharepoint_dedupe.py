"""U-430 — remittance check-stubs file to SharePoint as well as Box.

The generator only ever wrote to Box, so the SharePoint mirror drifted 157 files
behind between 2026-06-12 and 2026-09-04. Adding the upload is easy; deciding
whether a remittance is ALREADY on the far side is the part that can lose or
duplicate a financial document, so that decision is pure and tested here.

Two real hazards from the live 2026 folders shape these tests:

1. The pre-script SharePoint files carry SHORT vendor names ('Ferguson' vs
   'Ferguson Enterprises LLC'), so a vendor-sensitive key re-uploads 9 files
   under a second spelling.
2. Two DIFFERENT vendors in one payment can be paid the identical amount —
   payment 2502356724 pays Elmer Cordova and Wilmer Diaz $3,380.00 each — so a
   (doc, total) key collapses them into one and silently never uploads the
   second. That is a lost document, which is worse than a visible duplicate.

Note hazard 2 is not solved by adding the date — Cordova and Diaz share that too.
So the match is payment + total + a PREFIX-compatible vendor: loose enough to join
'Ferguson' to 'Ferguson Enterprises LLC', strict enough to keep 'Elmer Cordova'
apart from 'Wilmer Diaz'. Amount collisions that survive that test are reported by
`find_possible_twins` and still uploaded, because failing to file a document is
worse than filing a visible duplicate.
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from scripts.generate_payment_remittance import (
    find_possible_twins,
    needs_sharepoint_upload,
    parse_remittance_filename,
    same_remittance,
)

FERGUSON_LONG = "2026.06.05 - BILL PAYMENT - 2723064898 - Ferguson Enterprises LLC - $34,150.69.pdf"
FERGUSON_SHORT = "2026.06.05 - BILL PAYMENT - 2723064898 - Ferguson - $34,150.69.pdf"
CORDOVA = "2026.07.15 - BILL PAYMENT - 2502356724 - Elmer Cordova - $3,380.00.pdf"
DIAZ = "2026.07.15 - BILL PAYMENT - 2502356724 - Wilmer Diaz - $3,380.00.pdf"
BCHRIS_12TH = "2026.06.12 - BILL PAYMENT - 9361486213 - B. Christopher & Co., LLC - $16,000.00.pdf"
BCHRIS_18TH = "2026.06.18 - BILL PAYMENT - 9361486213 - B. Christopher & Co, LLC - $16,000.00.pdf"


class TestParseRemittanceFilename:
    def test_splits_the_convention(self):
        assert parse_remittance_filename(FERGUSON_LONG) == {
            "date": "2026.06.05",
            "doc_number": "2723064898",
            "vendor": "Ferguson Enterprises LLC",
            "total": "$34,150.69",
        }

    def test_vendor_containing_the_separator_survives(self):
        name = "2026.06.05 - BILL PAYMENT - 123 - Smith - Jones LLC - $1.00.pdf"
        assert parse_remittance_filename(name)["vendor"] == "Smith - Jones LLC"

    @pytest.mark.parametrize("name", [
        "2026.06.10 - BILL PAYMENT - 100675 - 100678.pdf",  # real off-convention check range
        "not a remittance.pdf",
        "",
    ])
    def test_off_convention_names_return_none(self, name):
        assert parse_remittance_filename(name) is None


class TestSameRemittance:
    def test_short_and_long_vendor_spelling_are_the_same_document(self):
        assert same_remittance(FERGUSON_LONG, FERGUSON_SHORT) is True

    @pytest.mark.parametrize("long_name,short_name", [
        ("2026.05.05 - BILL PAYMENT - 7431774292 - Ideal Millwork & Hardware - $9,170.76.pdf",
         "2026.05.05 - BILL PAYMENT - 7431774292 - Ideal Millwork - $9,170.76.pdf"),
        ("2026.05.05 - BILL PAYMENT - 7431774292 - Mobile Materials Nashville - $1,706.61.pdf",
         "2026.05.05 - BILL PAYMENT - 7431774292 - Mobile Materials - $1,706.61.pdf"),
        ("2026.05.05 - BILL PAYMENT - 7431774292 - The Structure Company of Nashville, LLC - $22,335.00.pdf",
         "2026.05.05 - BILL PAYMENT - 7431774292 - Structure Company of Nashville - $22,335.00.pdf"),
        ("2026.06.05 - BILL PAYMENT - 2723064898 - Garman Engineering LLC - $2,450.00.pdf",
         "2026.06.05 - BILL PAYMENT - 2723064898 - Garman Engineering - $2,450.00.pdf"),
        ("2026.06.10 - BILL PAYMENT - 1111457925 - Cobra, LLC - $5,757.25.pdf",
         "2026.06.10 - BILL PAYMENT - 1111457925 - Cobra - $5,757.25.pdf"),
        ("2026.06.10 - BILL PAYMENT - 1111457925 - Hartley Botanic Inc. - $11,015.90.pdf",
         "2026.06.10 - BILL PAYMENT - 1111457925 - Hartley Botanic - $11,015.90.pdf"),
        ("2026.06.10 - BILL PAYMENT - 1111457925 - Jones Stone Co. - $28,016.88.pdf",
         "2026.06.10 - BILL PAYMENT - 1111457925 - Jones Stone - $28,016.88.pdf"),
    ])
    def test_every_live_name_variant_pair_matches(self, long_name, short_name):
        """The 9 real Box/SharePoint spelling pairs must not become duplicates."""
        assert same_remittance(long_name, short_name) is True

    def test_different_vendors_at_the_same_amount_are_different_documents(self):
        """Cordova and Diaz: same payment, same total, SAME DATE — only the
        vendor separates them, so a vendor-blind key loses one."""
        assert same_remittance(CORDOVA, DIAZ) is False

    def test_punctuation_only_difference_is_the_same_document(self):
        assert same_remittance(BCHRIS_12TH, BCHRIS_18TH) is True

    def test_different_totals_are_never_the_same(self):
        other = FERGUSON_LONG.replace("$34,150.69", "$34,150.68")
        assert same_remittance(FERGUSON_LONG, other) is False

    def test_different_payments_are_never_the_same(self):
        other = FERGUSON_LONG.replace("2723064898", "2723064899")
        assert same_remittance(FERGUSON_LONG, other) is False

    def test_off_convention_name_never_matches(self):
        assert same_remittance("junk.pdf", FERGUSON_LONG) is False

    def test_parenthetical_qualifier_is_a_different_document(self):
        """'Weston Parker' and 'Weston Parker (Expense)' are two filings for one
        payer, and one normalizes to a prefix of the other — so without the
        parenthetical guard an identical total would drop one of them."""
        plain = "2026.06.15 - BILL PAYMENT - 1529260827 - Weston Parker - $778.00.pdf"
        qualified = "2026.06.15 - BILL PAYMENT - 1529260827 - Weston Parker (Expense) - $778.00.pdf"
        assert same_remittance(plain, qualified) is False
        assert needs_sharepoint_upload(qualified, [plain]) is True


class TestNeedsSharepointUpload:
    def test_exact_filename_present_is_skipped(self):
        assert needs_sharepoint_upload(FERGUSON_LONG, [FERGUSON_LONG]) is False

    def test_short_vendor_spelling_counts_as_present(self):
        """The 9-file duplicate hazard: same remittance, pre-script short name."""
        assert needs_sharepoint_upload(FERGUSON_LONG, [FERGUSON_SHORT]) is False

    def test_absent_file_is_uploaded(self):
        assert needs_sharepoint_upload(FERGUSON_LONG, [CORDOVA, DIAZ]) is True

    def test_empty_destination_uploads(self):
        assert needs_sharepoint_upload(FERGUSON_LONG, []) is True

    def test_same_amount_sibling_does_not_suppress_the_other_vendor(self):
        """THE data-loss case: Diaz must still upload when only Cordova is filed."""
        assert needs_sharepoint_upload(DIAZ, [CORDOVA]) is True

    def test_off_convention_name_falls_back_to_exact_match_only(self):
        odd = "2026.06.10 - BILL PAYMENT - 100675 - 100678.pdf"
        assert needs_sharepoint_upload(odd, [odd]) is False
        assert needs_sharepoint_upload(odd, [FERGUSON_LONG]) is True

    def test_same_document_refiled_under_another_date_is_skipped(self):
        assert needs_sharepoint_upload(BCHRIS_18TH, [BCHRIS_12TH]) is False


class TestFindPossibleTwins:
    def test_reports_same_amount_different_vendor(self):
        assert find_possible_twins([DIAZ], [CORDOVA]) == [(DIAZ, CORDOVA)]

    def test_a_recognised_same_document_is_not_a_twin(self):
        assert find_possible_twins([FERGUSON_LONG], [FERGUSON_SHORT]) == []

    def test_date_drift_alone_is_not_a_twin(self):
        assert find_possible_twins([BCHRIS_18TH], [BCHRIS_12TH]) == []

    def test_unrelated_files_are_not_twins(self):
        assert find_possible_twins([FERGUSON_LONG], [CORDOVA]) == []

    def test_off_convention_names_are_ignored(self):
        assert find_possible_twins(["junk.pdf"], [FERGUSON_LONG]) == []


# --------------------------------------------------------------------------- #
# Regression tests — one per confirmed Codex finding (review round 1)
# --------------------------------------------------------------------------- #
class TestUnreviewedVendorNamesNeverSuppress:
    """P1: the first draft matched 'one name is a prefix of the other', which
    also makes 'Acme' == 'Acme Construction' and would declare a remittance
    already filed when it is not. Only a reviewed alias may suppress."""

    ACME = "2026.01.02 - BILL PAYMENT - 777 - Acme - $1.00.pdf"
    ACME_LONG = "2026.01.02 - BILL PAYMENT - 777 - Acme Construction - $1.00.pdf"

    def test_prefix_alone_is_not_a_match(self):
        assert same_remittance(self.ACME, self.ACME_LONG) is False

    def test_prefix_alone_does_not_suppress_the_upload(self):
        assert needs_sharepoint_upload(self.ACME_LONG, [self.ACME]) is True

    def test_and_it_is_reported_for_review(self):
        assert find_possible_twins([self.ACME_LONG], [self.ACME]) == [
            (self.ACME_LONG, self.ACME)
        ]

    def test_only_reviewed_aliases_suppress(self):
        assert same_remittance(FERGUSON_LONG, FERGUSON_SHORT) is True


class TestFolderNamesAreNotFiles:
    """P1: dedupe compared against every child, so a FOLDER sharing a PDF's name
    satisfied the exact-name check and the document was never filed."""

    def test_only_file_entries_reach_the_dedupe(self):
        from scripts.generate_payment_remittance import _sp_file_names
        children = [
            {"name": FERGUSON_LONG, "item_type": "folder"},
            {"name": CORDOVA, "item_type": "file"},
        ]
        with patch("scripts.generate_payment_remittance._sp_children", return_value=children):
            names = _sp_file_names("drive", "folder")
        assert names == [CORDOVA]
        assert needs_sharepoint_upload(FERGUSON_LONG, names) is True


class TestListingFailureFailsClosed:
    """P1: the Graph client swallows errors into an EMPTY items list, which is
    indistinguishable from an empty folder — and 'empty' would defeat dedupe."""

    def test_non_200_listing_raises_instead_of_returning_empty(self):
        from scripts.generate_payment_remittance import _sp_children
        with patch("scripts.generate_payment_remittance.sp_client") as sp:
            sp.list_drive_item_children.return_value = {
                "status_code": 503, "message": "Service Unavailable", "items": []
            }
            with pytest.raises(SystemExit):
                _sp_children("drive", "folder")

    def test_successful_listing_returns_items(self):
        from scripts.generate_payment_remittance import _sp_children
        with patch("scripts.generate_payment_remittance.sp_client") as sp:
            sp.list_drive_item_children.return_value = {
                "status_code": 200, "items": [{"name": CORDOVA, "item_type": "file"}]
            }
            assert _sp_children("drive", "folder") == [{"name": CORDOVA, "item_type": "file"}]


class TestUploadRejectsFailureResponses:
    def test_non_200_upload_raises(self):
        from scripts.generate_payment_remittance import upload_to_sharepoint
        with patch("scripts.generate_payment_remittance.sp_client") as sp:
            sp.upload_small_file.return_value = {"status_code": 500, "message": "boom"}
            with pytest.raises(SystemExit):
                upload_to_sharepoint("drive", "folder", FERGUSON_LONG, b"%PDF-")

    def test_oversized_payload_is_refused_before_the_call(self):
        from scripts.generate_payment_remittance import upload_to_sharepoint
        with patch("scripts.generate_payment_remittance.sp_client") as sp:
            with pytest.raises(SystemExit):
                upload_to_sharepoint("drive", "folder", FERGUSON_LONG, b"x" * (4 * 1024 * 1024))
            sp.upload_small_file.assert_not_called()


class TestBackfillDryRunIsInert:
    """P2: nothing proved the default backfill performs no writes."""

    def _run(self, apply):
        from scripts.generate_payment_remittance import run_sharepoint_backfill
        box = MagicMock()
        box.download_file.return_value = b"%PDF-1.4"
        with ExitStack() as stack:
            stack.enter_context(patch("scripts.generate_payment_remittance.assert_cli_system_admin"))
            stack.enter_context(patch("scripts.generate_payment_remittance.BoxHttpClient", return_value=box))
            stack.enter_context(patch("scripts.generate_payment_remittance._box_items", side_effect=[
                [{"type": "folder", "name": "02 - Accounts Payable", "id": "a"}],
                [{"type": "folder", "name": "535 - Rogers Build - Check Stubs", "id": "b"}],
                [{"type": "folder", "name": "2026", "id": "c"}],
                [{"type": "file", "name": FERGUSON_LONG, "id": "f1"}],
            ]))
            stack.enter_context(patch("scripts.generate_payment_remittance.resolve_sp_year_folder",
                                      return_value="spfolder"))
            stack.enter_context(patch("scripts.generate_payment_remittance._sp_file_names",
                                      return_value=[]))
            up = stack.enter_context(patch("scripts.generate_payment_remittance.upload_to_sharepoint"))
            run_sharepoint_backfill("2026", apply=apply)
        return box, up

    def test_dry_run_downloads_nothing_and_uploads_nothing(self):
        box, up = self._run(apply=False)
        up.assert_not_called()
        box.download_file.assert_not_called()

    def test_apply_uploads_the_missing_file(self):
        box, up = self._run(apply=True)
        assert up.call_count == 1
        assert up.call_args[0][2] == FERGUSON_LONG
        box.download_file.assert_called_once_with("f1")


class TestBackfillRechecksBeforeEachUpload:
    """P1: the plan is built from one listing, but an upload run is long enough
    for another writer to file something midway — and the Graph path PUT is
    upload-OR-REPLACE, so a stale plan overwrites instead of failing."""

    def test_a_file_filed_since_the_plan_is_skipped_not_overwritten(self):
        from scripts.generate_payment_remittance import run_sharepoint_backfill
        box = MagicMock()
        box.download_file.return_value = b"%PDF-1.4"
        # Empty when the plan is built; the file has appeared by upload time.
        listings = [[], [FERGUSON_LONG]]
        with ExitStack() as stack:
            stack.enter_context(patch("scripts.generate_payment_remittance.assert_cli_system_admin"))
            stack.enter_context(patch("scripts.generate_payment_remittance.BoxHttpClient", return_value=box))
            stack.enter_context(patch("scripts.generate_payment_remittance._box_items", side_effect=[
                [{"type": "folder", "name": "02 - Accounts Payable", "id": "a"}],
                [{"type": "folder", "name": "535 - Rogers Build - Check Stubs", "id": "b"}],
                [{"type": "folder", "name": "2026", "id": "c"}],
                [{"type": "file", "name": FERGUSON_LONG, "id": "f1"}],
            ]))
            stack.enter_context(patch("scripts.generate_payment_remittance.resolve_sp_year_folder",
                                      return_value="spfolder"))
            stack.enter_context(patch("scripts.generate_payment_remittance._sp_file_names",
                                      side_effect=lambda *a, **k: listings.pop(0)))
            up = stack.enter_context(patch("scripts.generate_payment_remittance.upload_to_sharepoint"))
            run_sharepoint_backfill("2026", apply=True)
        up.assert_not_called()
        box.download_file.assert_not_called()


class TestYearFolderCacheIsPerYear:
    """P2: one cached folder for the whole batch misfiles every vendor after the
    first when a payment's BillPayments span two years (payment 8280187478 carries
    both 2026.03.20 and 2026.03.23)."""

    def test_each_year_resolves_its_own_folder(self):
        from scripts.generate_payment_remittance import sp_folder_for_year
        cache = {}
        with patch("scripts.generate_payment_remittance.resolve_sp_year_folder",
                   side_effect=lambda d, y, create: f"folder-{y}") as resolve:
            assert sp_folder_for_year(cache, "drive", "2025") == "folder-2025"
            assert sp_folder_for_year(cache, "drive", "2026") == "folder-2026"
            assert sp_folder_for_year(cache, "drive", "2025") == "folder-2025"
        assert resolve.call_count == 2  # memoised per year, not per file

    def test_second_year_is_not_given_the_first_years_folder(self):
        from scripts.generate_payment_remittance import sp_folder_for_year
        cache = {}
        with patch("scripts.generate_payment_remittance.resolve_sp_year_folder",
                   side_effect=lambda d, y, create: f"folder-{y}"):
            first = sp_folder_for_year(cache, "drive", "2025")
            second = sp_folder_for_year(cache, "drive", "2026")
        assert first != second


# --------------------------------------------------------------------------- #
# Regression tests — review round 2
# --------------------------------------------------------------------------- #
class TestNormalizationMakesNoWordLevelJudgements:
    """R2-P1: normalization also dropped a leading 'the', which made 'Acme' equal
    'The Acme' with no reviewed alias — breaking the one guarantee this predicate
    makes. Only punctuation and case may be normalized away."""

    PLAIN = "2026.01.02 - BILL PAYMENT - 777 - Acme - $1.00.pdf"
    ARTICLE = "2026.01.02 - BILL PAYMENT - 777 - The Acme - $1.00.pdf"

    def test_leading_article_is_a_real_difference(self):
        assert same_remittance(self.PLAIN, self.ARTICLE) is False

    def test_leading_article_does_not_suppress_the_upload(self):
        assert needs_sharepoint_upload(self.ARTICLE, [self.PLAIN]) is True

    def test_punctuation_and_case_are_still_normalized(self):
        assert same_remittance(BCHRIS_12TH, BCHRIS_18TH) is True

    def test_the_reviewed_article_pair_still_matches_via_the_alias_map(self):
        long_name = ("2026.05.05 - BILL PAYMENT - 7431774292 - "
                     "The Structure Company of Nashville, LLC - $22,335.00.pdf")
        short_name = ("2026.05.05 - BILL PAYMENT - 7431774292 - "
                      "Structure Company of Nashville - $22,335.00.pdf")
        assert same_remittance(long_name, short_name) is True


class TestTruncatedListingFailsClosed:
    """R2-P2: the shared paginator stops early at a page cap or a repeated
    nextLink and still reports 200, so a PARTIAL folder listing is
    indistinguishable from a complete one — and dedupe would then miss a file
    that is really there, ahead of a replace-capable PUT."""

    def test_truncated_listing_raises(self):
        from scripts.generate_payment_remittance import _sp_children
        with patch("scripts.generate_payment_remittance.sp_client") as sp:
            sp.list_drive_item_children.return_value = {
                "status_code": 200, "items": [{"name": CORDOVA, "item_type": "file"}],
                "truncated": True,
            }
            with pytest.raises(SystemExit):
                _sp_children("drive", "folder")

    def test_complete_listing_is_accepted(self):
        from scripts.generate_payment_remittance import _sp_children
        with patch("scripts.generate_payment_remittance.sp_client") as sp:
            sp.list_drive_item_children.return_value = {
                "status_code": 200, "items": [{"name": CORDOVA, "item_type": "file"}],
                "truncated": False,
            }
            assert len(_sp_children("drive", "folder")) == 1


class TestPaginatorReportsTruncation:
    """The signal the check above depends on, at its source."""

    def _client(self, pages):
        c = MagicMock()
        c.get.side_effect = pages
        return c

    def test_complete_pagination_reports_false(self):
        from integrations.ms.sharepoint.external.client import _collect_paginated_drive_items
        client = self._client([{"value": [{"id": "1"}]}])
        items, truncated = _collect_paginated_drive_items(client, "p", operation_name="op")
        assert len(items) == 1 and truncated is False

    def test_repeated_next_link_reports_true(self):
        from integrations.ms.sharepoint.external.client import _collect_paginated_drive_items
        same = "https://graph.microsoft.com/v1.0/next"
        client = self._client([
            {"value": [{"id": "1"}], "@odata.nextLink": same},
            {"value": [{"id": "2"}], "@odata.nextLink": same},
        ])
        items, truncated = _collect_paginated_drive_items(client, "p", operation_name="op")
        assert truncated is True

    def test_page_cap_reports_true(self):
        from integrations.ms.sharepoint.external.client import _collect_paginated_drive_items
        pages = [{"value": [{"id": str(i)}],
                  "@odata.nextLink": f"https://graph.microsoft.com/v1.0/n{i}"}
                 for i in range(60)]
        client = self._client(pages)
        items, truncated = _collect_paginated_drive_items(client, "p", operation_name="op")
        assert truncated is True


class TestTwinDiagnosticDescribesTheVendor:
    """R2-P3: the message said 'at a different date', but twins are found by
    payment+total with an unrecognized VENDOR and often share the date."""

    def test_backfill_message_names_the_vendor_not_the_date(self, capsys):
        from scripts.generate_payment_remittance import run_sharepoint_backfill
        box = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch("scripts.generate_payment_remittance.assert_cli_system_admin"))
            stack.enter_context(patch("scripts.generate_payment_remittance.BoxHttpClient", return_value=box))
            stack.enter_context(patch("scripts.generate_payment_remittance.resolve_year_folder",
                                      return_value="boxyear"))
            stack.enter_context(patch("scripts.generate_payment_remittance._box_items",
                                      return_value=[{"type": "file", "name": DIAZ, "id": "f1"}]))
            stack.enter_context(patch("scripts.generate_payment_remittance.resolve_sp_year_folder",
                                      return_value="spfolder"))
            stack.enter_context(patch("scripts.generate_payment_remittance._sp_file_names",
                                      return_value=[CORDOVA]))
            run_sharepoint_backfill("2026", apply=False)
        out = capsys.readouterr().out
        assert "unrecognized vendor name" in out
        assert "different date" not in out
