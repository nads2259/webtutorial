"""Northstar extension (plugin & theme) runtime module (docs/14, FR-EXT-001..008).

Schema-valid extension/theme/capability manifests that declare identity, capabilities and
permissions; trust tiers that gate which requested capabilities an extension may hold; install +
upgrade that verify a cryptographic signature + provenance and REJECT (fail closed) any
unsigned/forged/tampered/untrusted-publisher artifact before activation; disable/uninstall that
actually STOP execution and revoke grants; themes that change ONLY semantic tokens + declared
presentation slots and never an authorization decision; content-block extensions that define +
validate their own block schema; and a public catalog that requires a verified publisher.

Hexagonal (rule 10): the domain imports no infrastructure; provider/execution behaviour lives
behind ports in :mod:`.adapters`; there is one authoritative capability per action (LAW-04).
"""
