import time
from decimal import Decimal

import httpx
import pytest

from service_ops.auth import OAuth, Token, TokenStore
from service_ops.fixtures import DEMO_LINK, QB_BASE, ZOHO_BASE, ZOHO_TOKEN_URL, fixture_response
from service_ops.models import Invoice, Mode, ProviderError, Role, balances
from service_ops.operations import ServiceOps
from service_ops.providers import QuickBooks, ReadClient, ZohoCRM
from service_ops.runtime import Settings, runtime


def api(client, provider="quickbooks"):
    auth = OAuth(
        provider,
        client,
        ZOHO_TOKEN_URL,
        "fixture",
        "fixture",
        TokenStore(None),
        Token("fixture-access-not-a-secret", "fixture", time.time() + 3600),
    )
    return ReadClient(provider, client, auth)


async def test_fixture_workflow_and_exact_balances():
    settings = Settings(Mode.FIXTURE, Role.MANAGER, {DEMO_LINK.key: DEMO_LINK})
    async with runtime(settings) as operations:
        result = await operations.get_customer_overview(DEMO_LINK.key)
        assert result.contact.display_name == "Morgan Reed"
        assert result.customer.id == "42"
        assert result.balance_by_currency == {"GBP": "850.00"}
        assert result.synthetic and not result.truncated and result.errors == []
        assert [row.balance for row in result.invoices] == [Decimal("600"), Decimal("250"), 0]


@pytest.mark.parametrize("role", [Role.ENGINEER, Role.SALES])
async def test_permission_denial_happens_before_any_provider_request(role):
    requests = []

    def record(request):
        requests.append(request)
        return fixture_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
        operations = ServiceOps(
            Mode.FIXTURE,
            role,
            {DEMO_LINK.key: DEMO_LINK},
            ZohoCRM(api(client, "zoho"), ZOHO_BASE),
            QuickBooks(api(client), "9341450000000000"),
        )
        for operation in (operations.get_invoices, operations.get_customer_overview):
            with pytest.raises(PermissionError):
                await operation(DEMO_LINK.key)
        assert requests == []
        contact = await operations.get_contact(DEMO_LINK.key)
        assert contact.contact.id == DEMO_LINK.zoho_contact_id and len(requests) == 1


@pytest.mark.parametrize(
    "payload", [[], {"data": []}, {"data": [1]}, {"data": ["bad"]}, {"data": [{"id": "wrong"}]}]
)
async def test_zoho_invalid_shape_is_a_safe_error(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError, match="invalid_response"):
            await ZohoCRM(api(client, "zoho"), ZOHO_BASE).contact(DEMO_LINK.zoho_contact_id)


@pytest.mark.parametrize(
    "payload",
    [{"Customer": []}, {"Customer": 42}, {}, {"Customer": {"Id": "99", "DisplayName": "Wrong"}}],
)
async def test_quickbooks_invalid_customer_is_a_safe_error(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError, match="invalid_response"):
            await QuickBooks(api(client), "123").customer("42")


@pytest.mark.parametrize(
    "status,code,calls",
    [
        (429, "rate_limited", 2),
        (503, "upstream_unavailable", 2),
        (403, "access_denied", 1),
        (400, "upstream_rejected", 1),
        (404, "not_found", 1),
    ],
)
async def test_http_errors_are_bounded_and_redacted(status, code, calls):
    requests = []

    def fail(request):
        requests.append(request)
        return httpx.Response(status, text="SENTINEL-SECRET", headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ProviderError) as captured:
            await api(client).get(QB_BASE + "/query")
        assert captured.value.code == code
        assert "SENTINEL" not in str(captured.value)
        assert len(requests) == calls


async def test_timeout_and_malformed_json_are_safe_errors():
    def timeout(request):
        raise httpx.ReadTimeout("SENTINEL-SECRET", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderError, match="timeout") as captured:
            await api(client).get(QB_BASE + "/query")
        assert "SENTINEL" not in str(captured.value)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="not-json SENTINEL-SECRET")
        )
    ) as client:
        with pytest.raises(ProviderError, match="invalid_response"):
            await api(client).get(QB_BASE + "/query")


def invoice_row(identifier, currency="GBP"):
    return {
        "Id": str(identifier),
        "CustomerRef": {"value": "42"},
        "DocNumber": str(identifier),
        "TotalAmt": "12.10",
        "Balance": "0.10",
        "CurrencyRef": {"value": currency},
    }


async def test_pagination_advances_and_stops_without_losing_invoices():
    queries = []

    def page(request):
        query = request.url.params["query"]
        queries.append(query)
        start = 1 if "STARTPOSITION 1 " in query else 101
        count = 100 if start == 1 else 1
        return httpx.Response(
            200,
            json={
                "QueryResponse": {
                    "Invoice": [invoice_row(index) for index in range(start, start + count)]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(page)) as client:
        result = await QuickBooks(api(client), "123").invoices("42")
        assert len(result.invoices) == 101 and not result.truncated
        assert "STARTPOSITION 101" in queries[1] and len(queries) == 2


async def test_page_limit_is_visible_and_queries_cannot_be_injected():
    requests = []

    def page(request):
        requests.append(request)
        return httpx.Response(
            200, json={"QueryResponse": {"Invoice": [invoice_row(index) for index in range(100)]}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(page)) as client:
        qb = QuickBooks(api(client), "123")
        result = await qb.invoices("42", max_pages=1)
        assert len(result.invoices) == 100 and result.truncated
        with pytest.raises(ValueError):
            await qb.invoices("42' OR '1'='1")
        assert len(requests) == 1


async def test_unknown_currency_requires_explicit_configuration():
    row = invoice_row(1)
    row.pop("CurrencyRef")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"QueryResponse": {"Invoice": [row]}})
        )
    ) as client:
        with pytest.raises(ProviderError, match="missing_currency"):
            await QuickBooks(api(client), "123").invoices("42")
        result = await QuickBooks(api(client), "123", home_currency="GBP").invoices("42")
        assert result.invoices[0].currency == "GBP"


def test_decimal_totals_keep_currencies_separate():
    invoices = [
        Invoice(id="1", customer_id="42", number="A", total="0.30", balance="0.10", currency="GBP"),
        Invoice(id="2", customer_id="43", number="B", total="0.30", balance="0.20", currency="GBP"),
        Invoice(id="3", customer_id="44", number="C", total="1.00", balance="0.90", currency="USD"),
    ]
    assert balances(invoices) == {"GBP": "0.30", "USD": "0.90"}


async def test_partial_overview_does_not_fabricate_zero_balance():
    def response(request):
        if request.url.host == "sandbox-quickbooks.api.intuit.com":
            return httpx.Response(503, headers={"Retry-After": "0"}, text="SENTINEL-SECRET")
        return fixture_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        operations = ServiceOps(
            Mode.FIXTURE,
            Role.MANAGER,
            {DEMO_LINK.key: DEMO_LINK},
            ZohoCRM(api(client, "zoho"), ZOHO_BASE),
            QuickBooks(api(client), "9341450000000000"),
        )
        result = await operations.get_customer_overview(DEMO_LINK.key)
        assert result.contact.display_name == "Morgan Reed"
        assert result.invoices is None and result.balance_by_currency is None
        assert result.errors[0].code == "upstream_unavailable"
        assert "SENTINEL" not in result.model_dump_json()
