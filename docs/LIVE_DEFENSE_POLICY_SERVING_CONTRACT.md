# Policy serving contract (future approved checkpoint)

Status: **infrastructure**. No approved checkpoint is served.

This is the stable output shape for a **future** RL-owner-approved policy.
Missing fields stay missing. Q-values are stored scores, not probabilities.

## Fields

| Field | Meaning | If missing |
|---|---|---|
| `policy_version` | Version string from the runner | omit / null |
| `checkpoint_sha256` | Hash pin of the loaded bytes | null; do not load |
| `action` / `node` | Selected action | null |
| `top_actions` | Q ranking | null; never synthesize |
| `q_margin` | Score margin | null |
| `observation` / `observation_dim` | Feature vector | null |
| `inference_status` | `ran` or a skip reason | recorded |
| `abstention_reason` | Why inference did not decide | null if it ran |
| `feature_schema_ok` / `action_mask_valid` / `freshness_ok` / `observation_complete` | Serving assumptions | null if unknown |
| `principal_targets` | Pseudonymous principals | null |
| `feature_mapping_note` | Mapping caveat | null |
| `trained_commit_sha` | Training source commit | null until approved |
| `eval_bundle_sha256` | Evaluation evidence pin | null until approved |
| `uncertainty` | Explicit uncertainty if the policy emits one | null; **not** a Q-value |
| `q_values_are_not_probabilities` | Always true | always present |
| `auto_served_unapproved_checkpoint` | Always false | always present |

## Compatibility

Frozen v6 (`8113a37f…`) is a **compatibility fixture** only. Mapping a v6
`ShadowInference` into this contract must not claim v6 passed hosted-inference
timeout, restart, isolation, or new abstention criteria.

Shared-model changes that would alter research semantics need RL-owner
agreement. This additive contract (`SCHEMA_VERSION` 0.20.0) does not change
v6 training or promotion.

## Hosted inference readiness (not serving)

Configured, not activated:

- refuse hash mismatch and tamper
- timeout / restart: unset pending RL-owner
- isolation: worker process; untrusted code stays in sandbox
- abstention on assumption failure; fallback `abstention`
- never auto-serve unapproved or newer research checkpoints
- `approved_checkpoint_sha256` is null
