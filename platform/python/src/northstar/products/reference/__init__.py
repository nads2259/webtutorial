"""The Northstar reference tutorial product — assembled purely by composing released modules.

Public surface:

* :data:`REFERENCE_PRODUCT_PROFILE` — the declarative product profile (theme/config/taxonomy/
  SLOs/seed data), infrastructure-free.
* :func:`assemble_reference_product` — compose the released module composition root and bind it to
  the profile (no fork, no new capability).
* :class:`ReferenceProductSeeder` — seed the tutorial/offer/campaign/analytics via released
  capabilities on the composed command bus only.
"""

from __future__ import annotations

from .assembly import AssembledReferenceProduct, assemble_reference_product
from .profile import REFERENCE_PRODUCT_PROFILE, ReferenceProductProfile
from .seed import ReferenceProductSeeder, SeedReceipt

__all__ = [
    "REFERENCE_PRODUCT_PROFILE",
    "AssembledReferenceProduct",
    "ReferenceProductProfile",
    "ReferenceProductSeeder",
    "SeedReceipt",
    "assemble_reference_product",
]
