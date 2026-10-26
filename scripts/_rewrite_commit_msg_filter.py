#!/usr/bin/env python3
"""stdin -> stdout commit message filter for scripts/rewrite_ai_commit_authors.sh."""

from __future__ import annotations

import re
import sys

_DROP_EMAILS = {
    "cursoragent@cursor.com",
    "noreply@anthropic.com",
    "206951365+cursor[bot]@users.noreply.github.com",
    "158243242+devin-ai-integration[bot]@users.noreply.github.com",
}
_DROP_NAME_PREFIXES = (
    "cursor",
    "claude",
    "codex",
    "devin",
)
_CANON: dict[str, str] = {
    "amozhoor7@gatech.edu": "Alan Mozhoor <alanmozhoor@gmail.com>",
    "fam.mozhoor@gmail.com": "Alan Mozhoor <alanmozhoor@gmail.com>",
    "alanmozhoor@gmail.com": "Alan Mozhoor <alanmozhoor@gmail.com>",
    "adamissac08@gmail.com": "Adam Issac <62719255+adamissac@users.noreply.github.com>",
    "62719255+adamissac@users.noreply.github.com": (
        "Adam Issac <62719255+adamissac@users.noreply.github.com>"
    ),
    "saisirichowdarapu@Saisiris-MacBook-Air.local": (
        "Saicharan Chowdarapu <137108827+SaiC123@users.noreply.github.com>"
    ),
    "saisirichowdarapu@Mac.attlocal.net": (
        "Saicharan Chowdarapu <137108827+SaiC123@users.noreply.github.com>"
    ),
    "137108827+SaiC123@users.noreply.github.com": (
        "Saicharan Chowdarapu <137108827+SaiC123@users.noreply.github.com>"
    ),
    "srihithys@gmail.com": "SrihithY <srihithys@gmail.com>",
    "saroshdoddaka334@gmail.com": (
        "Sarosh Doddaka <114310140+saroshdoddaka@users.noreply.github.com>"
    ),
    "114310140+saroshdoddaka@users.noreply.github.com": (
        "Sarosh Doddaka <114310140+saroshdoddaka@users.noreply.github.com>"
    ),
    "vvihaankrishna@gmail.com": (
        "Vihaan Krishna <106044596+vihaankrishna100@users.noreply.github.com>"
    ),
    "106044596+vihaankrishna100@users.noreply.github.com": (
        "Vihaan Krishna <106044596+vihaankrishna100@users.noreply.github.com>"
    ),
}

_CO = re.compile(r"^(?i)(\s*co-authored-by:\s*)(.+?)\s*<([^>]+)>\s*$")
_MADE = re.compile(r"(?i)^\s*(made-with:\s*cursor|made with cursor|🤖\s*generated with)")


def main() -> None:
    out: list[str] = []
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if _MADE.match(line):
            continue
        m = _CO.match(line)
        if not m:
            out.append(line + "\n")
            continue
        prefix, name, email = m.group(1), m.group(2).strip(), m.group(3).strip()
        email_l = email.lower()
        name_l = name.lower()
        if email_l in {e.lower() for e in _DROP_EMAILS} or any(
            name_l.startswith(p) for p in _DROP_NAME_PREFIXES
        ):
            continue
        if "bot" in email_l and "users.noreply.github.com" in email_l:
            continue
        canon = _CANON.get(email) or _CANON.get(email_l)
        if canon:
            out.append(f"{prefix}{canon}\n")
        else:
            out.append(line + "\n")
    text = "".join(out).rstrip("\n") + "\n"
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
