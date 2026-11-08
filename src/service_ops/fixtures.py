import json
import re
import time

import httpx

from service_ops.auth import Token
from service_ops.models import CustomerLink

DEMO_LINK = CustomerLink(
    key="demo-engineering", zoho_contact_id="5725767000000412001", quickbooks_customer_id="42"
)
ZOHO_BASE = "https://developer.zohoapis.in"
ZOHO_TOKEN_URL = "https://accounts.zoho.in/oauth/v2/token"
QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_BASE = "https://sandbox-quickbooks.api.intuit.com/v3/company/9341450000000000"


def fixture_token() -> Token:
    return Token("fixture-access-not-a-secret", "fixture-refresh-not-a-secret", time.time() + 3600)


def fixture_response(request: httpx.Request) -> httpx.Response:
    if request.method != "GET":
        raise AssertionError("Fixture mode only permits the expected read requests")
    expected_auth = (
        "Zoho-oauthtoken fixture-access-not-a-secret"
        if request.url.host == "developer.zohoapis.in"
        else "Bearer fixture-access-not-a-secret"
    )
    if request.headers.get("Authorization") != expected_auth:
        return httpx.Response(401, json={"error": "fixture authentication rejected"})
    url = str(request.url.copy_with(query=None))
    if url == f"{ZOHO_BASE}/crm/v8/Contacts/{DEMO_LINK.zoho_contact_id}":
        body = {
            "data": [
                {
                    "id": DEMO_LINK.zoho_contact_id,
                    "Full_Name": "Morgan Reed",
                    "Email": "morgan@example.test",
                    "Account_Name": {"name": "Demo Engineering Ltd"},
                }
            ]
        }
    elif url == f"{QB_BASE}/customer/42":
        body = {
            "Customer": {
                "Id": "42",
                "DisplayName": "Demo Engineering Ltd",
                "PrimaryEmailAddr": {"Address": "accounts@example.test"},
            }
        }
    elif url == f"{QB_BASE}/query":
        query = request.url.params.get("query", "")
        if not re.fullmatch(
            r"SELECT \* FROM Invoice WHERE CustomerRef = '42' STARTPOSITION 1 MAXRESULTS 100",
            query,
        ):
            raise AssertionError("Unexpected QuickBooks fixture query")
        body = {
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "101",
                        "DocNumber": "DEMO-101",
                        "CustomerRef": {"value": "42"},
                        "TotalAmt": "1200.00",
                        "Balance": "600.00",
                        "CurrencyRef": {"value": "GBP"},
                    },
                    {
                        "Id": "102",
                        "DocNumber": "DEMO-102",
                        "CustomerRef": {"value": "42"},
                        "TotalAmt": "250.00",
                        "Balance": "250.00",
                        "CurrencyRef": {"value": "GBP"},
                    },
                    {
                        "Id": "103",
                        "DocNumber": "DEMO-103",
                        "CustomerRef": {"value": "42"},
                        "TotalAmt": "400.00",
                        "Balance": "0.00",
                        "CurrencyRef": {"value": "GBP"},
                    },
                ],
                "startPosition": 1,
                "maxResults": 3,
            }
        }
    else:
        raise AssertionError(
            f"Unexpected fixture URL host/path: {request.url.host}{request.url.path}"
        )
    return httpx.Response(
        200, content=json.dumps(body), headers={"content-type": "application/json"}
    )
