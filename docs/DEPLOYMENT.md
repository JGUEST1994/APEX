# Deployment

Phase 1 status: the local install (venv + SQLite or PostgreSQL) is tested. The Docker files are written but **not yet verified** because the build environment had no Docker daemon. Treat them as a starting point.

## Local (Windows or Linux)

See README quick start. On Windows use PowerShell and `.venv\Scripts\activate`.

## Docker Compose (Linux server / VPS)

```bash
cp .env.example .env            # set APEX_DB_PASSWORD=<strong password>
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

The API is published on `127.0.0.1` only. Expose it remotely through a TLS reverse proxy or VPN, never directly.

## Restart behaviour

Containers use `restart: unless-stopped`; the API applies migrations on start. When trading arrives (Phase 9) the engine will start in a halted, reconciling state and will not resume trading automatically if account state is uncertain.

## Backups (planned Phase 13)

Nightly `pg_dump -Fc` to encrypted off-site storage, with a periodic restore drill.
