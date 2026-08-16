# Analytics contracts

The analytics module consumes these registered, canonical contracts (single source of truth in
`spec/contracts/`, which is read-only during the build):

- `analytics-event-definition/1.0.0` — `spec/contracts/schemas/analytics-event-definition.schema.json`.
  Every catalog event type MUST validate against this schema and MUST declare a purpose (FR-ANL-003).
  The infrastructure-free domain (`domain/model.py`) enforces the same invariants by construction; a
  contract test (`tests/modules/analytics/test_manifest.py`) independently validates a produced
  definition dict against the canonical JSON Schema.
- `domain-event/1.0.0` — `spec/contracts/schemas/domain-event.schema.json` (CloudEvents-aligned
  envelope; this module produces no integration events yet, `produced_events: []`).
- `module-manifest/1.0.0` — `module.yaml` validates against
  `spec/contracts/schemas/module-manifest.schema.json`.
- `migration-manifest/1.0.0` — migration `000014_analytics.json` validates against
  `spec/contracts/schemas/migration-manifest.schema.json`.

This module defines no new contract schemas; per repository convention it references the canonical
schemas in `spec/contracts/` rather than copying them.
