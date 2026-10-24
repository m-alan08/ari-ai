"""In-memory store for shop-lite. Seeded synthetic data; reset per app instance."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Customer:
    id: str
    name: str
    email: str


@dataclass
class Session:
    token: str
    customer_id: str
    created_epoch: float
    max_age_s: float


@dataclass
class Order:
    id: str
    customer_id: str
    sku: str
    amount: Decimal
    currency: str
    status: str


@dataclass
class Invoice:
    id: str
    customer_id: str
    order_id: str
    amount: Decimal
    paid: Decimal


@dataclass
class InvoicePayment:
    id: str
    invoice_id: str
    customer_id: str
    amount: Decimal


@dataclass
class Store:
    customers: dict[str, Customer] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)  # bearer token -> session
    orders: dict[str, Order] = field(default_factory=dict)
    invoices: dict[str, Invoice] = field(default_factory=dict)
    payments: dict[str, InvoicePayment] = field(default_factory=dict)
    _counter: int = 0

    def next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:04d}"

    def payments_for(self, invoice_id: str) -> list[InvoicePayment]:
        return [p for p in self.payments.values() if p.invoice_id == invoice_id]


def seeded_store() -> Store:
    """Two synthetic customers with obviously fake tokens (fixture data, not secrets)."""
    s = Store()
    s.customers = {
        "cus_alice": Customer("cus_alice", "Alice Example", "alice@example.test"),
        "cus_bob": Customer("cus_bob", "Bob Example", "bob@example.test"),
    }
    now = time.time()
    s.sessions = {
        "fixture-token-alice": Session("fixture-token-alice", "cus_alice", now, 86_400.0),
        "fixture-token-bob": Session("fixture-token-bob", "cus_bob", now, 86_400.0),
    }
    s.orders = {
        "ord_a1": Order("ord_a1", "cus_alice", "sku-chair", Decimal("120.00"), "USD", "placed"),
        "ord_a2": Order("ord_a2", "cus_alice", "sku-lamp", Decimal("15.50"), "USD", "shipped"),
        "ord_b1": Order("ord_b1", "cus_bob", "sku-desk", Decimal("80.00"), "USD", "placed"),
    }
    s.invoices = {
        "inv_a1": Invoice("inv_a1", "cus_alice", "ord_a1", Decimal("120.00"), Decimal("40.00")),
        "inv_b1": Invoice("inv_b1", "cus_bob", "ord_b1", Decimal("80.00"), Decimal("0.00")),
    }
    return s
