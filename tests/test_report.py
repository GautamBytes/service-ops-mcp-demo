import json
from pathlib import Path

import pytest

from service_ops.demo import render_report


@pytest.fixture
def evidence():
    return json.loads((Path(__file__).parents[1] / "evidence/demo.json").read_text())


def test_quickbooks_failure_preserves_contact_and_marks_invoices_unavailable(evidence):
    evidence["manager_overview"].update(
        customer=None, invoices=None, balance_by_currency=None,
        errors=[{"provider": "quickbooks", "code": "upstream_unavailable", "retryable": True}],
    )
    report = render_report(evidence)
    assert "Morgan Reed" in report and "morgan@example.test" in report
    assert "Customer details unavailable" in report
    assert "Invoice data unavailable" in report and "upstream_unavailable" in report
    assert "GBP 0.00" not in report and "0 invoices" not in report


def test_zoho_failure_preserves_customer_and_invoices(evidence):
    evidence["manager_overview"].update(
        contact=None,
        errors=[{"provider": "zoho", "code": "timeout", "retryable": True}],
    )
    report = render_report(evidence)
    assert "Demo Engineering Ltd" in report and "GBP 850.00" in report
    assert "Contact details unavailable" in report
    assert "DEMO-101" in report and "timeout" in report


def test_both_provider_failures_do_not_invent_zero_balance(evidence):
    evidence["manager_overview"].update(
        contact=None, customer=None, invoices=None, balance_by_currency=None,
        errors=[
            {"provider": "zoho", "code": "timeout", "retryable": True},
            {"provider": "quickbooks", "code": "access_denied", "retryable": False},
        ],
    )
    report = render_report(evidence)
    assert "Contact details unavailable" in report and "Customer details unavailable" in report
    assert "Invoice data unavailable" in report
    assert "timeout" in report and "access_denied" in report
    assert "GBP 0.00" not in report and "0 invoices" not in report


def test_empty_successful_invoice_result_is_distinct_from_unavailable(evidence):
    evidence["manager_overview"].update(invoices=[], balance_by_currency={})
    report = render_report(evidence)
    assert "No invoices found" in report and "No outstanding invoices" in report
    assert "Invoice data unavailable" not in report and "GBP 0.00" not in report


def test_truncated_invoice_result_labels_totals_as_partial(evidence):
    evidence["manager_overview"]["truncated"] = True
    report = render_report(evidence)
    assert "Partial outstanding balance" in report
    assert "Incomplete invoice data" in report and "GBP 850.00" in report
    assert "complete customer balance" in report


def test_provider_error_fields_are_escaped_and_unknown_fields_not_rendered(evidence):
    evidence["manager_overview"]["errors"] = [{
        "provider": '<img src="x" onerror="bad">',
        "code": "<script>bad()</script>", "retryable": True,
        "raw_body": "SENTINEL-SECRET",
    }]
    report = render_report(evidence)
    assert "&lt;img" in report and "&lt;script&gt;" in report
    assert '<img src="x"' not in report and "<script>" not in report
    assert "SENTINEL-SECRET" not in report


def test_complete_report_keeps_customer_balance_and_invoice_records(evidence):
    report = render_report(evidence)
    assert "Morgan Reed" in report and "Demo Engineering Ltd" in report
    assert "GBP 850.00" in report and "DEMO-103" in report
    assert "Invoice data unavailable" not in report and "Incomplete invoice data" not in report
