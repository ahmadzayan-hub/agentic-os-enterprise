# Agentic OS Enterprise

A governed enterprise AI control plane for operating agents, approvals, knowledge, policy, security, and audit evidence.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 8080 by default)
- `pnpm --filter @workspace/agentic-os run dev` — run the web console (port 22832 by default)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `pnpm --filter @workspace/db run generate` — generate a reviewed versioned migration
- `pnpm --filter @workspace/db run migrate` — apply committed migrations
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `artifacts/agentic-os` — Vite + React console and preserved product styling
- `artifacts/api-server` — Express API, authentication, authorization, persistence, and audit behavior
- `lib/db/src/schema` — PostgreSQL schema
- `lib/api-spec/openapi.yaml` — shared API contract and generated-client source

## Architecture decisions

- PostgreSQL is the source of truth for users, sessions, mutable business records, and audit events.
- GitHub's `claude/agentic-os-enterprise-v3.1-ogi9cq` branch is the authoritative source; reconcile by merge, never reset or discard divergent Replit work.
- Demo records are seeded idempotently and never overwrite operator changes.
- Session identifiers are random, stored only as hashes, and delivered in HTTP-only cookies.
- The active runtime is Vite/React plus TypeScript/Express; archived Next.js/Python topology is not restored.

## Product

Operators can launch governed runs, decide approvals, inspect agent contracts, ingest and search knowledge, review governance/privacy posture, operate security kill switches, and inspect audit evidence.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- Push development schema changes with `pnpm --filter @workspace/db run push`.
- Use the managed workflows for the proxied preview. Package builds also have safe local defaults.
- Demo identities and sample records are available only when `AGENTIC_DEMO_MODE=true`; the managed development command enables it explicitly and production fails closed.
- Apply committed migrations before startup. Do not use schema push commands in post-merge or production workflows.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
