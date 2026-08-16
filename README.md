# Web Tutorial — a Northstar reference product

A runnable **tutorial product** built on the [Northstar framework](https://github.com/nads2259/framework).
It is assembled **purely by composition** over released framework modules — it forks nothing and adds no
business logic of its own. A learner can browse a domain → path → course of published content, resume where
they left off across devices, take a scored assessment, and earn a credential derived from explicit rules.

## What's inside

```
platform/python/src/northstar/   the framework runtime (kernel + modules + adapters + processes)
  └─ products/reference/          THIS product: profile (theme/config/taxonomy/SLOs) + assembly + seeder
apps/web/                         the learner web experience (Vite + React 19, accessible)
packages/ui-primitives/           accessible React primitives (WCAG 2.2 AA)
packages/design-tokens/           typed design tokens (contrast-checked)
packages/editor-adapter/          structured content editor (ProseMirror)
```

This distribution intentionally excludes the framework's build scaffolding (spec, task packets, Cursor
rules, the full test matrix, and sibling products such as Studio/Marketplace). It contains only what is
required to run the tutorial product.

## Architecture (inherited from Northstar)

- **Hexagonal / ports & adapters** — the domain imports no infrastructure; providers live behind ports.
- **One authoritative capability per action** — the UI is a *client* of capabilities, never a DB backdoor.
- **Tenant isolation** by forced Postgres Row-Level Security; **deny-by-default** authorization.
- **Immutable, versioned content**; completion/credentials derive from explicit rules over auditable evidence.

## Prerequisites

- Python **3.13**, Node **20+** with `pnpm`, and PostgreSQL **18 + pgvector** (Docker is easiest).

## Run it

```bash
# 1. Postgres 18 + pgvector (dev)
make db-up                     # or: docker run -d --name webtutorial-pg -e POSTGRES_PASSWORD=postgres \
                               #        -p 5432:5432 pgvector/pgvector:pg18

# 2. Python runtime
python3.13 -m venv .venv && . .venv/bin/activate
pip install -e platform/python -r requirements-dev.txt
export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/webtutorial"

# 3. One-touch bootstrap: doctor → migrate (alembic upgrade head) → seed → smoke journey
./northstar bootstrap --profile ci

# 4. API + web
python -m northstar.processes.api                 # backend (uvicorn → northstar.processes.api.asgi:app)
pnpm install && pnpm --filter @northstar/web dev  # learner UI → http://localhost:5173
```

The reference tutorial product itself is assembled purely by composition and seeded through released
capabilities:

```python
from northstar.products.reference.assembly import assemble_reference_product
from northstar.products.reference.seed import ReferenceProductSeeder

product = assemble_reference_product(database_url="postgresql+psycopg://postgres:postgres@localhost:5432/webtutorial")
# construct ReferenceProductSeeder with the assembled dependencies and call .seed(tenant="...")
```

> Entry points: the one-touch CLI is `./northstar` (`northstar.cli`); the API ASGI app is
> `northstar.processes.api.asgi:app`; the product lives in `northstar.products.reference`.

## License

See `LICENSE` and `NOTICE`. Built on Northstar; this product repository is composed from released modules.
