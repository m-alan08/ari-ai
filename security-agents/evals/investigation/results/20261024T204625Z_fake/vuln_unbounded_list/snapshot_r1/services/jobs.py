"""Job commands for report-hub. Commands are allow-listed and run as argv lists."""

from __future__ import annotations

import sqlite3
import subprocess

ALLOWED_COMMANDS = frozenset({"render", "reindex", "export"})


def run_report_command(command: str, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run an allow-listed report command; argv is a list, never a shell string."""
    if command not in ALLOWED_COMMANDS:
        raise ValueError(f"command {command!r} is not allowed")
    argv = ["reportctl", command, *args]
    return subprocess.run(argv, capture_output=True, timeout=60)


def export_rows(db_path: str, tenant: str, limit: int) -> list[tuple[str, str]]:
    """Export report rows for a tenant with a parameterized query."""
    db = sqlite3.connect(db_path)
    try:
        cur = db.execute(
            "SELECT id, title FROM reports WHERE tenant = ? LIMIT ?",
            (tenant, limit),
        )
        return [(str(r[0]), str(r[1])) for r in cur.fetchall()]
    finally:
        db.close()
