"""Rehearsal target app — a small FastAPI service with intentional flaws.

This application is the *customer repository* for the two-organization pilot
rehearsal. It is deliberately insecure in specific, checkable ways so the
assessment has genuine findings to prove. Do not deploy it anywhere.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException

DB = Path(__file__).resolve().parent / "books.db"

SEED = [(1, "alice", "Dune", "private alice notes"), (2, "bob", "Foundation", "private bob notes")]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS books (id INTEGER PRIMARY KEY, owner TEXT, title TEXT, notes TEXT)"
    )
    c.executemany("INSERT OR IGNORE INTO books (id, owner, title, notes) VALUES (?, ?, ?, ?)", SEED)
    c.commit()
    return c


def create_app() -> FastAPI:
    app = FastAPI(title="rehearsal-books")

    def current_user(authorization: str = Header(default="")) -> str:
        # INTENTIONAL FLAW 1: bearer check compares a static prefix, no signature/expiry.
        if not authorization.startswith("Bearer demo-"):
            raise HTTPException(status_code=401)
        return authorization.removeprefix("Bearer demo-")

    @app.get("/books")
    def list_books(user: str = Depends(current_user)):
        with _conn() as c:
            rows = c.execute("SELECT id, owner, title FROM books").fetchall()
        # INTENTIONAL FLAW 2: returns every owner's books to any authenticated user.
        return {"items": [{"id": r[0], "owner": r[1], "title": r[2]} for r in rows]}

    @app.get("/books/{book_id}")
    def get_book(book_id: int, user: str = Depends(current_user)):
        with _conn() as c:
            # INTENTIONAL FLAW 3: string-formatted SQL — book_id is interpolated raw.
            row = c.execute(
                f"SELECT id, owner, title, notes FROM books WHERE id = {book_id}"
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404)
        # INTENTIONAL FLAW 4: no ownership check — any user reads any owner's notes.
        return {"id": row[0], "owner": row[1], "title": row[2], "notes": row[3]}

    @app.post("/books")
    def create_book(payload: dict, user: str = Depends(current_user)):
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO books (owner, title, notes) VALUES (?, ?, ?)",
                (user, str(payload.get("title", "")), str(payload.get("notes", ""))),
            )
            c.commit()
            return {"id": cur.lastrowid}

    @app.delete("/books/{book_id}")
    def delete_book(book_id: int, user: str = Depends(current_user)):
        # INTENTIONAL FLAW 5: mutating route ignores role — a viewer can delete.
        with _conn() as c:
            c.execute("DELETE FROM books WHERE id = ?", (book_id,))
            c.commit()
        return {"deleted": book_id}

    return app


app = create_app()
