# Identity module migrations

The identity module owns the `northstar_identity` PostgreSQL schema (subjects, users, external
identities, sessions, credentials). Its migration is authored in the platform's single Alembic
lineage so ordering across modules stays linear and observable (rule 80, LAW-16):

- **Revision:** `000003` (down_revision `000002`) —
  `platform/python/src/northstar/adapters/persistence_sqlalchemy/migrations/versions/000003_identity.py`
- **Manifest:** `northstar.identity:000003` —
  `platform/python/src/northstar/adapters/persistence_sqlalchemy/migrations/000003_identity.json`
  (owner `northstar.identity`, `change_class: schema`, reversible).

The upgrade creates the `northstar_identity` schema and its tables; the downgrade drops the schema
(CASCADE), leaving the kernel-owned `northstar_meta`/`northstar_runtime` schemas untouched. The
Core table shapes the adapters bind to live in
`northstar.modules.identity.adapters.tables.build_identity_tables` and mirror the migration exactly.

Applying migrations to a shared/non-local database is an approval boundary (rule 80) and is out of
scope for this task; integration tests run the migration only on a per-run ephemeral database.
