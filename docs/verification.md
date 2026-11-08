# Verification status

This demo separates local execution from authenticated vendor testing.

## Local checks

Verified on 9 November 2026 with Python 3.14.2 and MCP Python SDK 2.3.0.

| Check | Observed result |
| --- | --- |
| `python -m pytest -q` | 42 tests passed. |
| `ruff check .` | Passed. |
| `ruff format --check .` | 17 files already formatted. |
| `python -m service_ops.demo` | Passed the actual stdio subprocess tool calls and role checks. |
| Browser inspection of the generated report | Complete, missing-provider and incomplete-invoice reports rendered correctly. Available records remained visible and incomplete totals carried a warning. |

The verification commands are documented in the README. The protocol journey starts a real MCP stdio subprocess and calls its tools through the official Python SDK. HTTP fixtures provide the vendor responses. No language model or paid API is part of that journey.

Report regression tests cover missing Zoho data, missing QuickBooks data, an invoice query failure after a successful customer read, both providers failing, a successful empty invoice list, incomplete totals, and escaped error text. Before the fix, the first seven focused tests produced `6 failed, 1 passed`. After the fix and one additional invoice-query case, all eight report tests pass.

The saved synthetic customer summary is in [demo.json](../evidence/demo.json). The HTML report renders that returned summary. Rerun the demo command to regenerate both files.

## Live checks

Zoho Developer Edition and QuickBooks sandbox authentication have not been exercised with real credentials. No claim of successful live API access is made.

The code implements fixed read routes and token refresh. The initial OAuth authorization and token provisioning still require the operator's developer accounts. Only one process may use a token directory at a time.

## What this does not verify

- Production accounts or real customer data.
- Claude prompt quality or a Claude-hosted dashboard.
- Synchroteam integration, audio transcription, coaching, or engineering manual search.
- Shared multi-user login, tenant isolation, distributed token refresh, or production deployment.

No third-party evaluation, employer approval, or client use is implied by the local test results.

Browser proof uses synthetic scenarios from the report regression tests. View the [missing-provider report](../evidence/report-partial-preview.jpg) and [incomplete-invoice report](../evidence/report-incomplete-preview.jpg).
