"""Northstar Enterprise module: federation + SCIM provisioning + LTI/xAPI interoperability.

Enterprise integration is delivered as ADAPTER capabilities (FR-IDN-006, FR-LRN-008), never a
fork of the identity core: a signature-verified external IdP assertion maps deterministically to
a Northstar subject through the existing identity capability, SCIM-shaped provisioning
create/update/deprovisions users and groups (deprovision reusing identity session invalidation),
and optional LTI launch + xAPI statement emission integrate learning with external tools. Every
provider sits behind a port (reference/signature-verified) so a real IdP/SCIM/LTI/LRS is a
drop-in adapter swap with no capability change.
"""
