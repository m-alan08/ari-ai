# hooks-lite

A small webhook receiver + job service used as an evaluation target.

## Behavior

- `POST /webhooks/github` requires a valid `X-Signature-256` HMAC and a fresh
  `X-Delivery-Epoch`; duplicate delivery ids are rejected.
- `POST /sessions/login` sets a Secure, HttpOnly, SameSite=Lax session cookie.
- `GET /jobs/{job_id}` requires an API session.
- `worker.fetch_artifact` downloads artifacts over verified TLS.
