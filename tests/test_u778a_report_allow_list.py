from integrations.intuit.qbo.report.external.client import ALLOWED_REPORTS


def test_unbilled_charges_not_allowed():
    assert "UnbilledCharges" not in ALLOWED_REPORTS  # QBO has no such report ("Permission Denied")


def test_other_reports_still_allowed():
    assert "ProfitAndLoss" in ALLOWED_REPORTS
