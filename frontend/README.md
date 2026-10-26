# frontend/

Next.js 16 (App Router) + React 19 + strict TypeScript + Tailwind 4 dashboard for the
whole product. Console screens read the control-plane API (`/api/v1`) or, for the Defense
Lab, a committed and schema-validated RL evaluation bundle. The Live Defense dashboard
also has an isolated **layout-preview** (`?preview=layout` and `/live-defense-pilot`)
whose fixtures are labelled sample layout and are never mixed with records.
Spec: [docs/14](../docs/14_FRONTEND_UX_DESIGN.md).

## What is implemented

| Route | Backed by |
|---|---|
| `/` landing (signed in): intro loader once per sign-in, full-screen hero, scroll-told chapters (problem timeline, how it works, one finding start to proof, checkable proof stats) | Nothing beyond the session; copy is product-accurate and static |
| `/projects` redirects to `/connect`: the Projects section is the connected-organization drill-down — organization → repositories → commits → per-commit Code review or 7 agents (with replay). Projects are created implicitly when a repository is connected; there is no standalone create-project form | `GET /github/repositories`; connect creates `POST /projects` + `/projects/{id}/repositories` implicitly |
| `/pricing` plans in the kairo.security layout, clearly marked **hackathon preview: proposals, nothing can be purchased** | Nothing (static) |
| `/projects/[id]` one next step from the loaded records (connect a repo, approve a scope, run a review, open the run, or view results), then assessments (start note: enqueue only), findings, repositories | `/projects/{id}/assessments`, `/findings`, `/scopes`, `/repositories` |
| `/connect/[org]/[repo]/commit/[sha]` has two tabs: Code review and 7 agents. The agents tab includes replay and a compact real-run panel: request authorization if needed, start an assessment under an active scope, and open the latest persisted run. The separate “Quick read” remains one Claude call that narrates a preview; it executes nothing and is not the agent run. Probe execution needs a repository `build_recipe.json`; otherwise validation is inconclusive and Patch/Verify run only on confirmed findings. | GitHub repository/project, scope, and assessment transport methods (`/github/repositories`, `/projects/{id}/scopes`, `/projects/{id}/assessments`); the separate preview still uses `POST /api/analyze` (`app/api/analyze/route.ts`, `lib/live-analysis.ts`) |
| `/projects/[id]/commits` select a GitHub commit, see matching recorded assessments, open the code-review and seven-agent journey | GitHub commits, repository snapshots and project assessments (bounded recent pages, stated in UI) |
| `/projects/[id]/scopes` request (pending-only note), approve (owners may approve their own sandbox-only scope), revoke | `/projects/{id}/scopes`, `/scopes/{id}/approve`, `/scopes/{id}/revoke` |
| `/projects/[id]/assessments/[asm]` live timeline: state rail from states actually entered, selectable eight-agent view (job, recorded outcome, real tool calls, handoff), budget meter, remediation approval gate, cancel (queued vs stop-requested, findings kept), durable event log, provider-mode banner | `watchAssessment` SSE (`Last-Event-ID` resume) + `GET /assessments/{id}` + `POST .../cancel` + 5 s polling fallback while running |
| `/projects/[id]/findings` validated / hypotheses / closed tabs | `/projects/{id}/findings` |
| `/projects/[id]/findings/[fnd]` evidence chain (expectation → Scout → Probe → Trace → Diagnose → Patch → Verify → Fortify), standards labels (CWE, CVSS, ATT&CK, D3FEND) from the persisted record, GitHub links at the exact SHA, line-numbered diff, Verify checks, status history, accept risk | `/findings/{id}`, `/findings/{id}/patches`, `/artifacts/{id}` (size-bounded, SHA-256 checked) |
| `/projects/[id]/approvals` inbox with will / will-not summaries | `/projects/{id}/approvals`, `/approvals/{id}/decision` |
| `/projects/[id]/monitoring` webhook/runtime event feed (simulated events labelled), impact sets, re-assessments, simulation-derived RL recommendations + explanations, imported RL evaluations, and an owner-only 24/7 defense switch | `/projects/{id}/events`, `/reassessments`, `/defense-recommendations`, `/rl-evaluations`, `PATCH /projects/{id}` |
| `/projects/[id]/live-defense` customer Live Defense dashboard (Overview, Investigation, Explainability, Staging apps, Controls, Readiness, First responder). Live mode is control-plane records plus owned-test controls plus server-enforced first-responder modes (OFF / MONITOR\|SHADOW / ASSISTED RESPONSE; AUTO-EXECUTE rejected). Scout/Diagnose may show a real authorized agent loop (cost/latency/integrity); that is not confirmation. RL-gated apps are server-enforced shadow-only. Analyst accept does not execute. Isolate is never offered. Rate-limit and exact-source block require a separate authorization endpoint after the deterministic gate, labelled **OWNED TEST ENVIRONMENT**. Adapter HTTP 200 is not effectiveness. Readiness is backend `GET /rl-readiness` (not a frontend flag). An RL recommendation is not executed defense. `?preview=layout` swaps in labelled sample chrome | `/projects/{id}`, `/events`, `/reassessments`, `/incidents`, `/defense-recommendations`, `/explanation`, `/rl-evaluations`, `/applications`, `/ingest-tokens`, `/defense-health`, `/defense-graph`, `/reviews`, `/live-policy-decisions`, `/rl-readiness`, `/control-grants`, `/control-channel`, `/control-authorizations`, `/live-controls`, `/emergency-stop`, `/first-responder/*` ([contracts](../docs/planning/LIVE_DEFENSE_FRONTEND_API.md)) |
| `/projects/[id]/live-defense` customer Live Defense dashboard (Overview, Investigation, Explainability, Staging apps, Controls, Readiness). Live mode is control-plane records plus owned-test controls. RL-gated apps are server-enforced shadow-only. Analyst accept does not execute. Isolate is never offered. Rate-limit and exact-source block require a separate authorization endpoint after the deterministic gate, labelled **OWNED TEST ENVIRONMENT**. Adapter HTTP 200 is not effectiveness. Readiness is backend `GET /rl-readiness` (not a frontend flag). `?preview=layout` swaps in labelled sample chrome | `/projects/{id}`, `/events`, `/reassessments`, `/incidents`, `/defense-recommendations`, `/explanation`, `/rl-evaluations`, `/applications`, `/ingest-tokens`, `/defense-health`, `/defense-graph`, `/reviews`, `/live-policy-decisions`, `/rl-readiness`, `/control-grants`, `/control-channel`, `/control-authorizations`, `/live-controls`, `/emergency-stop` ([contracts](../docs/planning/LIVE_DEFENSE_FRONTEND_API.md)) |
| `/projects/[id]/live-defense` customer Live Defense dashboard (Overview, Investigation, Explainability, Staging apps, Controls). Live mode is control-plane records plus owned-test controls. Analyst accept does not execute. Isolate is never offered. Rate-limit and exact-source block require a separate authorization endpoint after the deterministic gate, labelled **OWNED TEST ENVIRONMENT**. Adapter HTTP 200 is not effectiveness. `?preview=layout` swaps in labelled sample chrome | `/projects/{id}`, `/events`, `/reassessments`, `/incidents`, `/defense-recommendations`, `/explanation`, `/rl-evaluations`, `/applications`, `/ingest-tokens`, `/defense-health`, `/defense-graph`, `/reviews`, `/live-policy-decisions`, `/control-grants`, `/control-channel`, `/control-authorizations`, `/live-controls`, `/emergency-stop` ([contracts](../docs/planning/LIVE_DEFENSE_FRONTEND_API.md)) |
| `/projects/[id]/live-defense` customer Live Defense dashboard (Overview, Investigation, Explainability, Staging apps, Controls, First responder). Live mode is control-plane records plus owned-test controls plus server-enforced first-responder modes (OFF / MONITOR\|SHADOW / ASSISTED RESPONSE; AUTO-EXECUTE rejected). Analyst accept does not execute. Isolate is never offered. Rate-limit and exact-source block require a separate authorization endpoint after the deterministic gate, labelled **OWNED TEST ENVIRONMENT**. Adapter HTTP 200 is not effectiveness. An RL recommendation is not executed defense. `?preview=layout` swaps in labelled sample chrome | `/projects/{id}`, `/events`, `/reassessments`, `/incidents`, `/defense-recommendations`, `/explanation`, `/rl-evaluations`, `/applications`, `/ingest-tokens`, `/defense-health`, `/defense-graph`, `/reviews`, `/live-policy-decisions`, `/control-grants`, `/control-channel`, `/control-authorizations`, `/live-controls`, `/emergency-stop`, `/first-responder/*` ([contracts](../docs/planning/LIVE_DEFENSE_FRONTEND_API.md)) |
| `/projects/[id]/live-defense` customer Live Defense dashboard (Overview, Investigation, Explainability, Staging apps, Controls, Readiness, First responder). Live mode is control-plane records plus owned-test controls plus server-enforced first-responder modes (OFF / MONITOR\|SHADOW / ASSISTED RESPONSE; AUTO-EXECUTE rejected). RL-gated apps are server-enforced shadow-only. Analyst accept does not execute. Isolate is never offered. Rate-limit and exact-source block require a separate authorization endpoint after the deterministic gate, labelled **OWNED TEST ENVIRONMENT**. Adapter HTTP 200 is not effectiveness. An RL recommendation is not executed defense. Readiness is backend `GET /rl-readiness` (not a frontend flag). `?preview=layout` swaps in labelled sample chrome | `/projects/{id}`, `/events`, `/reassessments`, `/incidents`, `/defense-recommendations`, `/explanation`, `/rl-evaluations`, `/applications`, `/ingest-tokens`, `/defense-health`, `/defense-graph`, `/reviews`, `/live-policy-decisions`, `/rl-readiness`, `/control-grants`, `/control-channel`, `/control-authorizations`, `/live-controls`, `/emergency-stop`, `/first-responder/*` ([contracts](../docs/planning/LIVE_DEFENSE_FRONTEND_API.md)) |
| `/projects/[id]/live-defense` customer Live Defense dashboard (Overview, Investigation, Explainability, Staging apps, Controls, First responder). Live mode is control-plane records plus owned-test controls plus server-enforced first-responder modes (OFF / MONITOR\|SHADOW / ASSISTED RESPONSE; AUTO-EXECUTE rejected). Scout/Diagnose may show a real authorized agent loop (cost/latency/integrity); that is not confirmation. Analyst accept does not execute. Isolate is never offered. Rate-limit and exact-source block require a separate authorization endpoint after the deterministic gate, labelled **OWNED TEST ENVIRONMENT**. Adapter HTTP 200 is not effectiveness. An RL recommendation is not executed defense. `?preview=layout` swaps in labelled sample chrome | `/projects/{id}`, `/events`, `/reassessments`, `/incidents`, `/defense-recommendations`, `/explanation`, `/rl-evaluations`, `/applications`, `/ingest-tokens`, `/defense-health`, `/defense-graph`, `/reviews`, `/live-policy-decisions`, `/control-grants`, `/control-channel`, `/control-authorizations`, `/live-controls`, `/emergency-stop`, `/first-responder/*` ([contracts](../docs/planning/LIVE_DEFENSE_FRONTEND_API.md)) |
| `/projects/[id]/live-defense` customer Live Defense dashboard (Overview, Investigation, Explainability, Staging apps). Live mode is control-plane records: events, NIST incidents, simulation-derived recommendations, stored explanations, recorded health/topology, staging-app onboarding (issue/rotate/revoke/disconnect), analyst reviews, exportable pilot report. Health stays unknown without a recorded check. No apply/isolate/rate-limit; accept does not execute. `?preview=layout` swaps in labelled sample chrome | `/projects/{id}`, `/events`, `/reassessments`, `/incidents`, `/defense-recommendations`, `/explanation`, `/rl-evaluations`, `/applications`, `/ingest-tokens`, `/defense-health`, `/defense-graph`, `/pilot-report`, `/reviews` ([contracts](../docs/planning/LIVE_DEFENSE_FRONTEND_API.md)) |
| `/live-defense-pilot` isolated sample layout of the same views, no session and no API calls, permanent SAMPLE LAYOUT banner | Nothing: `lib/live-defense-preview.ts` |
| `/projects/[id]/expectations` snapshot picker (ready snapshots only), behavioral expectations with status filters, sources at file:line, confirm / conflicting / reject with a reason and `expected_revision` (409 on stale view), decision history | `/repositories/{id}/snapshots`, `/snapshots/{id}/expectations`, `PATCH /snapshots/{id}/expectations/{exp}`, `/decisions` |
| `/projects/[id]/incidents` open (record-only note; simulated by default, labelled), NIST lifecycle rail, triage, assign owner, transitions, two-person approval gates (requester cannot decide), timeline with CSF refs; closure panel: link findings and accepted verifications, closure criteria satisfied with hash-pinned patch/regression artifacts, lessons learned (Fortify regression evidence), communications (RS.CO-02/03, RC.CO-03), closure readiness list | `/projects/{id}/incidents`, `/incidents/{id}`, `/incidents/{id}/actions` |
| `/projects/[id]/settings` project name, languages, default + monitoring budgets, trigger policy (enabled, coalescing interval, record-only globs); validated client-side, owner-only on the backend; trigger policy is labelled 24/7 defense | `PATCH /projects/{id}` |
| `/lab` Defense Lab: signed-in users can toggle 24/7 defense for their projects when the backend is available (hidden in demo-only mode); plain-language header and a one-line explainer with the controlled-test-environment caveat; **headline held-out scores first** (three key numbers for the trained defender versus baselines, then one compact score card per headline metric with 95% CI and a computed trade-off note); then the recorded-episode replay (status bar labelled SIMULATION · RECORDED EVALUATION, stable network map with service icons, signal states and action badges, story panel — what's happening / what the AI did / why (reasoning shown as not recorded) / effect — service inspector, key moments, playback with speed and an observed-signal timeline). Provenance stays one click away in a collapsed footer. Hidden attacker stage only as an opt-in, labelled overlay | `rl-defense/results/protocol-*/defense_lab.json`, validated server-side against `rl-defense/fixtures/defense_lab.schema.json`; optional project list via `/projects` |
| `/demo` **demo account** ([ADR-0013](../docs/decisions/0013-labelled-simulated-demo.md)), not linked publicly: sign in on `/access` with the access code (`DEMO_ACCESS_CODE`, server-only env; unset = closed). `proxy.ts` keeps `/demo` closed without the signed httpOnly cookie. Opens the Lumen Pay sample account: organization → repository → recent commits; per commit an animated replay with Overview, Code review, Seven agents and Defense Lab. A small "Demo account · sample data" tag and footer on every page | Nothing: sample data in `lib/demo/` (no backend, GitHub or model calls) |

Same-origin API: `app/api/v1/[...path]` proxies to `BACKEND_ORIGIN` at request time
(no CORS; the backend's `SameSite=Lax` cookie and CSRF header work as designed;
SSE streams unbuffered). Types and runtime validation come only from
`shared/generated` through [`transport/`](transport/README.md).

Honesty rules enforced in code: backend offline → "Backend unreachable"; a response
that fails the shared JSON Schema → "Backend response rejected" (never rendered);
non-`live` `provider_mode` runs are labelled "Not a live-provider run"; fixture mode
exists only behind `NEXT_PUBLIC_DEMO_FIXTURES=1` and shows a permanent DEMO DATA banner.

## Plain-language layer (UX clarity)

For readers with no programming, security or RL background, see
[UX_CLARITY_CHECKLIST.md](UX_CLARITY_CHECKLIST.md): the overview's pillar loop, the eight-agent
relay with its human approval gate, before/after fix evidence, inline glossary terms, and a
Defense Lab that explains itself. Technical detail (orchestrator states, usage, event log,
every RL metric and provenance) is kept, one click away. All counts come from records.

## Interface finish

`app/interface.css` supplies the shared editorial finish: workspace title hierarchy, numbered organization cards, precise repository rows, restrained charcoal panels, ember selection edges and a compact replay frame. It retains the hero and scroll timelines in `globals.css`. The Projects header link stays current across `/connect` and its descendants. The desktop/mobile browser test uses explicit GitHub metadata fixtures and the real session/router.

## Landing, header and motion

- `components/explainer-video.tsx`: the landing's video chapter right after the hero (`public/videos/ari-explainer.mp4`). On `lg` with motion allowed it is a sticky 220vh track and scroll grows the player from an inset card to full size; it plays muted while on screen, pauses off screen, and offers sound, full screen and a download.
- `components/hero.tsx`, `intro-loader.tsx`, `story.tsx`: the landing's hero, once-per-sign-in
  intro and scroll-told chapters. Chapters pin and scrub only at `lg` with motion allowed;
  otherwise they render their final, readable state.
- `components/smooth-scroll.tsx` (root layout): Lenis-style momentum for mouse wheels and
  trackpads, frame-rate independent, no dependency. Native for touch, keyboard, scrollbar,
  zoom, horizontal gestures, nested scroll areas and `prefers-reduced-motion`.
- Header: `components/account-link.tsx` sits top right and is always visible: **Sign in**
  when signed out (or the backend is unreachable), otherwise the GitHub login. On the
  landing, the other tabs fade in on scroll over the hero.
- `components/faq.tsx`: accessible accordion used on `/pricing`.

## Night surface (every page except the landing)

Pages without a full-screen hero stage (`.hero-full`: the landing and the sign-in screens)
carry the hero's look at lower intensity. Everything is in the "Night surface" section of
`app/globals.css`:

- **Tokens flip** under `:root:not(:has(.hero-full))`: `bg`, `panel`, `panel-2`, `line`,
  `ink`, `muted`, `faint`, `control` and the status colours get night values, so anything
  written against `bg-panel` / `text-ink` / `border-line` follows with no component change.
  Use `--color-night` (never flips) for a surface that must stay dark, and `--color-ember`
  for brand-coloured text. Night contrast is asserted in `tests/unit/contrast.test.ts`.
- **Ambient light**: `components/ambient.tsx` (root layout) is a fixed layer of drifting
  ember orbs, a faint grid and grain, with a slow scroll parallax (`--sy`). It also sets
  `--gx/--gy` on the panel under the mouse for its glow.
- **Motion**: page titles (`PageTitle` → `.page-title`), ink bands (`.hero-ink`) and the
  project header rise in with the hero's blur; `Panel` rises once when first scrolled into
  view (`useRevealOnScroll` in `components/reveal.tsx`); primary buttons, project tabs and
  bordered link cards glow on hover; `Loading` shows an ember pulse. All of it stops under
  `prefers-reduced-motion`.

## Commands

Node ^22.22.2 or ≥ 24.15 (jsdom 30 for the unit tests). From `frontend/`:

```sh
npm ci --prefix transport && npm run build --prefix transport   # after every contract regeneration
npm ci
npm run lint && npm run typecheck && npm test && npm run build
BACKEND_ORIGIN=http://127.0.0.1:8000 NEXT_PUBLIC_DEV_LOGIN=1 npm run dev   # with `make dev` running the API
../.venv/bin/python tests/e2e/stack.py                                # browser e2e, see below
```

`NEXT_PUBLIC_DEV_LOGIN=1` only shows the development sign-in form; the backend still
refuses it unless `APP_ENV` is development/test and `AUTH_DEV_LOGIN_ENABLED=true`.

## Browser e2e (`tests/e2e/stack.py`)

Starts real FastAPI + PostgreSQL migrations + the job worker + `next start`, seeds two
org owners, a project and the `examples/payments-lite` repository binding (an operator
registry the UI cannot set), then runs Playwright. The journey test drives the rendered
UI: request scope → second user approves → start assessment → live SSE timeline →
second user approves remediation → reviewer + seven security agents complete → security
finding evidence chain → hash-checked diff → Verify accepted → state survives reload.
Also: signed GitHub push appears in monitoring, create project / connect repo,
backend-offline and contract-violation states, Defense Lab, bundle path refusal.

Model replies come from the backend's own TEST-ONLY scripted transports (the same ones
as `backend/tests/test_unified_assessment.py`), so every agent run is persisted with
`provider_mode="fake"` and the UI says so. Everything else — HTTP, cookies/CSRF, queue,
git checkout, reviewer test execution, local-dev sandbox checks, Patch/Verify, artifacts,
SSE — is real. No API key is read. Screenshots of the executed run are written to
`test-results/evidence/`.

