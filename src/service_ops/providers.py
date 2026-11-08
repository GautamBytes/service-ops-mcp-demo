import asyncio
import re
from typing import Any

import httpx
from pydantic import ValidationError

from service_ops.auth import OAuth
from service_ops.models import Contact, Customer, Invoice, InvoicePage, ProviderError


class ReadClient:
    def __init__(self, provider: str, client: httpx.AsyncClient, oauth: OAuth):
        self.provider = provider
        self.client = client
        self.oauth = oauth

    async def get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        access = await self.oauth.access()
        refreshed = False
        retried = False
        while True:
            prefix = "Zoho-oauthtoken" if self.provider == "zoho" else "Bearer"
            try:
                response = await self.client.get(
                    url, params=params, headers={"Authorization": f"{prefix} {access}"}
                )
            except httpx.TimeoutException:
                raise ProviderError(self.provider, "timeout", True) from None
            except httpx.HTTPError:
                raise ProviderError(self.provider, "network_error", True) from None
            if response.status_code == 401 and not refreshed:
                access = await self.oauth.access(rejected_token=access)
                refreshed = True
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if not retried:
                    retried = True
                    try:
                        delay = min(1, max(0, float(response.headers.get("Retry-After", "0.1"))))
                    except ValueError:
                        delay = 0.1
                    await asyncio.sleep(delay)
                    continue
                code = "rate_limited" if response.status_code == 429 else "upstream_unavailable"
                raise ProviderError(self.provider, code, True)
            if response.status_code == 204 or response.status_code == 404:
                raise ProviderError(self.provider, "not_found")
            if response.status_code == 401 or response.status_code == 403:
                raise ProviderError(self.provider, "access_denied")
            if response.status_code != 200:
                raise ProviderError(self.provider, "upstream_rejected")
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("object required")
                return data
            except ValueError:
                raise ProviderError(self.provider, "invalid_response") from None


def record_id(value: str) -> str:
    if not re.fullmatch(r"[0-9]{1,30}", value):
        raise ValueError("A numeric vendor record ID is required")
    return value


class ZohoCRM:
    def __init__(self, api: ReadClient, base_url: str):
        self.api = api
        self.base_url = base_url

    async def contact(self, contact_id: str) -> Contact:
        contact_id = record_id(contact_id)
        body = await self.api.get(f"{self.base_url}/crm/v8/Contacts/{contact_id}")
        try:
            rows = body["data"]
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError("one record required")
            row = rows[0]
            account = row.get("Account_Name")
            contact = Contact(
                id=row["id"],
                display_name=row["Full_Name"],
                email=row.get("Email"),
                account_name=account["name"] if account else None,
            )
            if contact.id != contact_id:
                raise ValueError("record mismatch")
            return contact
        except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
            raise ProviderError("zoho", "invalid_response") from None


class QuickBooks:
    def __init__(self, api: ReadClient, realm_id: str, home_currency: str | None = None):
        self.api = api
        if home_currency is not None and not re.fullmatch(r"[A-Z]{3}", home_currency):
            raise ValueError("Home currency must be a three-letter currency code")
        self.home_currency = home_currency
        self.base_url = (
            f"https://sandbox-quickbooks.api.intuit.com/v3/company/{record_id(realm_id)}"
        )

    async def customer(self, customer_id: str) -> Customer:
        customer_id = record_id(customer_id)
        body = await self.api.get(f"{self.base_url}/customer/{customer_id}")
        try:
            row = body["Customer"]
            email = row.get("PrimaryEmailAddr")
            customer = Customer(
                id=row["Id"],
                display_name=row["DisplayName"],
                email=email["Address"] if email else None,
            )
            if customer.id != customer_id:
                raise ValueError("record mismatch")
            return customer
        except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
            raise ProviderError("quickbooks", "invalid_response") from None

    async def invoices(self, customer_id: str, max_pages: int = 3) -> InvoicePage:
        customer_id = record_id(customer_id)
        if not 1 <= max_pages <= 10:
            raise ValueError("max_pages must be between 1 and 10")
        invoices = []
        seen: set[str] = set()
        for page in range(max_pages):
            query = (
                f"SELECT * FROM Invoice WHERE CustomerRef = '{customer_id}' "
                f"STARTPOSITION {page * 100 + 1} MAXRESULTS 100"
            )
            body = await self.api.get(f"{self.base_url}/query", {"query": query})
            try:
                query_response = body["QueryResponse"]
                rows = query_response.get("Invoice", [])
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError("invalid page")
                for row in rows:
                    currency = row.get("CurrencyRef", {}).get("value", self.home_currency)
                    if currency is None:
                        raise ProviderError("quickbooks", "missing_currency")
                    invoice = Invoice(
                        id=row["Id"],
                        customer_id=row["CustomerRef"]["value"],
                        number=row["DocNumber"],
                        total=row["TotalAmt"],
                        balance=row["Balance"],
                        currency=currency,
                    )
                    if invoice.customer_id != customer_id or invoice.id in seen:
                        raise ValueError("unexpected invoice")
                    seen.add(invoice.id)
                    invoices.append(invoice)
                if len(rows) < 100:
                    return InvoicePage(invoices=invoices, truncated=False)
            except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
                raise ProviderError("quickbooks", "invalid_response") from None
        return InvoicePage(invoices=invoices, truncated=True)
