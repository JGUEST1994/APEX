# 13. Security architecture

## Threats that matter most

1. Leaked broker or exchange credentials leading to unauthorised trades or withdrawals.
2. A bug, AI suggestion or compromised dependency placing orders outside risk limits.
3. Unauthorised access to the dashboard/API.
4. Loss or corruption of the database (positions, experiments, research).

## Controls

| Area | Control | Status |
|---|---|---|
| Real-money trading | No live mode exists; config rejects `live`/`real`/`production` | **Implemented + tested** |
| Secrets in config | Environment variables / `.env` (git-ignored), `SecretStr` so they never appear in reprs | **Implemented + tested** |
| Secrets in logs | `RedactingFilter` on every handler: key=value secrets and URL passwords are masked | **Implemented + tested** |
| Secrets at rest | Broker credentials stored encrypted (Fernet/AES-GCM) with a master key from the OS keyring or env; never in the DB in plain text | Phase 11 |
| Broker permissions | Trade-only API keys; withdrawals disabled wherever the venue supports it; IP allow-listing where offered | Policy; enforced at onboarding |
| AI isolation | AI module cannot reach execution; AI output is reviewed code/specs only | Design + review; import-linter in Phase 2 |
| Order safety | Risk service approval required; idempotent client order ids; duplicate-order guard; kill switch; halt on risk-service heartbeat loss | Phase 9 |
| AuthN/AuthZ | argon2 password hashes, short-lived JWTs, roles viewer/researcher/trader/admin, MFA for trader/admin | Phase 10 |
| Network | API binds to 127.0.0.1 by default (compose maps to localhost only); remote access via reverse proxy with TLS or a VPN (WireGuard/Tailscale) | Partially (bind defaults) |
| Database | Separate DB roles for app (DML) and migrations (DDL); no superuser for the app; TLS for remote DB | Phase 13 |
| Audit | `audit_log` table for logins, config changes, status changes, kill switch, orders | Table exists; writers in Phase 9–10 |
| Dependencies | Pinned lockfile, `pip-audit` in CI, Dependabot | Phase 2 |
| Backups | Nightly `pg_dump` to encrypted off-site object storage; restore drill each quarter | Phase 13 |
| Research ingestion | Only legally accessible sources; `licence_note` recorded per document; no circumvention of access controls | Field implemented; policy |

## Rules for contributors (human or AI)

- Never commit secrets; `.env` is ignored and `.env.example` holds placeholders only.
- Never log request headers or full broker payloads without passing them through redaction.
- Any change touching risk or execution needs tests and a second review before merge.
