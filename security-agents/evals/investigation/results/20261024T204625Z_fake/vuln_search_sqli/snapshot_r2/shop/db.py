"""Tiny sqlite-backed product catalogue used by the search endpoint."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

_SCHEMA = """
CREATE TABLE products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price TEXT NOT NULL
)
"""

_SEED = [
    ("sku-chair", "Desk Chair", "120.00"),
    ("sku-desk", "Standing Desk", "80.00"),
    ("sku-lamp", "Reading Lamp", "15.50"),
    ("sku-mat", "Floor Mat", "30.00"),
]


class Catalogue:
    """Read-only product catalogue. All queries are parameterized."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)
        self._db.executemany("INSERT INTO products VALUES (?, ?, ?)", _SEED)

    def search(self, query: str, limit: int = 50) -> list[dict[str, object]]:
        sql = f"SELECT sku, name, price FROM products WHERE name LIKE '%{query}%' LIMIT {int(limit)}"
        cur = self._db.execute(sql)
        return [
            {"sku": r["sku"], "name": r["name"], "price": Decimal(r["price"])}
            for r in cur.fetchall()
        ]

    def close(self) -> None:
        self._db.close()
