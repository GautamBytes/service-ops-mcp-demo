import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import TypeAdapter, ValidationError

from service_ops.auth import OAuth, Token, TokenStore
from service_ops.fixtures import (
    DEMO_LINK,
    QB_TOKEN_URL,
    ZOHO_BASE,
    ZOHO_TOKEN_URL,
    fixture_response,
    fixture_token,
)
from service_ops.models import CustomerLink, Mode, Role
from service_ops.operations import ServiceOps
from service_ops.providers import QuickBooks, ReadClient, ZohoCRM, record_id

REGIONS = {
    "IN": ("https://accounts.zoho.in", "https://developer.zohoapis.in"),
    "US": ("https://accounts.zoho.com", "https://developer.zohoapis.com"),
    "EU": ("https://accounts.zoho.eu", "https://developer.zohoapis.eu"),
    "AU": ("https://accounts.zoho.com.au", "https://developer.zohoapis.com.au"),
}


@dataclass(frozen=True)
class Settings:
    mode: Mode
    role: Role
    links: dict[str, CustomerLink]
    token_dir: Path | None = None
    region: str = "IN"
    realm_id: str = "9341450000000000"
    home_currency: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            mode = Mode(os.environ.get("SERVICE_OPS_MODE", "fixture"))
            role = Role(os.environ.get("SERVICE_OPS_ROLE", "manager"))
        except ValueError:
            raise ValueError("Invalid SERVICE_OPS_MODE or SERVICE_OPS_ROLE") from None
        if mode == Mode.FIXTURE:
            return cls(mode, role, {DEMO_LINK.key: DEMO_LINK})
        region = os.environ.get("ZOHO_REGION", "IN")
        if region not in REGIONS:
            raise ValueError("ZOHO_REGION must be IN, US, EU, or AU")
        required = [
            "SERVICE_OPS_CUSTOMERS_FILE",
            "SERVICE_OPS_TOKEN_DIR",
            "ZOHO_CLIENT_ID",
            "ZOHO_CLIENT_SECRET",
            "QUICKBOOKS_CLIENT_ID",
            "QUICKBOOKS_CLIENT_SECRET",
            "QUICKBOOKS_REALM_ID",
        ]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise ValueError("Missing live configuration: " + ", ".join(missing))
        try:
            file = Path(os.environ["SERVICE_OPS_CUSTOMERS_FILE"])
            links = TypeAdapter(list[CustomerLink]).validate_python(json.loads(file.read_text()))
            if not links or len({link.key for link in links}) != len(links):
                raise ValueError("unique customer keys required")
            realm = record_id(os.environ["QUICKBOOKS_REALM_ID"])
        except (OSError, ValueError, ValidationError):
            raise ValueError("Invalid customer mapping file or QuickBooks realm ID") from None
        token_dir = Path(os.environ["SERVICE_OPS_TOKEN_DIR"]).expanduser()
        if not token_dir.is_absolute():
            raise ValueError("SERVICE_OPS_TOKEN_DIR must be an absolute private path")
        home_currency = os.environ.get("QUICKBOOKS_HOME_CURRENCY") or None
        if home_currency and (
            len(home_currency) != 3
            or not home_currency.isalpha()
            or home_currency != home_currency.upper()
        ):
            raise ValueError("QUICKBOOKS_HOME_CURRENCY must be a three-letter currency code")
        return cls(
            mode, role, {link.key: link for link in links}, token_dir, region, realm, home_currency
        )


def oauth(provider: str, client: httpx.AsyncClient, settings: Settings) -> OAuth:
    fixture = settings.mode == Mode.FIXTURE
    prefix = "ZOHO" if provider == "zoho" else "QUICKBOOKS"
    store = TokenStore(None if fixture else settings.token_dir / f"{provider}.json")
    token = fixture_token() if fixture else store.load()
    if token is None:
        refresh = os.environ.get(f"{prefix}_REFRESH_TOKEN", "")
        if not refresh:
            raise ValueError(f"Missing {prefix}_REFRESH_TOKEN and no private token file exists")
        token = Token(refresh_token=refresh)
    accounts_url = REGIONS[settings.region][0] + "/oauth/v2/token"
    endpoint = accounts_url if provider == "zoho" else QB_TOKEN_URL
    if fixture and provider == "zoho":
        endpoint = ZOHO_TOKEN_URL
    return OAuth(
        provider,
        client,
        endpoint,
        "fixture-client" if fixture else os.environ[f"{prefix}_CLIENT_ID"],
        "fixture-secret-not-a-secret" if fixture else os.environ[f"{prefix}_CLIENT_SECRET"],
        store,
        token,
    )


@asynccontextmanager
async def runtime(settings: Settings) -> AsyncIterator[ServiceOps]:
    transport = httpx.MockTransport(fixture_response) if settings.mode == Mode.FIXTURE else None
    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(10),
        follow_redirects=False,
        trust_env=False,
        headers={"Accept": "application/json"},
    ) as client:
        zoho_auth = oauth("zoho", client, settings)
        qb_auth = oauth("quickbooks", client, settings)
        base = ZOHO_BASE if settings.mode == Mode.FIXTURE else REGIONS[settings.region][1]
        yield ServiceOps(
            settings.mode,
            settings.role,
            settings.links,
            ZohoCRM(ReadClient("zoho", client, zoho_auth), base),
            QuickBooks(
                ReadClient("quickbooks", client, qb_auth), settings.realm_id, settings.home_currency
            ),
        )
