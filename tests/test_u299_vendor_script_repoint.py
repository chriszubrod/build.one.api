"""Pure-logic tests for U-299/U-313: the last two raw-SQL vendor-ref
consumers (scripts/generate_payment_remittance.py, scripts/backfill_qbo_bills.py),
repointed by U-299 off qbo.Vendor/qbo.VendorVendor onto dbo.Vendor's native
QboId (dbo-first, verified via identity_consistency.py::verify_vendor_qbo_identity,
falling back to the legacy 2-hop on a miss/disagreement), then moved fully
dbo-only by U-313 (Wave 5's "trust dbo alone" plan, `docs/design/wave5.md`) —
`verify_identity_dbo_only`, no legacy fallback left (it had no data source
left either once `qbo.VendorVendor` stopped being written). Mirrors
tests/test_u284v_vendor_fanout_repoint.py's own U-313 update.
`resolve_local_vendor_id()` is hand-copied into both scripts (not extracted), per that
unit's precedent, so it is tested identically against both modules below.

Findings from the U-299 Workflow hunt (Codex was out-of-credits at both xhigh/gpt-5.5 and
high/gpt-5.4, so a Claude multi-lens adversarial hunt ran as the fallback) covered here as
regression tests:
  - P1: apply_backfill() gained a required realm_id positional -> fixed at its one call site
    in tests/test_sync_qbo_attachment_refusal_propagation.py (that test IS the regression test).
  - P2: resolve_local_vendor_id()'s legacy fallback trusted a VendorVendor mapping pointing at
    a since-soft-deleted Vendor row with no existence check -> test_legacy_mapping_points_at_deleted_vendor_returns_none.
  - P2: backfill_qbo_bills.py's dry-run path started hard-requiring a stored QboAuth row (a
    behavior change from its documented "SAFE BY DEFAULT... READ-ONLY" no-auth-needed contract)
    -> TestMainRealmIdResolution.
  - P2: select_unmapped()'s dbo-first override silently rewrote a row's MappedVendorId with zero
    observability when it actually disagreed with (not just filled a NULL in) the legacy chain
    -> test_disagreement_logs_warning / test_null_fill_does_not_log_warning.
  - P3: _classify_bucket()'s blank-DocNumber guard over-stripped vs SQL Server's space-only
    LTRIM/RTRIM -> superseded by the /simplify pass below (see next paragraph); the parity
    concern is now structurally eliminated rather than patched.

Post-hunt /simplify pass (reuse/simplification/efficiency/altitude, behavior-preserving)
additionally: (a) repointed local_vendor_and_contact_emails()'s raw Contact SQL onto the
existing ContactService.read_by_vendor_id() (this repo's "all DB access via stored
procedures" convention — the sibling write path two functions below already used it);
(b) rewrote _classify_bucket() to run SQL Server's own CASE (not a hand-ported Python
mirror of it) so the SQL/Python bucket-rule duplication that caused the P3 whitespace bug
can't recur by construction; (c) moved the realm_id hard-requirement back into
apply_backfill() itself (self-defending, as it was pre-diff) so main()'s fallback can stay
uniformly soft instead of a `if args.apply: raise` special case.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest

import scripts.backfill_qbo_bills as backfill_mod
import scripts.generate_payment_remittance as remit_mod

sys.path.insert(0, str(Path(__file__).resolve().parent))

RESOLVER_MODULES = [remit_mod, backfill_mod]
RESOLVER_IDS = ["generate_payment_remittance", "backfill_qbo_bills"]


# --- Section 1: resolve_local_vendor_id (hand-copied in both scripts) ---


@pytest.mark.parametrize("mod", RESOLVER_MODULES, ids=RESOLVER_IDS)
class TestResolveLocalVendorId:
    def test_verified_direct_hit(self, mod):
        vendor_service = Mock()
        direct_vendor = SimpleNamespace(id=10, qbo_id="QV-1", realm_id="realm-1")
        vendor_service.read_by_qbo_identity.return_value = direct_vendor  # same row both calls

        result = mod.resolve_local_vendor_id("QV-1", "realm-1", vendor_service=vendor_service)

        assert result == 10
        assert vendor_service.read_by_qbo_identity.call_count == 2
        vendor_service.read_by_qbo_identity.assert_called_with("QV-1", "realm-1")

    def test_dbo_miss_returns_none(self, mod):
        vendor_service = Mock()
        vendor_service.read_by_qbo_identity.return_value = None

        result = mod.resolve_local_vendor_id("QV-1", "realm-1", vendor_service=vendor_service)

        assert result is None
        assert vendor_service.read_by_qbo_identity.call_count == 1  # verify never reached

    def test_stolen_identity_refuses_and_returns_none(self, mod):
        """Regression (mirrors test_u284v's own U-313 sibling): a fresh
        re-read that no longer resolves back to the same row must be
        refused, not trusted — there is no legacy mapping-table hop left to
        fall back to."""
        vendor_service = Mock()
        direct_vendor = SimpleNamespace(id=10, qbo_id="QV-1", realm_id="realm-1")
        stolen = SimpleNamespace(id=99, qbo_id="QV-1", realm_id="realm-1")
        vendor_service.read_by_qbo_identity.side_effect = [direct_vendor, stolen]

        result = mod.resolve_local_vendor_id("QV-1", "realm-1", vendor_service=vendor_service)

        assert result is None

    def test_dbo_miss_on_empty_ref_returns_none(self, mod):
        """`resolve_local_vendor_id` has no explicit falsy short-circuit of its
        own (unlike the connector-level resolvers) — an empty ref value just
        flows straight into `read_by_qbo_identity`, which resolves nothing."""
        vendor_service = Mock()
        vendor_service.read_by_qbo_identity.return_value = None

        result = mod.resolve_local_vendor_id("", "realm-1", vendor_service=vendor_service)

        assert result is None
        vendor_service.read_by_qbo_identity.assert_called_once_with("", "realm-1")


# --- Section 2: generate_payment_remittance.py::local_vendor_and_contact_emails ---


class TestLocalVendorAndContactEmails:
    def test_resolves_vendor_then_looks_up_contacts(self):
        contacts = [
            SimpleNamespace(email="a@x.com"),
            SimpleNamespace(email=" b@y.com "),
            SimpleNamespace(email=None),
            SimpleNamespace(email=""),
        ]
        with patch.object(remit_mod, "resolve_local_vendor_id", return_value=42) as mock_resolve, \
             patch("entities.contact.business.service.ContactService") as mock_contact_cls:
            mock_contact_cls.return_value.read_by_vendor_id.return_value = contacts
            local_id, emails = remit_mod.local_vendor_and_contact_emails("QV-1", "realm-1")

        assert local_id == 42
        assert emails == ["a@x.com", "b@y.com"]
        mock_resolve.assert_called_once_with("QV-1", "realm-1")
        mock_contact_cls.return_value.read_by_vendor_id.assert_called_once_with(42)

    def test_unresolved_vendor_skips_contact_lookup_entirely(self):
        with patch.object(remit_mod, "resolve_local_vendor_id", return_value=None), \
             patch("entities.contact.business.service.ContactService") as mock_contact_cls:
            result = remit_mod.local_vendor_and_contact_emails("QV-1", "realm-1")

        assert result == (None, [])
        mock_contact_cls.assert_not_called()

    def test_resolved_vendor_with_zero_contacts(self):
        with patch.object(remit_mod, "resolve_local_vendor_id", return_value=42), \
             patch("entities.contact.business.service.ContactService") as mock_contact_cls:
            mock_contact_cls.return_value.read_by_vendor_id.return_value = []
            result = remit_mod.local_vendor_and_contact_emails("QV-1", "realm-1")

        assert result == (42, [])


def test_resolve_vendor_emails_threads_realm_id():
    payment = {"vendor_qbo_id": "QV-1", "vendor": "Acme", "qbo_email": None}
    with patch.object(remit_mod, "local_vendor_and_contact_emails", return_value=(42, [])) as mock_lookup:
        remit_mod.resolve_vendor_emails(payment, {}, "realm-9")

    mock_lookup.assert_called_once_with("QV-1", "realm-9")


# --- Section 3: backfill_qbo_bills.py::select_unmapped's dbo-first override ---


_COLUMNS = ["Id", "QboId", "VendorRefValue", "VendorRefName", "DocNumber", "TxnDate",
            "MappedVendorId", "bucket"]


def _row(**overrides):
    base = {
        "Id": 1, "QboId": "1001", "VendorRefValue": "V1", "VendorRefName": "Acme",
        "DocNumber": "B-100", "TxnDate": "2026-01-01", "MappedVendorId": None,
        "bucket": "unmapped_vendor",
    }
    base.update(overrides)
    return tuple(base[c] for c in _COLUMNS)


def _cursor_for(rows):
    cur = MagicMock()
    cur.description = [(c,) for c in _COLUMNS]
    cur.fetchall.return_value = rows
    return cur


class TestSelectUnmappedVendorRepoint:
    def test_flips_unmapped_vendor_bucket_when_dbo_resolves(self):
        """The core motivating scenario: legacy join misses (dbo.Vendor.QboId
        stamped but qbo.VendorVendor mapping never created / lost)."""
        cur = _cursor_for([_row(Id=1, VendorRefValue="V1", MappedVendorId=None,
                                bucket="unmapped_vendor")])
        cur.fetchone.return_value = (backfill_mod.CREATABLE,)  # SQL-computed bucket

        with patch("scripts.backfill_qbo_bills.get_connection") as mock_conn_ctx, \
             patch.object(backfill_mod, "resolve_local_vendor_id", return_value=55) as mock_resolve:
            mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cur
            rows = backfill_mod.select_unmapped(realm_id="realm-1")

        assert rows[0]["MappedVendorId"] == 55
        assert rows[0]["bucket"] == backfill_mod.CREATABLE
        mock_resolve.assert_called_once()
        assert mock_resolve.call_args.args[:2] == ("V1", "realm-1")

    def test_agreeing_resolution_skips_recompute_and_caches_by_ref(self):
        rows_in = [
            _row(Id=1, VendorRefValue="V1", MappedVendorId=55, bucket="already_exists_unlinked"),
            _row(Id=2, VendorRefValue="V1", MappedVendorId=55, bucket="already_exists_unlinked"),
        ]
        cur = _cursor_for(rows_in)

        with patch("scripts.backfill_qbo_bills.get_connection") as mock_conn_ctx, \
             patch.object(backfill_mod, "resolve_local_vendor_id", return_value=55) as mock_resolve, \
             patch.object(backfill_mod, "_classify_bucket") as mock_classify:
            mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cur
            rows = backfill_mod.select_unmapped(realm_id="realm-1")

        assert mock_resolve.call_count == 1  # cached across both rows sharing VendorRefValue
        mock_classify.assert_not_called()
        assert all(r["bucket"] == "already_exists_unlinked" for r in rows)

    def test_both_miss_stays_unmapped_vendor_no_recompute(self):
        cur = _cursor_for([_row(Id=1, VendorRefValue="V1", MappedVendorId=None,
                                bucket="unmapped_vendor")])

        with patch("scripts.backfill_qbo_bills.get_connection") as mock_conn_ctx, \
             patch.object(backfill_mod, "resolve_local_vendor_id", return_value=None), \
             patch.object(backfill_mod, "_classify_bucket") as mock_classify:
            mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cur
            rows = backfill_mod.select_unmapped(realm_id="realm-1")

        assert rows[0]["bucket"] == "unmapped_vendor"
        mock_classify.assert_not_called()

    def test_dbo_miss_with_nonnull_legacy_mapped_vendor_id_preserves_it(self):
        """Codex P2 (U-313 review): a dbo-first miss (resolve_local_vendor_id
        returns None -- no legacy fallback left inside it as of U-313) must
        leave an ALREADY non-null MappedVendorId/bucket exactly as this
        script's own raw SQL join computed it, not blank it out or corrupt
        it. This SQL join (a separate, out-of-scope mechanism -- see the
        module docstring) is this script's own legacy-hop bucketing logic,
        unaffected by U-313's Python-side resolver change; this test pins
        that the override logic here (untouched by this diff) still composes
        correctly with the resolver's new no-fallback contract."""
        cur = _cursor_for([_row(Id=1, VendorRefValue="V1", MappedVendorId=77,
                                bucket="already_exists_unlinked")])

        with patch("scripts.backfill_qbo_bills.get_connection") as mock_conn_ctx, \
             patch.object(backfill_mod, "resolve_local_vendor_id", return_value=None), \
             patch.object(backfill_mod, "_classify_bucket") as mock_classify:
            mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cur
            rows = backfill_mod.select_unmapped(realm_id="realm-1")

        assert rows[0]["MappedVendorId"] == 77
        assert rows[0]["bucket"] == "already_exists_unlinked"
        mock_classify.assert_not_called()

    def test_disagreement_logs_warning(self):
        """Regression (Workflow hunt P2): overriding a NON-null MappedVendorId
        (a real disagreement, not an ordinary NULL-fill) must be observable."""
        cur = _cursor_for([_row(Id=1, QboId="1001", VendorRefValue="V1", MappedVendorId=77,
                                bucket="already_exists_unlinked")])
        cur.fetchone.return_value = (backfill_mod.CREATABLE,)  # dbo-resolved vendor 55 no longer matches

        with patch("scripts.backfill_qbo_bills.get_connection") as mock_conn_ctx, \
             patch.object(backfill_mod, "resolve_local_vendor_id", return_value=55), \
             patch.object(backfill_mod, "logger") as mock_logger:
            mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cur
            rows = backfill_mod.select_unmapped(realm_id="realm-1")

        assert rows[0]["MappedVendorId"] == 55
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args.args[0]
        assert "77" in msg and "55" in msg

    def test_null_fill_does_not_log_warning(self):
        """Filling a NULL (the ordinary not-yet-mapped case) is not a
        disagreement -- must not be logged as one."""
        cur = _cursor_for([_row(Id=1, VendorRefValue="V1", MappedVendorId=None,
                                bucket="unmapped_vendor")])
        cur.fetchone.return_value = (backfill_mod.CREATABLE,)

        with patch("scripts.backfill_qbo_bills.get_connection") as mock_conn_ctx, \
             patch.object(backfill_mod, "resolve_local_vendor_id", return_value=55), \
             patch.object(backfill_mod, "logger") as mock_logger:
            mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cur
            backfill_mod.select_unmapped(realm_id="realm-1")

        mock_logger.warning.assert_not_called()


# --- Section 4: backfill_qbo_bills.py::_classify_bucket (isolated) ---


class TestClassifyBucket:
    """_classify_bucket() is a thin passthrough to SQL Server's own CASE (not
    a hand-ported Python mirror of it) -- these tests pin the param order/
    binding, not a re-implementation of the blank/date/exists logic itself
    (that would just be re-copying the SQL as a Python assertion)."""

    def test_returns_whatever_sql_computes(self):
        cur = MagicMock()
        cur.fetchone.return_value = ("null_docnumber",)
        assert backfill_mod._classify_bucket(cur, 1, None, "2026-01-01") == "null_docnumber"

        cur.fetchone.return_value = ("already_exists_unlinked",)
        assert backfill_mod._classify_bucket(cur, 1, "B-1", "2026-01-01") == "already_exists_unlinked"

        cur.fetchone.return_value = (backfill_mod.CREATABLE,)
        assert backfill_mod._classify_bucket(cur, 1, "B-1", "2026-01-01") == backfill_mod.CREATABLE

    def test_doc_number_passed_through_verbatim_no_python_stripping(self):
        """Regression: the blank-DocNumber rule must stay SQL-side (SQL
        Server's LTRIM/RTRIM strip spaces only; a bare Python .strip() once
        stripped more and diverged from it) -- _classify_bucket must bind
        doc_number as-is and let SQL decide, never pre-transform it."""
        cur = MagicMock()
        cur.fetchone.return_value = (backfill_mod.CREATABLE,)
        backfill_mod._classify_bucket(cur, 1, "\t", "2026-01-01")

        params = cur.execute.call_args.args[1]
        assert params == ("\t", "\t", 1, "\t", "2026-01-01")

    def test_param_order_matches_placeholders(self):
        cur = MagicMock()
        cur.fetchone.return_value = (backfill_mod.CREATABLE,)
        backfill_mod._classify_bucket(cur, 42, "B-1", "2026-01-01")

        cur.execute.assert_called_once_with(ANY, ("B-1", "B-1", 42, "B-1", "2026-01-01"))


# --- Section 5: backfill_qbo_bills.py's realm_id resolution/enforcement ---


class TestMainRealmIdResolution:
    def test_dry_run_falls_back_when_no_qbo_auth(self, monkeypatch):
        """Regression (Workflow hunt P2): dry-run is documented 'SAFE BY
        DEFAULT... READ-ONLY' and previously had zero QBO-auth dependency --
        it must not hard-crash just because vendor-ref resolution now wants
        a realm_id."""
        monkeypatch.setattr(sys, "argv", ["backfill_qbo_bills.py"])

        with patch.object(backfill_mod, "assert_cli_system_admin"), \
             patch.object(backfill_mod, "QboAuthService") as mock_auth_cls, \
             patch.object(backfill_mod, "select_unmapped", return_value=[]) as mock_select:
            mock_auth_cls.return_value.resolve_realm_id.side_effect = ValueError("no auth")
            backfill_mod.main()  # must NOT raise

        mock_select.assert_called_once()
        assert mock_select.call_args.kwargs.get("realm_id") is None

    def test_dry_run_uses_resolved_realm_id_when_auth_present(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["backfill_qbo_bills.py"])

        with patch.object(backfill_mod, "assert_cli_system_admin"), \
             patch.object(backfill_mod, "QboAuthService") as mock_auth_cls, \
             patch.object(backfill_mod, "select_unmapped", return_value=[]) as mock_select:
            mock_auth_cls.return_value.resolve_realm_id.return_value = "realm-live"
            backfill_mod.main()

        assert mock_select.call_args.kwargs.get("realm_id") == "realm-live"

    def test_apply_backfill_raises_without_realm_id(self):
        """apply_backfill() is self-defending (matches pre-U-299 behavior,
        restored by the /simplify altitude pass): a direct caller that omits
        realm_id must fail fast with a clear message, not deep inside the
        pull pipeline."""
        with pytest.raises(ValueError, match="No QBO authentication"):
            backfill_mod.apply_backfill([{"bucket": backfill_mod.CREATABLE}], None, False, None)

    def test_apply_reraises_when_no_qbo_auth_end_to_end(self, monkeypatch):
        """main() stays uniformly soft (no args.apply special-case); it's
        apply_backfill()'s own guard that surfaces the failure."""
        monkeypatch.setattr(sys, "argv", ["backfill_qbo_bills.py", "--apply"])

        with patch.object(backfill_mod, "assert_cli_system_admin"), \
             patch.object(backfill_mod, "QboAuthService") as mock_auth_cls, \
             patch.object(backfill_mod, "select_unmapped",
                          return_value=[{"bucket": backfill_mod.CREATABLE}]):
            mock_auth_cls.return_value.resolve_realm_id.side_effect = ValueError("no auth")
            with pytest.raises(ValueError, match="No QBO authentication"):
                backfill_mod.main()
