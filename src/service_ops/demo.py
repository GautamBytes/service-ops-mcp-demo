import asyncio
import html
import json
import sys
from decimal import Decimal
from pathlib import Path

from mcp import Client, StdioServerParameters


def server_process(role: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "service_ops.server"],
        env={
            "SERVICE_OPS_MODE": "fixture",
            "SERVICE_OPS_ROLE": role,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
    )


async def verify_protocol() -> dict:
    async with Client(server_process("manager"), read_timeout_seconds=10) as client:
        tools = await client.list_tools()
        names = sorted(tool.name for tool in tools.tools)
        assert names == ["get_contact", "get_customer_overview", "get_invoices"]
        for tool in tools.tools:
            assert "role" not in tool.input_schema.get("properties", {})
            assert tool.annotations.read_only_hint is True
        result = await client.call_tool(
            "get_customer_overview", {"customer_key": "demo-engineering"}
        )
        assert not result.is_error, result.content
        overview = result.structured_content
        assert overview["contact"]["display_name"] == "Morgan Reed"
        assert overview["customer"]["id"] == "42"
        assert overview["balance_by_currency"] == {"GBP": "850.00"}
        assert len(overview["invoices"]) == 3
        assert overview["synthetic"] and overview["source"] == "fixture"
        assert not overview["errors"] and not overview["truncated"]
        for name in ("get_contact", "get_invoices"):
            checked = await client.call_tool(name, {"customer_key": "demo-engineering"})
            assert not checked.is_error
            assert checked.structured_content["role"] == "manager"
            if name == "get_contact":
                assert checked.structured_content["contact"]["display_name"] == "Morgan Reed"
            else:
                assert checked.structured_content["balance_by_currency"] == {"GBP": "850.00"}
                assert len(checked.structured_content["invoices"]) == 3
    async with Client(server_process("engineer"), read_timeout_seconds=10) as client:
        await client.list_tools()
        allowed = await client.call_tool("get_contact", {"customer_key": "demo-engineering"})
        assert not allowed.is_error and allowed.structured_content["role"] == "engineer"
        for name in ("get_invoices", "get_customer_overview"):
            denied = await client.call_tool(name, {"customer_key": "demo-engineering"})
            assert denied.is_error
            assert "manager role" in str(denied.content)
            assert "850" not in str(denied.content)
        escalated = await client.call_tool(
            "get_invoices", {"customer_key": "demo-engineering", "role": "manager"}
        )
        assert escalated.is_error
    return {
        "verification": "passed",
        "transport": "MCP stdio subprocess",
        "data": "synthetic HTTP fixtures through the real provider adapters",
        "live_vendor_calls": "not performed",
        "paid_model_calls": 0,
        "tools": names,
        "manager_overview": overview,
        "manager_checks": {"contact_read": "allowed", "invoices": "allowed", "overview": "allowed"},
        "engineer_checks": {
            "contact_read": "allowed",
            "invoices": "denied",
            "overview": "denied",
            "role_argument_escalation": "denied",
        },
    }


def render_report(evidence: dict) -> str:
    overview = evidence["manager_overview"]

    def escape(value):
        return html.escape(str(value))

    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(invoice[key])}</td>" for key in ("number", "currency", "total", "balance")
        )
        + "</tr>"
        for invoice in overview["invoices"]
    )
    amounts = "".join(
        f'<div class="amount">{escape(currency)} {escape(balance)}</div>'
        for currency, balance in overview["balance_by_currency"].items()
    )
    access_rows = "".join(
        f"<tr><td>{label}</td><td>{escape(evidence['manager_checks'][key]).capitalize()}</td>"
        f"<td>{escape(evidence['engineer_checks'][key]).capitalize()}</td></tr>"
        for key, label in (
            ("contact_read", "Read contact"),
            ("invoices", "Read invoices"),
            ("overview", "Combined overview"),
        )
    )
    invoice_count = len(overview["invoices"])
    settled_count = sum(Decimal(invoice["balance"]) == 0 for invoice in overview["invoices"])
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Service operations MCP demo</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f2f4f0;color:#18332d;font:16px/1.6 system-ui}}
main{{max-width:1000px;margin:64px auto;padding:0 28px}}header{{border-bottom:1px solid #c7d0c8}}
.tag{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#527060}}
h1{{font-size:44px;line-height:1.1;letter-spacing:-.04em;margin:16px 0}}
h2{{font-size:20px}}.muted{{color:#53665e}}
.cards{{display:grid;grid-template-columns:2fr 1fr;gap:20px;margin:28px 0}}
article,section{{background:white;border:1px solid #d9e1da;border-radius:12px;
padding:24px;margin:24px 0}}
.cards article{{margin:0}}.amount{{font-size:36px;font-weight:650;margin:10px 0}}
.pass{{color:#176346}}table{{width:100%;border-collapse:collapse;text-align:left}}
th,td{{padding:12px 8px;border-bottom:1px solid #e3e8e3}}th{{font-size:13px;color:#53665e}}
code{{background:#edf2ed;padding:2px 6px;border-radius:4px}}footer{{font-size:13px;color:#53665e}}
@media(max-width:640px){{main{{margin:28px auto}}h1{{font-size:32px}}
.cards{{grid-template-columns:1fr}}}}
</style><main><header><div class="tag">Local integration demo / synthetic data</div>
<h1>Service operations, through MCP.</h1>
<p class="muted">Zoho CRM contact details and QuickBooks invoices in one read-only workflow.</p>
</header>
<div class="cards"><article><div class="tag">Customer record</div>
<h2>{escape(overview["customer"]["display_name"])}</h2>
<p>{escape(overview["contact"]["display_name"])}<br>{escape(overview["contact"]["email"])}</p>
<p class="muted">Linked by configured vendor IDs. No guessed name matching.</p></article>
<article><div class="tag">Outstanding balance</div>{amounts}
<p class="muted">{invoice_count} invoices, {settled_count} settled.</p></article></div>
<section><h2>QuickBooks invoice records</h2><table><thead><tr><th>Invoice</th><th>Currency</th>
<th>Total</th><th>Balance</th></tr></thead><tbody>{rows}</tbody></table></section>
<section><h2>Access checks passed</h2><table><thead><tr>
<th>Operation</th><th>Manager</th><th>Engineer</th></tr></thead>
<tbody>{access_rows}</tbody></table>
<p class="muted">The trusted process selects the role.
Passing a manager role as a tool argument fails.</p></section>
<footer>Generated from the MCP subprocess verification in <code>evidence/demo.json</code>.
Local HTTP fixtures only. No live vendor calls, paid model calls, or customer data.
</footer></main></html>"""


def main() -> None:
    evidence = asyncio.run(verify_protocol())
    output = Path("evidence")
    output.mkdir(exist_ok=True)
    (output / "demo.json").write_text(json.dumps(evidence, indent=2) + "\n")
    (output / "report.html").write_text(render_report(evidence))
    print("PASS: MCP subprocess tools, exact invoice balance, and role denials.")
    print("Saved evidence/demo.json and evidence/report.html. Synthetic fixtures only.")


if __name__ == "__main__":
    main()
