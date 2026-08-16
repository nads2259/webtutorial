"""Identity application layer: capabilities and the ports they depend on.

The application layer depends only on the pure :mod:`..domain` and on abstract ports (this
package). Concrete infrastructure — the OIDC provider, persistence, MFA and federation/SCIM —
is injected as adapters at the composition root (dependency inversion, rule 20 §D).
"""

from __future__ import annotations
