# Email Agent — Project Memory

> Auto-loaded by OpenCode as project instructions. This is the durable context
> for this repository so a fresh session can continue without re-deriving
> decisions. Keep it high-signal; put detail in `docs/architecture.md`.
>
> **Last updated:** 2026-11-21 · **Phase:** 0 (design complete, no code yet)

---

## 1. What this project is

An independent **Email Agent**: a React/TypeScript SPA + FastAPI backend that
connects to a user's Gmail through delegated OAuth, answers mailbox questions
with visible tool activity, and performs narrowly-scoped **reversible** mailbox
actions only after explicit human approval, with verification and an audit trail.

**It is a separate product from `SemanticSearch_v1` (the completed RAG project).**
No shared repo, packages, config, database, CI, or Keycloak instance.

## 2. Hard constraints — do not violate

1. **Independence, with one recorded exception.** Never import from, deploy alongside, or
   share code/config/database/CI with `C:\Users\aman8\Repo\SemanticSearch_v1`.
   Sharing the *VM* is allowed. **The one deliberate exception is Keycloak** (realm
   `email-agent` on the co-tenant's instance) — see §3a. Nothing else is shared.
2. **The model never holds authority.** It can call read tools and `propose_*`
   tools only. `execute_*` is server-only, reachable only from the approval
   resume path, and requires a `PolicyGrant`.
3. **No provider *mutation* without policy.** `PolicyGrant` is minted only by
   `policy.authorize()`; the adapter's write methods take it as a parameter.
   **Reads need no grant** — only an authenticated user, a read-scoped
   connection, and the tool's hard bounds. Do not "fix" this asymmetry.
4. **No permanent delete, no send, no forward, no mailbox rules** in v1.
5. **Never persist email bodies.** IDs, thread IDs, labels, `sizeEstimate`,
   content hash only. `proposal_items` (IDs + prior labels) is allowed and
   required — it is not body content.
6. **Never store Keycloak tokens** — not in the browser, not in the session row.
   Keep `sub`, `email`, `roles` only (§ architecture §1).
7. **Never put Gmail tokens** in the browser, logs, traces, Keycloak, or model
   context.
8. **One approval path.** Approval = AG-UI `resume` payload. No second endpoint.
9. **Execution is single-writer per connection**: conditional status transition
   + advisory lock + atomic chunk claim. Never retry an `indeterminate` chunk
   without reading back first.
10. **Single-VM deployable.** Every image `linux/arm64`; total stack ≤ ~7 GB RAM.

## 3. Locked decisions

| Concern | Decision |
|---|---|
| Backend / frontend | FastAPI (Python) · React + TypeScript |
| App identity | **Keycloak — shared co-tenant instance**, realm `email-agent`, confidential client `email-agent-bff` |
| Session | **httpOnly cookie BFF** (`__Secure-ea_session`, `Path=/mail`), server-side sessions, CSRF header on mutations; **no token in JS** |
| Mailbox authority | **Google OAuth 2.0 + PKCE**, tokens encrypted in Postgres |
| Agent transport | **AG-UI** over SSE (POST + `fetch` stream), interrupts for approval |
| Frontend state | **Redux Toolkit** (no `oidc-client-ts`; login is a full-page redirect) |
| Database | **Postgres**, one database `email_agent`, Alembic |
| Packaging | **Docker Compose** |
| Deployment target | **OCI Always Free, `VM.Standard.A1.Flex`, arm64**, Ubuntu |
| Public edge | **Existing host nginx**, one DuckDNS hostname, path prefixes `/mail/` + `/mail/api/` |
| OAuth posture | **Testing status, DuckDNS, personal use** — 7-day token re-consent is accepted and handled in-product |

## 3a. Shared Keycloak — the accepted coupling

Email Agent does **not** run Keycloak. It uses the co-tenant's instance at
`https://capriman27.duckdns.org/auth`, realm `email-agent`.

- Provisioned only by `infrastructure/keycloak/provision-realm.sh` (`kcadm`, idempotent).
- Do **not** change the co-tenant's compose, `KC_HOSTNAME`, or `/auth` path.
- Consequence: co-tenant down ⇒ our login down. Accepted for a personal project.
- The co-tenant's Postgres volume now holds auth for both products — never delete it.
- The API must start and stay up while Keycloak is unreachable (lazy JWKS, `/healthz` vs `/readyz`).

## 4. Where things live

- `docs/architecture.md` — the authoritative design (v2, 24 sections). Read this
  before making structural changes.
- `docs/threat-model.md`, `docs/api.md`, `docs/runbook.md`, `docs/decisions/` —
  planned, not yet written.
- Repo layout is defined in `docs/architecture.md` §2.
- Related but **separate** project for reference only:
  `C:\Users\aman8\Repo\SemanticSearch_v1` (see §6 for what we learned from it).

## 5. Current status

- ✅ Architecture v2.1 written and validated (see §7 for verified corrections).
- ✅ Target environment inspected (co-tenancy, ports, firewall).
- ✅ Decisions D1–D5 resolved (§8).
- ⬜ Phase 0: repo scaffold, CI, compose skeleton, Keycloak realm, OCI/DNS/TLS.
- ⬜ Phase 1: Keycloak login (shared realm) + Gmail read-only + AG-UI streaming + Redux UI.
- ⬜ Phase 2: evals/injection corpus, rate limits, retention.
- ⬜ Phase 3: approved reversible writes (propose/interrupt/execute/verify/undo).
- ⬜ Phase 4: VM deploy, monitoring, backups, runbook.

## 6. Verified environment facts (2026-11-21)

**Existing co-tenant on the Oracle VM (`SemanticSearch_v1`):**

- Host **nginx owns `:80`/`:443`** with certbot TLS on a **DuckDNS** hostname
  (`capriman27.duckdns.org`), routing by **path**: `/` → `127.0.0.1:8080`,
  `/api` → backend, `/auth` → Keycloak (`KC_HTTP_RELATIVE_PATH=/auth`).
- Containers publish to **loopback only** (`127.0.0.1:8080:80`). Good pattern.
- Keycloak **26.2**, `start --http-enabled=true --import-realm`, shares the
  **application Postgres database** (anti-pattern we must not copy).
- Uses deprecated `KEYCLOAK_ADMIN`; `depends_on: service_started` (weak gating).
- Realm export **contains plaintext user passwords** and
  `directAccessGrantsEnabled: true` (anti-patterns we must not copy).
- `iptables` has a final `REJECT`; ACCEPT rules were inserted positionally
  (`-I INPUT 5`). Fragile — do not touch, and add no new public ports.
- VM is `VM.Standard.A1.Flex`, **aarch64/arm64**.

**Port allocation for Email Agent (no collisions):**

| Purpose | Address |
|---|---|
| Host nginx | `:80`, `:443` (existing — do not bind) |
| SemanticSearch frontend | `127.0.0.1:8080` (existing) |
| Email Agent web (static SPA) | `127.0.0.1:8081` |
| Email Agent api | `127.0.0.1:8001` |
| Keycloak | **none** — we run no Keycloak; the co-tenant's is reached via `/auth` |
| Postgres / internal services | not published |

**Public paths on `capriman27.duckdns.org`:** `/` and `/api` (co-tenant),
`/auth` (shared Keycloak), **`/mail/`** and **`/mail/api/`** (ours).

## 7. Verified gotchas (from live docs — do not re-derive)

**Gmail / Google OAuth**

- **Trash does not free storage.** Spam and Trash count against quota until
  emptied; emptying is permanent delete (out of scope). Never promise reclaimed
  space. Storage figures also lag 48–72 h.
- `gmail.readonly` **is a restricted scope**. Non-personal deployment needs
  verification + **annual third-party CASA assessment**.
- **`gmail.modify` permits send/compose.** No narrower write scope exists. This
  is a documented residual risk, mitigated by the tool registry — not by scope.
- **Testing status ⇒ 7-day refresh tokens** (External user type, any scope
  beyond profile/email). **Decision: accepted.** This project stays in Testing
  on DuckDNS with no budget, so the product must treat weekly re-consent as a
  normal flow (clear "Reconnect Gmail", one click, no lost history). Revisit
  only if it ever needs to serve anyone else.
- Password change invalidates Gmail refresh tokens. Handle `invalid_grant`.
- Refresh-token cap: 100 per Google account per client ID; older ones are
  silently invalidated. Don't re-consent unnecessarily.
- Quota: **6,000 units/min/user**. `messages.list` 5 · `messages.get` 20
  (**60** for full/raw/html) · `batchModify` 50 · `trash` 20.
  ⇒ **~100 full message fetches per minute.** Two-stage retrieval is mandatory:
  `list` → `metadata` → top-25 `full`.
- `batchModify` is atomic per call and does not report per-message failures ⇒
  **the chunk is the unit of partial success** (chunk size 100–250, max 500/run).

**AG-UI**

- Approval uses the **interrupt** lifecycle: `RunFinished` with
  `outcome: {type:"interrupt", interrupts:[...]}`; resume via a **new run** with
  `resume: [{interruptId, status, payload}]`.
- Emit `StateSnapshot`/`MessagesSnapshot` **before** the interrupt, and persist
  the proposal **before** emitting it.
- Mark server-owned tools `metadata: {"ag-ui:tool-execution": "server"}`.
- **Interrupts are still draft** in the spec. Pin the version; isolate mapping.
- `EventSource` cannot POST ⇒ use `fetch` streaming.

**Keycloak 26.x — mostly the co-tenant's config now**

- **Ours to get right:** access tokens do **not** include our client in `aud` by
  default — add an **Audience protocol mapper** (`email-agent-api`); validate
  `aud` + `azp` (`email-agent-bff`) + `iss`.
- **Ours:** `iss` must be the **public** URL
  (`https://capriman27.duckdns.org/auth/realms/email-agent`), including the
  `/auth` context path. JWKS is fetched over that same public URL (hairpin
  through the local edge) — there is no internal Docker route between stacks.
- **Ours:** realm exports/scripts must contain **no users/passwords** and
  `directAccessGrantsEnabled: false`.
- **Not ours — do not change:** management port **9000** (`/health/ready`),
  `KC_PROXY_TRUSTED_ADDRESSES`, `KC_HOSTNAME`, `KC_BOOTSTRAP_ADMIN_*`,
  `KC_HTTP_RELATIVE_PATH=/auth`. We never proxy or reference 9000.
- Its admin console is currently reachable under `/auth` — an accepted risk
  recorded in the threat model; hardening it is a coordinated change.

**Oracle / Docker**

- **arm64 only.** One amd64 image breaks the deploy.
- Firewall has **two gates**: OCI Security List **and** host `iptables`.
- A1 capacity is region/AD-dependent; may need retries.
- Always Free has no SLA and idle resources may be reclaimed.

## 8. Decisions — resolved 2026-11-21

| # | Decision | Resolution |
|---|---|---|
| D1 | Google OAuth posture | **Testing status, personal use.** No verification, no CASA, no spend. Weekly re-consent is an accepted, designed-for flow. |
| D2 | Domain | **Keep DuckDNS** (`capriman27.duckdns.org`). No purchase. Blocks public launch by design. |
| D3 | Keycloak | **Share the co-tenant's instance**, new realm `email-agent` (§3a). ~2 GB and a JVM upgrade surface saved. |
| D4 | Session transport | **httpOnly cookie BFF.** Keeps the token out of JS (this app renders attacker HTML), enables server-side revocation, and drops `oidc-client-ts` entirely. |
| D5 | Hostname/naming | **One hostname, path prefixes** `/mail/` (SPA) + `/mail/api/` (API). No wildcard cert needed. |

**Exit triggers — revisit only if:** the app must serve someone else (D1/D2),
the co-tenant's Keycloak becomes unstable (D3), or a real domain is bought (D5).

## 9. Next steps

1. Phase 0 scaffold: `backend/`, `frontend/`, `infrastructure/compose/`,
   `.github/workflows/`. (`AGENTS.md` and `docs/architecture.md` exist.)
2. Write `docs/threat-model.md` — T1–T4 outlined in architecture §11, plus the
   accepted risks: shared Keycloak, exposed admin console, 7-day tokens,
   role changes not reflected until re-login.
3. Compose skeleton: `web` (8081) + `api` (8001) + `postgres`, loopback-only
   ports, resource limits, `ROOT_PATH=/mail/api`, **no Keycloak service**.
4. `infrastructure/keycloak/provision-realm.sh` — idempotent `kcadm` script:
   realm `email-agent`, confidential client `email-agent-bff`, audience mapper,
   `directAccessGrantsEnabled: false`, redirect URI
   `https://capriman27.duckdns.org/mail/api/auth/callback`.
5. Host-nginx `location` templates for `/mail/` and `/mail/api/` (documented in
   `docs/runbook.md`, not committed as root config).
6. Frontend build with `base: '/mail/'` and router `basename: '/mail'`.
7. Register the Google OAuth redirect URI
   `https://capriman27.duckdns.org/mail/api/v1/connections/gmail/callback` —
   it must match the API route exactly.

## 10. Conventions

- Python: `ruff` + `mypy`; async everywhere in the API/adapter; Pydantic models
  for every tool boundary.
- TypeScript: strict mode; Redux Toolkit slices; no `any` at API boundaries.
- All provider mutations go through `PolicyGrant`. Tests assert this.
- Never log bodies, tokens, subjects, or addresses. Allowlisted fields only.
- Migrations via Alembic; one migration per PR.
- Commit messages: imperative, scoped (`api:`, `policy:`, `ui:`, `infra:`).

## 11. Session log

| Date | Change |
|---|---|
| 2026-11-21 | v1 plan reviewed (found trash-storage error, restricted-scope gap, `gmail.modify` scope issue, undefined approval seam, provider-idempotency error, quota infeasibility). |
| 2026-11-21 | v2 written to `docs/architecture.md` with Keycloak / Docker / Redux / OCI constraints; verified AG-UI interrupts, Gmail quota, restricted scopes, Keycloak 26 config. |
| 2026-11-21 | Inspected `SemanticSearch_v1`: host nginx owns 80/443 (so Caddy is dev-only), port plan fixed, Keycloak-sharing and DuckDNS/7-day-token risks identified, anti-patterns recorded. |
| 2026-11-21 | Created this memory file. |
| 2026-11-21 | Decisions D1–D5 resolved: Testing + DuckDNS, shared Keycloak, cookie BFF, path prefixes. Architecture bumped to v2.1 (§8 posture, §15 co-tenancy/topology, §1/§14 session, ADR 11–12). |
| 2026-11-21 | Review fixes (v2.2): read/mutation invariant split; `proposal_items` with exact IDs + prior labels; concurrency gates + crash reconciliation; callback route unified; **no Keycloak tokens stored**; CSRF bootstrap specified. |
