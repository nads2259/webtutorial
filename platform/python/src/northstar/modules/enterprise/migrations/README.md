# Enterprise module migrations

The enterprise module owns the `northstar_enterprise` PostgreSQL schema. Its schema migration is
registered centrally with the shared Alembic history (a module owns its migrations; no cross-module
schema edits — rule 80, LAW-16):

- `platform/python/src/northstar/adapters/persistence_sqlalchemy/migrations/versions/000021_enterprise.py`
- Manifest: `platform/python/src/northstar/adapters/persistence_sqlalchemy/migrations/000021_enterprise.json`

Migration `000021` (down_revision `000020`) creates `enterprise_federation_mapping` and
`enterprise_provisioning_record` with forced tenant Row-Level Security, and is reversible.
