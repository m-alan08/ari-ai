"""Labeled retrieval evaluation for BM25+graph context packs.

Each case in CASES is a question whose necessary evidence spans are known up
front: every ``required`` qualified name must appear in the pack's provenance
for the pack to count as a recall hit. The assertions encode a regression
floor measured on this fixture (primary evidence always recalled, at least
80% of secondary evidence), not a claim that retrieval is complete — the
audit report records where larger repositories fall below it.
"""

from __future__ import annotations

from pathlib import Path

from intel_test_helpers import commit
from intel_test_helpers import repository as repository
from shared.intelligence import ContextPackRequest

from code_intelligence.api import IntelligenceService, RepositoryBinding
from code_intelligence.store import SQLiteIndexStore

FIXTURE_FILES = {
    "shop/__init__.py": "",
    "shop/auth.py": (
        "def current_token(authorization: str):\n"
        '    """Must resolve the Bearer token to a tenant-scoped customer."""\n'
        "    if not authorization.startswith('Bearer '):\n"
        "        raise ValueError('unauthenticated')\n"
        "    return authorization.removeprefix('Bearer ')\n\n"
        "def require_owner(customer_id, token):\n"
        '    """A customer may only read their own resources."""\n'
        "    if customer_id != token:\n"
        "        raise PermissionError('not the owner')\n"
    ),
    "shop/payments.py": (
        "from fastapi import FastAPI, Depends\n"
        "from shop.auth import current_token, require_owner\n\n"
        "app = FastAPI()\n\n"
        "def payment_out(payment):\n"
        "    return {'id': payment['id'], 'amount': payment['amount']}\n\n"
        "@app.get('/payments')\n"
        "def list_payments(token=Depends(current_token)):\n"
        "    return [payment_out(p) for p in STORE.payments_for(token)]\n\n"
        "@app.get('/payments/{payment_id}')\n"
        "def get_payment(payment_id: str, token=Depends(current_token)):\n"
        "    payment = STORE.payments[payment_id]\n"
        "    require_owner(payment['customer'], token)\n"
        "    return payment_out(payment)\n"
    ),
    "shop/refunds.py": (
        "from fastapi import FastAPI, Depends\n"
        "from shop.auth import current_token, require_owner\n\n"
        "app = FastAPI()\n\n"
        "@app.post('/refunds')\n"
        "def create_refund(payment_id: str, amount: float, token=Depends(current_token)):\n"
        "    payment = STORE.payments[payment_id]\n"
        "    require_owner(payment['customer'], token)\n"
        "    total = sum(r['amount'] for r in STORE.refunds_for(payment_id))\n"
        "    if total + amount > payment['amount']:\n"
        "        raise ValueError('refunds exceed payment')\n"
        "    return {'status': 'ok'}\n"
    ),
    "tests/test_shop.py": (
        "def test_refund_capped_at_payment_amount(): ...\n"
        "def test_other_customers_payment_is_not_readable(): ...\n"
        "def test_missing_token_is_rejected(): ...\n"
    ),
}

# (question focus, required qualified names). The first name is the primary
# evidence — the span a reviewer cannot answer without.
CASES: list[tuple[list[str], list[str]]] = [
    (
        ["create refund", "/refunds"],
        ["shop.refunds.create_refund", "shop.auth.require_owner"],
    ),
    (
        ["list payments for the caller", "list_payments"],
        ["shop.payments.list_payments", "shop.payments.payment_out"],
    ),
    (
        ["bearer token authentication", "current_token"],
        ["shop.auth.current_token"],
    ),
    (
        ["read one payment by id", "/payments/{payment_id}"],
        ["shop.payments.get_payment", "shop.auth.require_owner"],
    ),
]


def service(path: Path, database: Path) -> IntelligenceService:
    return IntelligenceService(
        SQLiteIndexStore(database),
        [
            RepositoryBinding(
                project_id="prj_fixture00",
                repo_id="rep_fixture00",
                full_name="owned/fixture",
                local_path=path,
            )
        ],
    )


def pack_spans(pack) -> set[tuple[str, int, int]]:
    return {
        (p.path, p.span.start_line, p.span.end_line) for p in pack.provenance if p.path and p.span
    }


def test_labeled_questions_recall_required_evidence(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    root, _ = repository
    for name, content in FIXTURE_FILES.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    sha = commit(root)

    engine = service(root, tmp_path / "index.sqlite")
    ref = engine.index_repository("prj_fixture00", "rep_fixture00", sha)
    index = engine.store.get(ref)
    spans = {s.qualified_name: (s.path, s.start_line, s.end_line) for s in index.symbols}
    for _, required in CASES:
        assert all(name in spans for name in required), "fixture drifted"

    per_case: list[tuple[list[str], int, int]] = []
    for focus, required in CASES:
        pack = engine.build_context_pack(
            ContextPackRequest(
                snapshot_ref=ref,
                purpose="review",
                focus=focus,
                token_budget=20_000,
            )
        )
        assert pack.token_count <= pack.token_budget
        present = pack_spans(pack)
        hits = [name for name in required if spans[name] in present]
        per_case.append((required, len(hits), len(required)))
        # Primary evidence must never be missed on this fixture.
        assert spans[required[0]] in present, f"{focus}: missed {required[0]}"

    recalled = sum(h for _, h, _ in per_case)
    total = sum(t for _, _, t in per_case)
    # Regression floor measured on this fixture; large-repo behavior is
    # reported in the audit, not asserted here.
    assert recalled / total >= 0.8
