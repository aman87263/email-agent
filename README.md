# Email Agent

An independent Email Agent: a React/TypeScript SPA + FastAPI backend that connects to a user's Gmail through delegated OAuth, answers mailbox questions with visible tool activity, and performs narrowly-scoped **reversible** mailbox actions only after explicit human approval, with verification and an audit trail.

## Architecture Overview

- **Backend**: FastAPI (Python 3.12) with async SQLAlchemy, PostgreSQL
- **Frontend**: React 18 + TypeScript + Vite, Redux Toolkit, Tailwind CSS
- **Authentication**: Keycloak OIDC (shared instance, realm `email-agent`), httpOnly cookie BFF session
- **Mailbox Authority**: Google OAuth 2.0 + PKCE, encrypted refresh tokens in PostgreSQL
- **Agent Transport**: AG-UI over SSE (POST + fetch stream), interrupts for approval
- **Deployment**: Docker Compose on Oracle Cloud Always Free (Ampere A1 ARM64)
- **Public Edge**: Existing host nginx, path prefixes `/mail/` + `/mail/api/`

## Key Design Decisions

| Concern | Decision |
|---------|----------|
| Model Authority | Model can only `propose_*`; `execute_*` is server-only, requires `PolicyGrant` |
| Idempotency | Local pre-write ledger + read-back reconciliation + `indeterminate` state |
| Retrieval | Two-stage: `list` (5u) → `metadata` (20u) → top-25 `full` (60u) |
| OAuth Posture | Testing status, DuckDNS, 7-day token re-consent (accepted) |
| Storage | No email bodies persisted; only IDs, labels, sizes, hashes |
| Concurrency | Three gates: conditional status update + advisory lock + atomic chunk claim |

## Quick Start (Local Development)

```bash
# Prerequisites: Docker, Docker Compose

# 1. Clone and configure
git clone <repo>
cd email-agent

# 2. Copy environment template and fill in values
cp infrastructure/compose/.env.example infrastructure/compose/.env
# Edit .env with your credentials

# 3. Start local stack (includes Caddy for HTTPS)
cd infrastructure/compose
docker compose -f compose.yaml -f compose.local.yaml up -d

# 4. Access at https://localhost.mail/ (Caddy provides local HTTPS)
```

## Production Deployment (OCI Always Free)

```bash
# On the OCI VM (ARM64)
cd /opt/email-agent
docker compose -f infrastructure/compose/compose.yaml pull
docker compose -f infrastructure/compose/compose.yaml up -d
```

### Required Infrastructure

- Oracle Cloud Always Free VM: `VM.Standard.A1.Flex` (4 OCPU / 24 GB ARM64)
- Host nginx on `:80`/`:443` with certbot TLS on `capriman27.duckdns.org`
- Shared Keycloak instance at `/auth` (realm `email-agent` provisioned via `provision-realm.sh`)
- DuckDNS hostname: `capriman27.duckdns.org`
- OCI Security List + host iptables: port 443 only

### Port Allocation

| Service | Address |
|---------|---------|
| Host nginx | `:80`, `:443` |
| Co-tenant frontend | `127.0.0.1:8080` |
| Email Agent web | `127.0.0.1:8081` |
| Email Agent API | `127.0.0.1:8001` |
| Keycloak | *shared, via `/auth`* |

### Public Paths

- `/` → Co-tenant SPA
- `/api` → Co-tenant backend
- `/auth` → Shared Keycloak
- `/mail/` → Email Agent SPA
- `/mail/api/` → Email Agent API

### API Routes

`ROOT_PATH=/mail/api`; routes are written logically and the prefix is applied once.

```text
POST /auth/login                     # starts OIDC flow (browser navigation)
GET  /auth/callback                  # Keycloak redirect; sets session cookie
POST /auth/logout                    # CSRF-protected; clears session
GET  /v1/auth/session                # who am I (SPA bootstrap)

POST /v1/agent/runs                  # start OR resume a run (AG-UI SSE stream)
GET  /v1/agent/runs/{id}             # run state
GET  /v1/agent/runs/{id}/events      # replay: ?after=<seq> (JSON)
POST /v1/agent/runs/{id}/cancel

GET  /v1/connections
POST /v1/connections/gmail/start     # returns consent URL + state
GET  /v1/connections/gmail/callback  # browser redirect from Google
POST /v1/connections/gmail/enable-write
DELETE /v1/connections/{id}

GET  /v1/proposals/{id}
GET  /v1/executions/{id}
POST /v1/executions/{id}/undo

GET  /v1/audit-events
GET  /healthz  /readyz
```

