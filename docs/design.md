# Why the demo uses provider clients

A service manager asks for a customer's CRM details and open invoices through MCP. The server reads a configured customer link, calls the provider clients, and returns structured results. The same provider clients run against synthetic HTTP responses in the default demo.

Each customer link contains explicit Zoho and QuickBooks record IDs. Similar names or email addresses do not establish identity. Each invoice carries its currency, and totals remain separate by currency.

The server owner selects a role when starting the process. A tool argument cannot change that role. Financial tools check the role before making provider requests. This is a local process permission example. A shared production service would also need authenticated users, tenant isolation, and an audited identity provider.

## Design comparison

Two independent design sketches compared these choices.

| Choice | Benefit | Cost |
| --- | --- | --- |
| Typed Zoho and QuickBooks clients with one service | Provider schemas and errors stay together. The service owns customer identity and permissions. | Each provider has its own small parser. |
| Generic operation registry and gateway | One registry can generate tools and policies. | Extra indirection for a small number of operations. |

The implementation uses typed provider clients. An independent design review agreed with that choice. It also recommended explicit customer links and private atomic token persistence from the registry design. Those two changes preserve the smaller interface while addressing identity mistakes and stale refresh tokens.

The Model the Domain principle guided the explicit customer-ID links and currency-bearing invoice records. The Prove It Works principle guided the real subprocess protocol check and report regression test.

## Boundaries

- MCP tools accept a configured customer key. They do not accept URLs, SQL, access tokens, or roles.
- The provider clients own fixed API routes and vendor response parsing.
- OAuth refresh is the only external POST operation. Business operations only read data.
- Synthetic HTTP responses exercise the same URL construction and parsing code as authenticated requests.
- Local protocol tests prove the MCP path. They do not prove that a vendor has accepted real credentials.

Synchroteam, speech transcription, engineering manual search, and shared web authentication are outside this first demo. No client accounts or private business data are part of this repository.
