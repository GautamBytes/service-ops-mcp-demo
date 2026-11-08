from service_ops.models import (
    ContactResult,
    CustomerLink,
    InvoiceResult,
    Mode,
    Overview,
    ProviderError,
    Role,
    balances,
)
from service_ops.providers import QuickBooks, ZohoCRM


class ServiceOps:
    def __init__(
        self,
        mode: Mode,
        role: Role,
        links: dict[str, CustomerLink],
        zoho: ZohoCRM,
        quickbooks: QuickBooks,
    ):
        self.mode = mode
        self.role = role
        self.links = links
        self.zoho = zoho
        self.quickbooks = quickbooks

    def link(self, key: str) -> CustomerLink:
        if key not in self.links:
            raise ValueError("Unknown customer key")
        return self.links[key]

    def require_manager(self) -> None:
        if self.role != Role.MANAGER:
            raise PermissionError("Financial tools require the manager role")

    async def get_contact(self, customer_key: str) -> ContactResult:
        link = self.link(customer_key)
        contact = await self.zoho.contact(link.zoho_contact_id)
        return ContactResult(
            source=self.mode, synthetic=self.mode == Mode.FIXTURE, role=self.role, contact=contact
        )

    async def get_invoices(self, customer_key: str) -> InvoiceResult:
        self.require_manager()
        link = self.link(customer_key)
        customer = await self.quickbooks.customer(link.quickbooks_customer_id)
        page = await self.quickbooks.invoices(link.quickbooks_customer_id)
        return InvoiceResult(
            source=self.mode,
            synthetic=self.mode == Mode.FIXTURE,
            role=self.role,
            customer=customer,
            invoices=page.invoices,
            balance_by_currency=balances(page.invoices),
            truncated=page.truncated,
        )

    async def get_customer_overview(self, customer_key: str) -> Overview:
        self.require_manager()
        link = self.link(customer_key)
        errors = []
        contact = None
        customer = None
        invoices = None
        totals = None
        truncated = False
        try:
            contact = await self.zoho.contact(link.zoho_contact_id)
        except ProviderError as error:
            errors.append(error.safe())
        try:
            customer = await self.quickbooks.customer(link.quickbooks_customer_id)
            page = await self.quickbooks.invoices(link.quickbooks_customer_id)
            invoices = page.invoices
            totals = balances(page.invoices)
            truncated = page.truncated
        except ProviderError as error:
            errors.append(error.safe())
        return Overview(
            source=self.mode,
            synthetic=self.mode == Mode.FIXTURE,
            role=self.role,
            customer_key=customer_key,
            contact=contact,
            customer=customer,
            invoices=invoices,
            balance_by_currency=totals,
            truncated=truncated,
            errors=errors,
        )
