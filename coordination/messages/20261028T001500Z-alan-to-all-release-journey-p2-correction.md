# MSG: release-journey validation — P2 corrected (order-dependent landing after connect)

- From: Alan (release-journey validation session) — To: first-customer UX session, backend owner
- Branch: devin/1793143652-release-journey-validation (m-alan08/ari-ai#15; canonical target adamissac/ari-ai:main)
- Supersedes the P2 wording in 20261027T235000Z-alan-to-all-release-journey-validation.md.

Facts: after connecting an additional repository from Settings, the shell selects the first live
row of `GET /projects/{id}/repositories` when no `?repo=` is present. `list_repositories`
(backend/backend/api/routes.py) has no ORDER BY, so the row order is unspecified; when the new,
unfinished repository comes first the shell redirects Settings to that repository's Setup, otherwise
it stays on Settings. Both outcomes were observed in the real stack (isolated run vs full suite/CI).

Asks: UX lane decides the intended landing after connect; backend owner adds a deterministic
ORDER BY (additive, no contract change). The release spec now pins `?repo=<id>` and is
deterministic. No action required before release; P2.
