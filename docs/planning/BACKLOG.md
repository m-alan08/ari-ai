# Backlog (post-MVP and stretch)

Owners UNASSIGNED. IDs use the `1xx` range per prefix. Stretch items may be
pulled into MVP only if MVP tasks they would compete with are `DONE`.

| ID | Title | Type | Rationale / trigger | Depends on |
|---|---|---|---|---|
| INTEL-101 | JavaScript/TypeScript extraction + Express adapter | stretch | FR-1-08; broadens demo | INTEL-003 |
| INTEL-102 | User confirm/edit expectations UI + API | stretch | FR-1-04 | INTEL-006, UI-001 |
| INTEL-103 | pgvector semantic retrieval | later | If lexical recall insufficient (ADR-0010 revisit) | INTEL-007 |
| SEC-101 | GitHub PR creation from verified patch | stretch | FR-2-08 | SEC-010, CORE-005 |
| SEC-102 | gVisor/Firecracker sandbox runtime | later | Before third-party repos (R-03) | SEC-003 |
| SEC-103 | Additional check-template categories | later | Coverage growth; each needs security review | SEC-005 |
| SEC-104 | Staging-URL scope targets with ownership verification | later | Requires new contract + legal review | CORE-007 |
| MON-101 | PR comments / commit statuses | stretch | Developer workflow | MON-003 |
| MON-102 | OpenTelemetry runtime ingestion | later | Real telemetry | MON-004 |
| MON-103 | Dependency advisory polling (OSV) | later | SCA coverage | MON-002 |
| RL-101 | Ablations (DQN vs DDQN, features, reward sensitivity) | later | Protocol 17 §Ablations | RL-004 |
| RL-102 | Replay real monitoring signals as eval observations | later | RQ-005 transfer question | RL-004, MON-004 |
| CORE-101 | Organizations RBAC (owner/member/viewer) | later | FR-P-06 | CORE-005 |
| CORE-102 | S3-compatible artifact store | later | Hosted deployment | CORE-009 |
| CORE-103 | Queue abstraction swap (Celery/Temporal) | later | R-09 trigger | CORE-006 |
| INFRA-101 | Hosted staging (Vercel + Railway free tiers) | later | Requires approval (NFR-09) | INFRA-004 |
| INFRA-102 | Dedicated sandbox host | later | Paid; requires approval + ADR | SEC-102 |
| DOCS-101 | Contributor License Agreement / DCO policy | later | Before outside contributions | DOCS-003 |
| TEST-101 | Labeled benchmark for finding precision | later | [research/EVALUATION_DATASETS](../research/EVALUATION_DATASETS.md) | TEST-005 |
| PROD-101 | Design-partner interviews (validate A-1…A-4) | later | [02](../02_PROBLEM_AND_USERS.md) | — |

- DONE (2026-10-17): Pricing navigation uses the same color, hover, and contrast handling as sibling links.


Completed: homepage invitation and evaluation selector alignment (feat/adam/home-next-step).


Completed: centered sign-in form and reduced-motion-aware background decoration.


Completed: evaluation terminology cleanup and explicit synthetic/illustrative provenance labels.

### Independent review automation — activation pending (2026-10-25)

Automation package implemented; local safety suite 56 passing. Manually review/merge
installation PRs, verify GitHub container CI, then configure native Codex, optional
OpenAI project key and existing Devin sessions with confirmed native hard caps.
No prompt relay is needed after activation. See
`docs/release/AUTOMATED_INDEPENDENT_REVIEW.md`; paid calls remain OFF.
