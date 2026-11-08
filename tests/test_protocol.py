import copy

from service_ops.demo import render_report, verify_protocol


async def test_real_mcp_stdio_protocol_and_roles():
    evidence = await verify_protocol()
    assert evidence["verification"] == "passed"
    assert evidence["engineer_checks"]["role_argument_escalation"] == "denied"
    assert evidence["live_vendor_calls"] == "not performed"
    report = render_report(evidence)
    assert "GBP 850.00" in report and "synthetic data" in report
    assert "<script" not in report and "<iframe" not in report
    changed = copy.deepcopy(evidence)
    changed["manager_overview"]["customer"]["display_name"] = '<img src="x" onerror="bad">'
    changed["manager_overview"]["balance_by_currency"] = {"USD": "19.75"}
    changed["manager_overview"]["invoices"] = changed["manager_overview"]["invoices"][:1]
    changed["engineer_checks"]["invoices"] = "test-status"
    changed_report = render_report(changed)
    assert "USD 19.75" in changed_report and "GBP 850.00" not in changed_report
    assert "1 invoices, 0 settled" in changed_report and "Test-status" in changed_report
    assert "&lt;img" in changed_report and '<img src="x"' not in changed_report
