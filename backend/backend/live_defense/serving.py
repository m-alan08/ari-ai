"""Stable serving contract for a future approved policy.

Frozen v6 is a compatibility fixture only. Missing fields stay missing.
Q-values are scores, not probabilities. This module never auto-serves a
newer research checkpoint.
"""

from __future__ import annotations

from typing import Any

from backend.live_defense.policy import V6_SHA256, ShadowInference
from shared.live_defense import (
    HostedInferenceReadinessConfig,
    HostedInferenceReadinessReport,
    PolicyServingOutput,
)

HOSTED_ISOLATION = (
    "Worker process only; untrusted target code stays in the sandbox; "
    "no outbound network by default."
)


def serving_from_inference(inference: ShadowInference) -> PolicyServingOutput:
    """Map a real inference result. Nulls stay null; nothing is synthesized."""
    observation = inference.observation
    return PolicyServingOutput(
        policy_version=inference.policy_version,
        checkpoint_sha256=inference.checkpoint_sha256,
        action=inference.action,
        node=inference.node,
        top_actions=inference.top_actions,
        q_margin=inference.q_margin,
        observation=observation,
        observation_dim=None if observation is None else len(observation),
        inference_status=inference.status,
        abstention_reason=inference.reason,
        feature_schema_ok=inference.feature_schema_ok,
        action_mask_valid=inference.action_mask_valid,
        principal_targets=inference.principal_targets,
        feature_mapping_note=inference.feature_mapping_note,
        trained_commit_sha=None,
        eval_bundle_sha256=None,
        uncertainty=None,
    )


def serving_from_labeled_fixture(payload: dict[str, Any]) -> PolicyServingOutput:
    """Compatibility path when torch/v6 cannot run. Does not invent missing keys."""
    allowed = set(PolicyServingOutput.model_fields)
    present = {key: payload[key] for key in payload if key in allowed and payload[key] is not None}
    present.setdefault("q_values_are_not_probabilities", True)
    present.setdefault("auto_served_unapproved_checkpoint", False)
    return PolicyServingOutput.model_validate(present)


def hosted_config(*, include_v6_fixture: bool = True) -> HostedInferenceReadinessConfig:
    return HostedInferenceReadinessConfig(
        compatibility_fixture_sha256=V6_SHA256 if include_v6_fixture else None,
        isolation=HOSTED_ISOLATION,
    )


def hosted_report() -> HostedInferenceReadinessReport:
    config = hosted_config()
    return HostedInferenceReadinessReport(
        config=config,
        approved_checkpoint_present=config.approved_checkpoint_sha256 is not None,
        v6_used_only_as_compatibility_fixture=True,
        v6_passed_new_criteria=False,
        notes=[
            "v6 hash-pin is a compatibility fixture only.",
            "v6 is not claimed to have passed timeout, restart, isolation, or new abstention criteria.",
            "Newer research checkpoints are not auto-served.",
        ],
    )
