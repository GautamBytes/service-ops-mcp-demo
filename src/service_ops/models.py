from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

RecordId = Annotated[str, Field(pattern=r"^[0-9]{1,30}$")]
Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class Mode(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live-readonly"


class Role(StrEnum):
    SALES = "sales"
    ENGINEER = "engineer"
    MANAGER = "manager"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CustomerLink(Model):
    key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
    zoho_contact_id: RecordId
    quickbooks_customer_id: RecordId


class Contact(Model):
    id: RecordId
    display_name: str
    email: str | None
    account_name: str | None


class Customer(Model):
    id: RecordId
    display_name: str
    email: str | None


class Invoice(Model):
    id: RecordId
    customer_id: RecordId
    number: str
    total: Money
    balance: Money
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class InvoicePage(Model):
    invoices: list[Invoice]
    truncated: bool


class SafeError(Model):
    provider: str
    code: str
    retryable: bool


class ContactResult(Model):
    source: Mode
    synthetic: bool
    role: Role
    contact: Contact


class InvoiceResult(Model):
    source: Mode
    synthetic: bool
    role: Role
    customer: Customer
    invoices: list[Invoice]
    balance_by_currency: dict[str, str]
    truncated: bool


class Overview(Model):
    source: Mode
    synthetic: bool
    role: Role
    customer_key: str
    contact: Contact | None
    customer: Customer | None
    invoices: list[Invoice] | None
    balance_by_currency: dict[str, str] | None
    truncated: bool
    errors: list[SafeError]


@dataclass
class ProviderError(Exception):
    provider: str
    code: str
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.provider}: {self.code}"

    def safe(self) -> SafeError:
        return SafeError(provider=self.provider, code=self.code, retryable=self.retryable)


def balances(invoices: list[Invoice]) -> dict[str, str]:
    totals: dict[str, Decimal] = {}
    for invoice in invoices:
        totals[invoice.currency] = totals.get(invoice.currency, Decimal(0)) + invoice.balance
    return {currency: format(value, ".2f") for currency, value in sorted(totals.items())}
