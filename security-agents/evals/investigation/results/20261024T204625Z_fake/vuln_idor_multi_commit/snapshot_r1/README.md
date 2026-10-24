# shop-lite

A small orders/invoices API used as an evaluation target.

## Behavior

- Bearer tokens identify a customer; sessions expire after `max_age_s`.
- A customer can only read their own profile, orders and invoices.
- Invoice payments accumulate and may never exceed the invoice amount.
- `/search` queries the product catalogue with parameterized SQL; it is read-only.
