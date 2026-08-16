# Enterprise module contracts

The enterprise module reuses canonical framework contracts and the identity module's published
ports; it introduces no new wire contract of its own. Its capabilities exchange typed application
DTOs (see `application/capabilities.py`) over the kernel command/query buses, and it depends on:

- `spec/contracts/schemas/module-manifest.schema.json` — the `module.yaml` manifest (validated).
- `spec/contracts/schemas/migration-manifest.schema.json` — the `000021_enterprise.json` manifest.

Federation/SCIM/LTI/xAPI provider shapes (OIDC/SAML assertion, SCIM 2.0 resource, LTI 1.3 launch,
ADL xAPI statement) are represented as pure domain value objects and mapped explicitly to the
Northstar-native model; external terminology never corrupts the internal domain (FR-LRN-008).