Redirect URIs to register:

- Keycloak: `https://capriman27.duckdns.org/mail/api/auth/callback`
- Google: `https://capriman27.duckdns.org/mail/api/v1/connections/gmail/callback`

## Environment Variables

| Variable | Description |
|----------|-------------|
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `KEYCLOAK_CLIENT_SECRET` | Keycloak confidential client secret |
| `CSRF_SECRET_KEY` | 32+ byte secret for CSRF token derivation |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `KEK_BASE64` | Base64-encoded 32-byte Key Encryption Key |

Generate KEK: `openssl rand -base64 32`

## Keycloak Provisioning

```bash
# On the OCI VM with Keycloak admin credentials
export KC_ADMIN_PASSWORD=your-admin-password
./infrastructure/keycloak/provision-realm.sh
```

This creates:
- Realm `email-agent`
- Confidential client `email-agent-bff` with audience mapper for `email-agent-api`
- Role `email-agent-user`
- Redirect URI: `https://capriman27.duckdns.org/mail/api/auth/callback`

## Google OAuth Configuration

In Google Cloud Console:
1. Create OAuth 2.0 credentials (Web application)
2. Add authorized redirect URI: `https://capriman27.duckdns.org/mail/api/v1/connections/gmail/callback`
3. Configure consent screen with `gmail.readonly` and `gmail.modify` scopes
4. **Testing mode** — add your email as test user (100 user cap)
5. **Note**: Testing mode = 7-day refresh token expiry, weekly re-consent required

## Development

### Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
ruff check app tests
mypy app
pytest -v
```

### Frontend
```bash
cd frontend
npm ci
npm run dev      # Vite dev server on :3000
npm run build    # Production build
npm run lint     # ESLint
npm run test     # Vitest
```

## Project Structure

```
email-agent/
├── backend/
│   ├── app/
│   │   ├── api/           # FastAPI routes
│   │   ├── agent/         # Coordinator + AG-UI mapping
│   │   ├── tools/         # Read + propose tools
│   │   ├── execution/     # Server-only executor, ledger, verification
│   │   ├── providers/     # EmailProvider protocol + Gmail adapter
│   │   ├── policy/        # PolicyEngine, PolicyGrant
│   │   ├── auth/          # OIDC, session, encryption
│   │   ├── db/            # Models, repositories, Alembic
│   │   └── main.py
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── app/           # Store, provider, router
│   │   ├── features/      # Feature slices (auth, agent, approval, connections, audit, ui)
│   │   ├── components/
│   │   └── lib/
│   ├── package.json
│   ├── Dockerfile
│   └── nginx.conf
├── infrastructure/
│   ├── compose/           # Docker Compose + .env.example
│   ├── keycloak/          # provision-realm.sh
│   ├── edge/              # Caddyfile for local dev
│   └── deploy/            # OCI notes, backup/restore
├── docs/
│   ├── architecture.md    # Authoritative design
│   ├── threat-model.md
│   ├── api.md
│   └── runbook.md
└── .github/workflows/     # CI/CD
```

## Testing Strategy

- **Provider contract**: Recorded Gmail fixtures + live sandbox account
- **Policy**: Golden-file table tests for every `(tool, args, scopes, connection state) → decision`
- **Digest**: Property tests for key-order independence, unicode normalization
- **Structural**: Assert adapter write methods unreachable without `PolicyGrant`
- **Injection corpus**: Adversarial emails → proposals only, zero unapproved executions
- **Frontend**: Reducer tests, approval card snapshots, XSS sanitization

## Compliance & Launch Gates

| Gate | Status |
|------|--------|
| Domain | DuckDNS (accepted, blocks public launch) |
| Restricted scope verification | `gmail.readonly` is restricted — Testing mode accepted |
| CASA assessment | Annual, required for public launch — not applicable in Testing |
| OAuth posture | Testing, personal use — 7-day re-consent designed as product flow |

## License

MIT