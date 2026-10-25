# First-user flow map and usability blockers

Audience: a nontechnical pilot user (a founder with no cybersecurity training).
Scope: the real connected product — not a demo script. Verified end-to-end on the
e2e stack (real API + Postgres + worker + Next app; model replies scripted and
labelled `Test provider (scripted)` in the UI) on 2026-10-24.

## The journey, screen by screen

| Step | Route | What the user sees | Honesty label |
|---|---|---|---|
| 1. Sign in | `/` → sign in | Dev login (e2e) or GitHub OAuth (prod) | live |
| 2. Pick org | org switcher | Only orgs the user belongs to | live |
| 3. Connect repo | `/connect` | Repo picker → project created | live |
| 4. Pick commit | `/connect/{org}/{repo}/commit/{sha}` | Commit workspace | live |
| 5. Instant read | Quick-read panel | One-paragraph AI read of the diff; friendly error when no model key is configured (raw error behind "Technical detail") | live when run; error state honest |
| 6. Run agents | `7 agents` tab | Sector map, 4-phase row, per-agent rail, step log in plain English ("Followed the call graph", "Read the code", "Reported its result") | `RECORDED RUN` + `TEST PROVIDER (SCRIPTED)` when replaying a stored run; `Preview` for diff-only reasoning |
| 7. Review findings | Findings list | Title, severity, kind (Security/Functional), file path, plain-English explanation, recorded evidence | persisted records |
| 8. Approve / decline | `Human approval required` region | `Fix request · <finding title>` per approval, Will / Will-not grid, two-step confirm ("never merges or deploys"), Back cancels | live decisions |
| 9. Verify evidence | Post-run results | Independent Verify reruns the attack + normal tests; evidence links to executed output | persisted records |
| 10. Monitor | `/projects/{id}` feed, `/incidents` | New commits, triggered assessments, incident closure with linked verified findings | live / persisted |

## What "live result" vs "recorded" means in the UI

- **Live result** — produced by the running stack right now (session, quick read
  with a configured key, approval decisions, monitoring events).
- **Recorded run** — a stored assessment replayed step by step; header says
  `RECORDED RUN · <status> · <provider label>`; provider summary distinguishes
  `Test provider (scripted)` from live-provider turns.
- **Scripted test** — e2e/seeded runs; provider label says scripted, never "live".
- **Preview** — the agent map's diff-only reasoning before any run exists.
- **Illustrative sample** — static example content (avoid; where present it must
  say so explicitly).

## Usability blockers found → status

| Blocker | Severity | Status |
|---|---|---|
| Approval card showed `Fix request (remediation)` — user could not tell which finding they were approving | High | Fixed (#538): subject resolved via finding records; falls back to `Fix request ({subject_type})` |
| Approval was a single ambiguous click | High | Fixed (#538): two-step confirm with Will/Will-not copy, Back cancels |
| Agent replay showed internal tool ids (`ns__tool`) | Medium | Fixed (#538): plain-English step names |
| Quick-read failure dumped a raw server error | Medium | Fixed (#538): friendly message + "Technical detail" disclosure + Try again |
| "Recorded tool calls" disclosure still shows raw ids (`read_file · ok`) | Low | Fixed (#538): shares the replay's plain-English names |
| Finding paths inconsistently prefixed (`payments_lite/app.py` vs `target/payments_lite/app.py`) | Low | Fixed (#538): paths display repo-relative |
| Decline is one-click (asymmetric with two-step Approve) | Low | Open — consider confirm for destructive declines |
| Approval inbox (`/approvals`) approved in one click; only the on-page gate had the two-step confirm | Medium | Fixed (#538): inbox Approve opens the same confirm + Back |
| No video artifact this pass (`recording_stop` post-processing failed twice) | — | Screenshots captured instead; re-record in a fresh session if needed |

## Verified assertions

- Named approval cards with severity/kind/path badges; no unresolved fallback.
- Two-step confirm copy is honest: "never merges or deploys".
- Keyboard-only operation works (Tab → Approve/Decline/Request details/Back; Enter opens disclosure + confirm).
- ~430px viewport: no horizontal overflow on cards, sector map, step log, findings.
- State persists across reload; approvals left pending stay pending.
- No real-time-protection or automatic-fix claims anywhere; every stat cites saved records.
- Org/repo scope is per-user membership; breadcrumb scoped to the connected org/repo/commit.

## Acceptance check

A nontechnical user can navigate the product, tell which actions already happened
(recorded run + persisted evidence vs. live controls), tell a possible issue
("suspicion"/"possible") from a confirmed finding ("confirmed", "Issue confirmed"),
and complete an authorized review (approve/decline with a second click) without
developer assistance. Verified against the seeded `AWAITING_APPROVAL` assessment
with two pending remediation approvals.