Env: `TEST_DATABASE_URL` (admin URL of an existing Postgres; default embedded pgserver,
which cannot start from a path containing spaces), `E2E_BROWSER_CHANNEL=chrome` to use an
installed Chrome instead of Playwright's Chromium, `E2E_SKIP_BUILD=1` to reuse `.next`.

## Known gaps (not hidden in the UI)

- No backend endpoint serves the Defense Lab bundle; the Lab reads the committed artifact.
- A finding whose assessment has no indexed snapshot can only show expectation ids.
- Lessons learned stay blocked, and the closure panel says so, when Fortify did not record a regression-test artifact.
- GitHub links stay off when an assessment scope names more than one repository. Findings still do not carry a repo id.
- Live-provider (`provider_mode=live`) runs have not been exercised through the UI.
- The project journey cannot see a phase the latest assessment's event log did not record. Phase views follow the events log's `next_cursor` from `after=0`; they stop after 20 pages (10,000 events) and then say a later phase may be unmarked.
- Live Defense still has no live-incident machine (DETECTED→EXECUTING) and no AUTO-EXECUTE. Shadow `LivePolicyDecision` rows from authenticated staging ingest are implemented (NOT EXECUTED). Phase 7A first-responder modes, capability registry, and analyst handoff are implemented; agent steps are permission-gated and marked unverified rather than claimed live-Claude success. Health is never inferred from HTTP 200. [LIVE_DEFENSE_FRONTEND_API.md](../docs/planning/LIVE_DEFENSE_FRONTEND_API.md).

