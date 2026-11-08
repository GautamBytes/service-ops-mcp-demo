import asyncio
import json
import os
import time
from urllib.parse import parse_qs

import httpx
import pytest

from service_ops.auth import OAuth, Token, TokenStore
from service_ops.fixtures import QB_TOKEN_URL, ZOHO_TOKEN_URL
from service_ops.models import ProviderError
from service_ops.providers import ReadClient
from service_ops.runtime import Settings


async def test_quickbooks_refresh_rotates_and_survives_restart(tmp_path):
    path = tmp_path / "tokens" / "quickbooks.json"
    requests = []

    def refresh(request):
        requests.append(request)
        assert str(request.url) == QB_TOKEN_URL
        assert request.headers["authorization"].startswith("Basic ")
        assert parse_qs(request.content.decode())["refresh_token"] == ["old-refresh"]
        return httpx.Response(
            200,
            json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as client:
        oauth = OAuth(
            "quickbooks",
            client,
            QB_TOKEN_URL,
            "id",
            "secret",
            TokenStore(path),
            Token(refresh_token="old-refresh"),
        )
        tokens = await asyncio.gather(*[oauth.access() for _ in range(8)])
        assert tokens == ["new-access"] * 8 and len(requests) == 1
    restarted = TokenStore(path).load()
    assert restarted.refresh_token == "new-refresh" and restarted.access_token == "new-access"
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob(".token-*"))


async def test_zoho_refresh_uses_form_credentials_and_retains_refresh_token(tmp_path):
    def refresh(request):
        assert str(request.url) == ZOHO_TOKEN_URL
        body = parse_qs(request.content.decode())
        assert body == {
            "grant_type": ["refresh_token"],
            "refresh_token": ["old-refresh"],
            "client_id": ["client-id"],
            "client_secret": ["client-secret"],
        }
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as client:
        oauth = OAuth(
            "zoho",
            client,
            ZOHO_TOKEN_URL,
            "client-id",
            "client-secret",
            TokenStore(tmp_path / "zoho.json"),
            Token(refresh_token="old-refresh"),
        )
        assert await oauth.access() == "new-access"
        assert oauth.token.refresh_token == "old-refresh"


async def test_401_refreshes_once_then_stops():
    calls = []

    def response(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                200, json={"access_token": "fresh", "refresh_token": "rotated", "expires_in": 3600}
            )
        return httpx.Response(401, text="SENTINEL-SECRET")

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        oauth = OAuth(
            "quickbooks",
            client,
            QB_TOKEN_URL,
            "id",
            "secret",
            TokenStore(None),
            Token("old", "refresh", time.time() + 3600),
        )
        with pytest.raises(ProviderError, match="access_denied"):
            await ReadClient("quickbooks", client, oauth).get("https://example.test/invoices")
    assert [request.method for request in calls] == ["GET", "POST", "GET"]
    assert calls[-1].headers["authorization"] == "Bearer fresh"


async def test_401_recovery_returns_vendor_data_after_refresh():
    def response(request):
        if request.method == "POST":
            return httpx.Response(
                200, json={"access_token": "fresh", "refresh_token": "rotated", "expires_in": 3600}
            )
        if request.headers["authorization"] == "Bearer fresh":
            return httpx.Response(200, json={"Customer": {"Id": "42"}})
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        oauth = OAuth(
            "quickbooks",
            client,
            QB_TOKEN_URL,
            "id",
            "secret",
            TokenStore(None),
            Token("old", "refresh", time.time() + 3600),
        )
        result = await ReadClient("quickbooks", client, oauth).get("https://example.test/customer")
        assert result == {"Customer": {"Id": "42"}}


@pytest.mark.parametrize(
    "body",
    [
        {"error": "SENTINEL-SECRET"},
        {"access_token": 5},
        {"access_token": "new", "expires_in": "NaN"},
    ],
)
async def test_invalid_refresh_response_is_redacted_and_preserves_saved_token(tmp_path, body):
    store = TokenStore(tmp_path / "token.json")
    store.save(Token("old", "refresh", 0))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        oauth = OAuth("zoho", client, ZOHO_TOKEN_URL, "id", "secret", store, store.load())
        with pytest.raises(ProviderError, match="oauth_refresh_failed") as captured:
            await oauth.access()
        assert "SENTINEL" not in str(captured.value)
    assert store.load().access_token == "old"


def test_world_readable_or_invalid_token_file_is_rejected(tmp_path):
    path = tmp_path / "token.json"
    path.write_text(
        json.dumps({"access_token": "secret", "refresh_token": "secret", "expires_at": "bad"})
    )
    os.chmod(path, 0o644)
    with pytest.raises(ProviderError, match="invalid_private_token_file"):
        TokenStore(path).load()
    os.chmod(path, 0o600)
    with pytest.raises(ProviderError, match="invalid_private_token_file"):
        TokenStore(path).load()


def test_live_mode_fails_closed_without_configuration(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("SERVICE_OPS_", "ZOHO_", "QUICKBOOKS_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("SERVICE_OPS_MODE", "live-readonly")
    with pytest.raises(ValueError, match="Missing live configuration"):
        Settings.from_env()


def test_unknown_role_and_mode_are_rejected(monkeypatch):
    monkeypatch.setenv("SERVICE_OPS_MODE", "production")
    with pytest.raises(ValueError, match="Invalid SERVICE_OPS_MODE"):
        Settings.from_env()
