# Analytics migrations

Per repository convention (as with the other first-party modules), the analytics module's Alembic
migration lives centrally under
`platform/python/src/northstar/adapters/persistence_sqlalchemy/migrations/` rather than in a
per-module directory:

- `versions/000014_analytics.py` — creates the owned `northstar_analytics` schema with the
  `event_definition`, `event` and `identity_stitch` tables and enables **FORCE ROW LEVEL SECURITY**
  (tenant isolation on `organization_id`) on every table. `down_revision = "000013"`. Reversible.
- `000014_analytics.json` — the registered migration manifest (validated against
  `spec/contracts/schemas/migration-manifest.schema.json`).

Creating/applying a migration is an approval boundary (rule 80): it is verified up/down on the
ephemeral test database only and is never auto-applied to a shared environment.