Assessment start retries keep their key after network errors. An explicit
IDEMPOTENCY_CONFLICT offers “Prepare a new assessment action”: it sends nothing
until Start assessment is pressed again. Check the existing assessment list first;
a new action can create another assessment. Keys are scoped to the current project.

## ARI identity and landing page

The owner-supplied ant PNG is preserved unchanged at `public/brand/ari-ant.png`
and `app/icon.png` (favicon). The on-screen mark is just the ant:
`public/brand/ari-ant-mark.png` is a transparent, tightly cropped cut-out derived
from the original. `LogoMark` draws it as a CSS mask filled with `currentColor`;
`size` is its height and width follows the ant's aspect ratio. `Wordmark` pairs it
with **ARI** in locally bundled Chakra Petch 700. Other text keeps Inter, Space
Grotesk and IBM Plex Mono. Font provenance, licenses and hashes are in `app/fonts/`.


The shared header, logo and ordinary navigation controls are transparent; only
Pricing keeps its orange fill. `HeaderContrast` adapts each control to the nearest
painted surface beneath it (white text/ant on dark, ink on light), including light
cards within dark sections. Hover adds a restrained translucent tint.
`SiteNavigation` shows only the logo and account entry at the homepage top; Projects,
Defense Lab, Demo and Pricing reveal at `scrollY >= 100` with a 250ms fade and 8px
horizontal movement, and hide below that threshold. Reserved layout slots keep the
account entry anchored; hidden links are inert and excluded from accessibility.
Phones have a compact Menu disclosure for access before scrolling, including Escape
to close and restore focus. Scroll reveal closes that disclosure, so returning to the
top hides the links again. Reduced motion removes the fade/translation. Other pages
always show full navigation. The landing header stays fixed; other routes use sticky
positioning. The hero centers “FIND THE FLAW. / PROVE THE FIX.”, the demo and how-it-works
links, and the seven workflow pills. Its headline uses a bold warm-white first line and a lighter-weight warm-ivory second line, tighter line spacing and responsive mobile sizing. Pointer spotlight, entrance animation and reduced-motion behavior remain; there is no text glow. The in-page anchor has an offset for the floating controls.

