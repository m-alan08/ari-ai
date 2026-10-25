"""TEST DOUBLE of the security-agents orchestrator (owned by Alan), used only to exercise
the backend ``AssessmentHost`` contract end to end. It is NOT the product orchestrator:
its "agents" are one-turn tool loops against a scripted FakeTransport. It lives in
backend/tests and is wired in via ORCHESTRATOR_FACTORY only by tests."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.host import AssessmentHost, agent_run
from shared.assessment import AgentName, AgentRunStatus, AssessmentState
from shared.base import utcnow
from shared.evidence import ArtifactKind, EvidenceKind, EvidenceRef, SourceSpan
from shared.findings import (
    Finding,
    FindingKind,
    FindingStatus,
    PatchCandidate,
    StatusChange,
    VerificationCheck,
    VerificationCheckName,
    VerificationResult,
    VerificationVerdict,
)
from shared.ids import IdPrefix, new_id
from shared.scope import ApprovalStatus, ApprovalSubject

S = AssessmentState


class Note(BaseModel):
    summary: str = Field(max_length=300)


def _loop(host: AssessmentHost, agent: AgentName, step: str, prompt: str) -> tuple[str, Note]:
    gw = host.llm()
    with agent_run(
        host,
        agent,
        step,
        provider_mode=gw.provider_mode,
        model_id=gw.model_for(agent.value),
        prompt_version=f"{agent.value}@test",
        input_payload={"prompt": prompt},
    ) as run_id:
        res = gw.run_tool_loop(
            agent=agent.value,
            system=f"You are {agent.value}.",
            user_content=prompt,
            tools=[],
            output_model=Note,
            max_turns=3,
        )
        host.finish_agent_run(
            run_id,
            AgentRunStatus.succeeded,
            usage=res.usage,
            tool_calls=res.tool_calls,
            output=res.final.model_dump(),
            transcript=res.transcript,
        )
    return run_id, res.final


class ReferenceOrchestrator:
    def __init__(
        self,
        *,
        verify_with_patch_run: bool = False,
        diff_text: str | None = None,
        bind_verified_bytes: bool = False,
        seal_verified_diff: bool = False,
    ) -> None:
        self.verify_with_patch_run = verify_with_patch_run
        self.diff_text = diff_text
        self.bind_verified_bytes = bind_verified_bytes or seal_verified_diff
        self.seal_verified_diff = seal_verified_diff

    def run(self, host: AssessmentHost) -> None:
        a = host.assessment
        if a.state is S.SCOPE_CHECK:
            self._analyze(host)
        elif a.state is S.AWAITING_APPROVAL:
            self._remediate(host)

    def _analyze(self, host: AssessmentHost) -> None:
        a = host.assessment
        host.transition(S.INDEXING)
        host.transition(S.FUNCTIONAL_REVIEW)
        _loop(host, AgentName.reviewer, "review", "review diff")
        host.transition(S.RECON)
        scout_run, note = _loop(host, AgentName.scout, "recon", "map attack surface")
        span = EvidenceRef(
            kind=EvidenceKind.source_span,
            path="app/invoices.py",
            commit_sha=a.commit_sha,
            span=SourceSpan(start_line=40, end_line=52),
            provenance="agent:scout",
        )
        now = utcnow()
        f = Finding(
            id=new_id(IdPrefix.fnd),
            assessment_id=a.id,
            commit_sha=a.commit_sha,
            kind=FindingKind.security,
            title="Invoice lookup lacks tenant filter",
            status=FindingStatus.hypothesis,
            evidence=[span],
            confidence=0.6,
            produced_by_run_id=scout_run,
            subject_refs=["app.invoices.get_invoice"],
            created_at=now,
            updated_at=now,
        )
        host.record_finding(f, actor="agent:scout")
        host.transition(S.VALIDATION)
        probe_run, _ = _loop(host, AgentName.probe, "validation", "validate hypothesis")
        out = host.store_text("GET /invoices/2 as tenant A -> 200 (expected 404)", ArtifactKind.log)
        f = f.model_copy(
            update={
                "status": FindingStatus.validated,
                "confidence": 0.95,
                "updated_at": utcnow(),
                "evidence": [
                    span,
                    EvidenceRef(
                        kind=EvidenceKind.sandbox_run,
                        artifact_id=out.id,
                        sha256=out.sha256,
                        provenance="agent:probe",
                    ),
                ],
                "history": [
                    StatusChange(
                        from_status=FindingStatus.hypothesis,
                        to_status=FindingStatus.validated,
                        at=utcnow(),
                        actor="agent:probe",
                    )
                ],
            }
        )
        host.record_finding(f, actor="agent:probe")
        host.transition(S.ANALYSIS)
        _loop(host, AgentName.trace, "analysis", "estimate impact")
        host.transition(S.DIAGNOSIS)
        _loop(host, AgentName.diagnose, "diagnosis", "root cause")
        host.request_approval(ApprovalSubject.remediation, f.id, "patch the tenant filter")
        host.transition(S.AWAITING_APPROVAL)

    def _remediate(self, host: AssessmentHost) -> None:
        decided = [ap for ap in host.approvals() if ap.subject_type is ApprovalSubject.remediation]
        if not decided or decided[-1].status is ApprovalStatus.pending:
            return
        if decided[-1].status is ApprovalStatus.declined:
            host.transition(S.REPORTING)
            host.transition(S.COMPLETED)
            return
        f = host.findings()[0]
        host.transition(S.REMEDIATION)
        patch_run, _ = _loop(host, AgentName.patch, f"patch:{f.id}", "write minimal diff")
        diff_text = self.diff_text or (
            "--- a/app/invoices.py\n+++ b/app/invoices.py\n+ .filter(tenant=user.tenant)\n"
        )
        diff = host.store_text(
            diff_text,
            ArtifactKind.diff,
            media_type="text/x-diff",
        )
        import hashlib

        verified_sha = hashlib.sha256(diff_text.encode()).hexdigest()
        patch = PatchCandidate(
            id=new_id(IdPrefix.pat),
            finding_id=f.id,
            attempt=1,
            diff_artifact_id=diff.id,
            diff_sha256=diff.sha256,
            base_commit_sha=f.commit_sha,
            worktree_ref=".worktrees/t",
            files_changed=["app/invoices.py"],
            produced_by_run_id=patch_run,
            rationale="re-add tenant filter",
            created_at=utcnow(),
        )
        host.record_patch(patch)
        verify_run = patch_run
        if not self.verify_with_patch_run:
            verify_run, _ = _loop(host, AgentName.verify, f"verify:{patch.id}", "re-run checks")
        log = host.store_text(
            "original check: 404 (fixed); suite: 12 passed", ArtifactKind.test_report
        )
        ev = [
            EvidenceRef(
                kind=EvidenceKind.test_run,
                artifact_id=log.id,
                sha256=log.sha256,
                provenance="agent:verify",
            )
        ]
        binding_evidence: list[EvidenceRef] = []
        if self.bind_verified_bytes:
            binding_doc = {
                "patch_id": patch.id,
                "verified_diff_sha256": verified_sha,
                "stored_diff_artifact_id": diff.id,
                "stored_diff_sha256": diff.sha256,
                "redacted": verified_sha != diff.sha256,
            }
            if self.seal_verified_diff:
                sealed = host.seal_diff(diff_text, verified_sha)
                binding_doc["sealed_diff_artifact_id"] = sealed.id
            binding = host.store_json(binding_doc)
            binding_evidence = [
                EvidenceRef(
                    kind=EvidenceKind.artifact,
                    artifact_id=binding.id,
                    sha256=binding.sha256,
                    provenance="agent:verify",
                )
            ]
        host.record_verification(
            VerificationResult(
                id=new_id(IdPrefix.ver),
                patch_id=patch.id,
                verdict=VerificationVerdict.accepted,
                checks=[
                    VerificationCheck(
                        name=n,
                        passed=True,
                        evidence=[*ev, *(binding_evidence if n.value == "diff_scope_ok" else [])],
                    )
                    for n in VerificationCheckName
                ],
                sandbox_run_ids=["sbx-test"],
                produced_by_run_id=verify_run,
                created_at=utcnow(),
            )
        )
        host.record_finding(
            f.model_copy(update={"status": FindingStatus.verified, "updated_at": utcnow()}),
            actor="agent:verify",
        )
        host.transition(S.HARDENING)
        _loop(host, AgentName.fortify, "hardening", "prevent recurrence")
        host.transition(S.REPORTING)
        host.transition(S.COMPLETED)


def build_orchestrator() -> ReferenceOrchestrator:
    return ReferenceOrchestrator()


def build_cheating_orchestrator() -> ReferenceOrchestrator:
    return ReferenceOrchestrator(verify_with_patch_run=True)


def build_sealed_diff_orchestrator() -> ReferenceOrchestrator:
    return ReferenceOrchestrator(
        diff_text=(
            "--- a/app/invoices.py\n+++ b/app/invoices.py\n"
            "+ tokens: dict[str, str]\n"
            "+ assert headers == 'Bearer fixture-token-alice'\n"
        ),
        seal_verified_diff=True,
    )


def build_secret_diff_orchestrator() -> ReferenceOrchestrator:
    return ReferenceOrchestrator(
        diff_text=(
            "--- a/app/invoices.py\n+++ b/app/invoices.py\n"
            "+ github_token = '" + "ghp_" + ("a" * 36) + "'\n"
        ),
        seal_verified_diff=True,
    )


def build_legacy_redacted_diff_orchestrator() -> ReferenceOrchestrator:
    return ReferenceOrchestrator(
        diff_text=(
            "--- a/app/invoices.py\n+++ b/app/invoices.py\n"
            "+ assert headers == 'Bearer fixture-token-alice'\n"
        ),
        bind_verified_bytes=True,
    )


def scripted_transport() -> Any:
    """Every call answers with a valid submit_result naming the requesting agent."""
    from backend.llm import FakeTransport

    def respond(request: dict[str, Any]) -> dict[str, Any]:
        agent = request["system"].split("You are ", 1)[1].split(".", 1)[0]
        return {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_submit",
                    "name": "submit_result",
                    "input": {"summary": f"{agent} done"},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 800, "output_tokens": 120},
        }

    return FakeTransport([respond] * 50)
