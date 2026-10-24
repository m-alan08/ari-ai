"""HTTP API for shop-lite.

Intended behavior (the platform should discover these from docstrings, tests and
conventions):

- A customer can only read their own profile, orders and invoices.
- Session tokens older than their max age must be rejected.
- Invoice payments may never exceed the invoice amount in total.
- Product search is read-only and never runs caller-supplied SQL.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from shop.db import Catalogue
from shop.store import Customer, Invoice, Order, Session, Store, seeded_store


class OrderIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")


class OrderOut(BaseModel):
    id: str
    customer_id: str
    sku: str
    amount: Decimal
    currency: str
    status: str


class InvoiceOut(BaseModel):
    id: str
    customer_id: str
    order_id: str
    amount: Decimal
    paid: Decimal


class PaymentIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    note: str = Field(default="", max_length=200)


class PaymentOut(BaseModel):
    id: str
    invoice_id: str
    amount: Decimal


class CustomerOut(BaseModel):
    id: str
    name: str
    email: str


class ProductOut(BaseModel):
    sku: str
    name: str
    price: Decimal


class SessionOut(BaseModel):
    customer_id: str
    issued_epoch: float
    max_age_s: float


def create_app(store: Store | None = None, catalogue: Catalogue | None = None) -> FastAPI:
    app = FastAPI(title="shop-lite", version="0.1.0")
    app.state.store = store or seeded_store()
    app.state.catalogue = catalogue or Catalogue()

    def get_store() -> Store:
        return app.state.store

    def get_catalogue() -> Catalogue:
        return app.state.catalogue

    def session_valid(sess: Session, now: float) -> bool:
        """Sessions die at max_age_s even if the token is otherwise known."""
        return now - sess.created_epoch <= sess.max_age_s

    def current_customer(
        authorization: Annotated[str | None, Header()] = None,
        store: Store = Depends(get_store),
    ) -> Customer:
        """Resolve the bearer token to a customer; 401 if missing, unknown or expired."""
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        session = store.sessions.get(authorization.removeprefix("Bearer "))
        if session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        if not session_valid(session, time.time()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired")
        return store.customers[session.customer_id]

    def require_owner(resource_customer_id: str, customer: Customer) -> None:
        """A customer may only access resources they own. 404 (not 403) avoids leaking existence."""
        if resource_customer_id != customer.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    def order_out(o: Order) -> OrderOut:
        return OrderOut(
            id=o.id,
            customer_id=o.customer_id,
            sku=o.sku,
            amount=o.amount,
            currency=o.currency,
            status=o.status,
        )

    def invoice_out(store: Store, inv: Invoice) -> InvoiceOut:
        return InvoiceOut(
            id=inv.id,
            customer_id=inv.customer_id,
            order_id=inv.order_id,
            amount=inv.amount,
            paid=inv.paid,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/me", response_model=CustomerOut)
    def me(customer: Customer = Depends(current_customer)) -> CustomerOut:
        return CustomerOut(id=customer.id, name=customer.name, email=customer.email)

    @app.get("/customers/{customer_id}", response_model=CustomerOut)
    def get_customer(
        customer_id: str,
        customer: Customer = Depends(current_customer),
        store: Store = Depends(get_store),
    ) -> CustomerOut:
        """A customer can only read their own profile."""
        target = store.customers.get(customer_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        return CustomerOut(id=target.id, name=target.name, email=target.email)

    @app.get("/orders", response_model=list[OrderOut])
    def list_orders(
        customer: Customer = Depends(current_customer), store: Store = Depends(get_store)
    ) -> list[OrderOut]:
        """List the current customer's own orders."""
        return [order_out(o) for o in store.orders.values() if o.customer_id == customer.id]

    @app.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
    def create_order(
        body: OrderIn,
        customer: Customer = Depends(current_customer),
        store: Store = Depends(get_store),
    ) -> OrderOut:
        o = Order(store.next_id("ord"), customer.id, body.sku, body.amount, body.currency, "placed")
        store.orders[o.id] = o
        return order_out(o)

    @app.get("/orders/{order_id}", response_model=OrderOut)
    def get_order(
        order_id: str,
        customer: Customer = Depends(current_customer),
        store: Store = Depends(get_store),
    ) -> OrderOut:
        """Return one order by id."""
        o = store.orders.get(order_id)
        if o is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        require_owner(o.customer_id, customer)
        return order_out(o)

    @app.get("/invoices/{invoice_id}", response_model=InvoiceOut)
    def get_invoice(
        invoice_id: str,
        customer: Customer = Depends(current_customer),
        store: Store = Depends(get_store),
    ) -> InvoiceOut:
        """Return one invoice by id, including payments applied so far."""
        inv = store.invoices.get(invoice_id)
        if inv is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        require_owner(inv.customer_id, customer)
        return invoice_out(store, inv)

    @app.post("/invoices/{invoice_id}/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
    def pay_invoice(
        invoice_id: str,
        body: PaymentIn,
        customer: Customer = Depends(current_customer),
        store: Store = Depends(get_store),
    ) -> PaymentOut:
        """Apply a partial payment to an invoice the customer owns.

        Total payments for an invoice must never exceed the invoice amount.
        """
        inv = store.invoices.get(invoice_id)
        if inv is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        require_owner(inv.customer_id, customer)
        if inv.paid + body.amount > inv.amount:
            raise HTTPException(422, "payment exceeds invoice balance")
        p = InvoicePayment(store.next_id("pay"), inv.id, customer.id, body.amount)
        store.payments[p.id] = p
        inv.paid += body.amount
        return PaymentOut(id=p.id, invoice_id=p.invoice_id, amount=p.amount)

    @app.get("/search", response_model=list[ProductOut])
    def search_products(
        q: str = "",
        limit: int = 50,
        customer: Customer = Depends(current_customer),
        catalogue: Catalogue = Depends(get_catalogue),
    ) -> list[ProductOut]:
        """Search the product catalogue by name (read-only, parameterized)."""
        rows = catalogue.search(q, limit=max(1, min(limit, 50)))
        return [ProductOut(**row) for row in rows]

    @app.get("/session", response_model=SessionOut)
    def session_status(
        customer: Customer = Depends(current_customer),
        store: Store = Depends(get_store),
        authorization: Annotated[str | None, Header()] = None,
    ) -> SessionOut:
        """Return the current session's age bounds for client display."""
        sess = store.sessions[authorization.removeprefix("Bearer ")]
        return SessionOut(
            customer_id=sess.customer_id,
            issued_epoch=sess.created_epoch,
            max_age_s=sess.max_age_s,
        )

    return app


app = create_app()