The footer uses the same supplied ant and local wordmark as the header. The intro's progress,
reduced-motion, automated-browser and once-per-sign-in behavior remain in place.

Brand/intro unit tests cover accessibility, source integrity and skip behavior.
`tests/e2e/brand.spec.ts` checks real image loads, sign-in, desktop/mobile logo
bounds, header navigation, the served favicon and intro/reduced-motion behavior.
Set `ARI_LOGO_SCREENSHOTS` to an external directory for optional screenshot proof.


## Provenance labels (2026-10-17)

Real GitHub commit metadata is labelled separately from illustrative analysis. The monitoring attack drill uses real project routes and detection, synthetic counters, and either Claude or scripted planning; it sends no service attack traffic and only recommends responses. Its pending state does not assert that Claude was called.

The access-gated sample account distinguishes its sample data from the separately labelled saved live-run panel. The panel discloses the historical run's missing unpatched-snapshot regression execution, so it does not establish red-to-green proof. See [the demo runbook](../docs/demo/RUNBOOK.md) for current access, setup and evidence boundaries. No new live provider run was performed for these wording changes.


The homepage ends after its proof section; the former closing invitation (and its dedicated styles) have been removed. Evaluation selection elsewhere uses aligned native controls.

Sign-in screens share a centered form over subtle decorative orbits; reduced motion disables orbit animation.

