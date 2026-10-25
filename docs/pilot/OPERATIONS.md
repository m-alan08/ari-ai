# Pilot operations — incidents, support, recovery, escalation

Founder-led support for five customers. No on-call rotation is claimed;
response targets below are **commitments we staff for**, not marketing SLAs.

## Support ownership and channels

| Role | Who | Scope |
|---|---|---|
| Support owner | Adam Issac (pilot launch owner) | First contact for everything; triage, comms, deletion requests |
| Engineering escalation | Owning engineer per subsystem (see `coordination/OWNERSHIP.md`) | Fixes, sandbox/LLM incidents |
| Security escalation | Security/privacy workstream | Isolation, leakage, scope/boundary incidents |

Channel for the pilot: a dedicated support email / shared channel per
customer + in-app incidents. Every support request is logged against the
customer record in METRICS.md (support-burden metric).

## Incident reporting

**Customer → us:** customers report adverse events via the support channel
or `POST /projects/{id}/incidents` (owner-reported incidents are a first-class
record). We also treat these platform signals as candidate incidents: failed
assessments, repeated 5xx, scope-check violations, anomalous spend, ingest
rejections, sandbox policy violations.

**Us → customer (notification targets):**

| Severity | Example | Notify within | Channel |
|---|---|---|---|
| Sev-1 | Cross-tenant data exposure, scope bypass, credential leak, unauthorized code execution on host | 4 h acknowledgment, 24 h written summary | Direct email/call + written record |
| Sev-2 | Customer's assessment data lost, service down > 2 h, ingestion stopped silently | 1 business day | Email |
| Sev-3 | Single failed run, deferred jobs, flaky UI | Weekly report | Email/dashboard |

Incident records use the platform's NIST SP 800-61 Rev.3-aligned states
(reported → triaged → analysis → containment → eradication → recovery →
closed; `docs/security/NIST_INCIDENT_HANDLING.md`). Every state transition
and decision lands in the hash-chained audit log.

## Service recovery

- **Durable queue**: jobs survive worker restarts via Postgres leases
  (`SKIP LOCKED`, attempt counter, exhausted → failed visibly). Verified:
  one eval run transiently failed on a worker restart and stayed FAILED —
  failure semantics are honest.
- **Readiness**: `/readyz` reports database/queue/llm_mode/github_app —
  check it before customer sessions and after every deploy.
- **Deferred work**: rate/budget-limited monitoring runs defer with a
  `queued` status and resume automatically; nothing is silently dropped.
- **Cost recovery**: $50/day global auto cap + per-run budgets bound any
  runaway; a cap hit defers work, it does not break the service.

## Escalation routes

1. Support owner acknowledges per the table above.
2. Sev-1 → security escalation + freeze affected scope(s) (revoke scope or
   flip monitoring off — both are instant, code-enforced) + if live-defense
   is involved, the emergency-stop record exists for staging apps.
3. Customer notified with: what happened, what data/systems were touched,
   what we did, what they should do, follow-up time.
4. Post-incident: written record in the incident log, root cause, corrective
   action, metric update (support burden, failure counts).
