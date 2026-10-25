"""Evidence references and artifact pointers.

Every conclusion the platform shows must cite at least one ``EvidenceRef``.
Large content (diffs, logs, prompts) is never inlined: it lives in the artifact
store and is referenced by ``ArtifactRef`` (id + sha256).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from shared.base import CommitSha, RepoRelPath, Sha256Hex, ShortText, StrictModel
from shared.ids import ArtifactId


class SourceSpan(StrictModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    start_col: int | None = Field(default=None, ge=0)
    end_col: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> SourceSpan:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        return self


class EvidenceKind(StrEnum):
    source_span = "source_span"
    test_run = "test_run"
    sandbox_run = "sandbox_run"
    tool_output = "tool_output"
    artifact = "artifact"
    human_input = "human_input"
    doc = "doc"
    change_intent = "change_intent"
    monitoring_event = "monitoring_event"


class EvidenceRef(StrictModel):
    """Pointer to a piece of evidence pinned to an exact code version.

    ``source_span`` evidence must carry ``path`` + ``commit_sha`` + ``span``;
    artifact-backed kinds must carry ``artifact_id`` + ``sha256``.
    """

    kind: EvidenceKind
    path: RepoRelPath | None = None
    commit_sha: CommitSha | None = None
    span: SourceSpan | None = None
    symbol: ShortText | None = Field(default=None, description="Qualified symbol name or sym_ id")
    artifact_id: ArtifactId | None = None
    sha256: Sha256Hex | None = None
    provenance: ShortText = Field(
        description="Producer and version, e.g. 'code_intelligence.indexer@0.1' or 'agent:probe'"
    )
    note: ShortText | None = None

    @model_validator(mode="after")
    def _complete(self) -> EvidenceRef:
        if self.kind is EvidenceKind.source_span and not (
            self.path and self.commit_sha and self.span
        ):
            raise ValueError("source_span evidence requires path, commit_sha and span")
        if self.kind in {
            EvidenceKind.test_run,
            EvidenceKind.sandbox_run,
            EvidenceKind.tool_output,
            EvidenceKind.artifact,
        } and not (self.artifact_id and self.sha256):
            raise ValueError(f"{self.kind.value} evidence requires artifact_id and sha256")
        return self


class ArtifactKind(StrEnum):
    diff = "diff"
    sealed_diff = "sealed_diff"
    log = "log"
    test_report = "test_report"
    webhook_payload = "webhook_payload"
    llm_transcript = "llm_transcript"
    tool_output = "tool_output"
    report = "report"
    rl_eval_summary = "rl_eval_summary"
    rl_checkpoint = "rl_checkpoint"
    other = "other"


class ArtifactRef(StrictModel):
    id: ArtifactId
    kind: ArtifactKind
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    media_type: ShortText = "application/octet-stream"
    redacted: bool = Field(
        default=True, description="True when secrets were scrubbed before storage"
    )