User-facing lab copy describes controlled evaluation runs and isolated test networks. Synthetic drills and illustrative walkthroughs retain explicit provenance.


Homepage wheel scrolling stops once at the expanded walkthrough video. The arriving gesture is absorbed until a new gesture (180ms idle) or a 1.2-second maximum. A new wheel gesture, upward scrolling, keyboard navigation or Continue exploring can leave immediately. Touch and reduced-motion scrolling remain native.


The walkthrough zoom uses smoothstep scroll progress and a 1.5-second CSS transition so hard wheel gestures do not snap it to full size. Reduced motion disables the transition.


Landing: the original centered hero, scroll-driven problem timeline, expanding process cards and finding walkthrough are restored. Wheel scrolling uses the original momentum behavior without the redesign's reading-distance cap. `LandingFooter` retains Ari’s charcoal/burnt-orange/cream horizon design.

Footer correction: use a conventional aligned footer with Ari branding/tagline on the left, two navigation columns on the right, and a separated copyright/back-to-top row. Stack branding above the link columns on mobile. Preserve Ari colors and restored hero/scroll animations. Validation recorded in PR.

Refine the landing headline with tighter two-line rhythm, a stronger cream first line and a lighter warm-ivory second line. Preserve wording, centered layout, pointer spotlight and scroll animations. Branch `fix/adam/hero-headline`; validation in PR.

Add staggered proof-card/footer entrances and subtle button, workflow-chip, process-evidence and footer-link motion. Reduce hero ants from 10–34 to 6–16. Honor reduced motion; preserve existing scroll traversal. Branch `feat/adam/landing-motion`; validation in PR.

Simplify project navigation to Code review and 7 agents. Move settings, scopes, approvals and other records into More; lead with run/replay and starting a check; disclose findings, repositories and progress on demand. Keep all routes and approval requirements. Branch `feat/adam/project-simplify`; validation in PR.

Keep organization/repository/commit navigation scoped to the selected repository and SHA. Real commit runs stay inline in a two-tab reviewer/seven-agent workspace with demo-style agent rail and recorded replay; no redirect to shared project history. Resolve the repository’s actual project, search older run pages, reject mismatched results and retain scoped remediation approvals. Optional narrated quick reads are explicitly requested and separate from executed records. Branch `fix/adam/commit-workspace`; validation in PR.


Connected repo/commit pages expose a per-repository Automatic runs switch. ON shows background-run status; OFF exposes manual Run. The backend schedules independently of browser visits. Quick reads use Haiku 5.5 only. See backend README and issue #380 for prerequisites and quotas.
