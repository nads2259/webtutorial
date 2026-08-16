"""WebAuthn/passkey verifier adapter backed by the ``py_webauthn`` library (infra, rule 10).

Wraps ``py_webauthn`` behind :class:`WebAuthnVerifierPort` so the pure domain and the application
capabilities never import the library (LAW-12): the challenge is issued and later verified, the
relying-party id and origin are checked, and a signature-count regression on authentication (the
tell-tale of a cloned authenticator, WebAuthn §6.1.1) is rejected. Library failures surface as the
uniform :class:`MfaVerificationFailed` so callers cannot distinguish the precise cause
(anti-enumeration, docs/07 §14).
"""

from __future__ import annotations

import base64
from collections.abc import Mapping

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..application.ports import WebAuthnRegistrationVerification
from ..domain.errors import MfaVerificationFailed


class PyWebAuthnVerifier:
    """A :class:`WebAuthnVerifierPort` implemented with the ``py_webauthn`` library."""

    def __init__(self, *, rp_id: str, rp_name: str, origin: str) -> None:
        self._rp_id = rp_id
        self._rp_name = rp_name
        self._origin = origin

    @property
    def rp_id(self) -> str:
        return self._rp_id

    @property
    def origin(self) -> str:
        return self._origin

    def build_registration_options(
        self, *, subject_id: str, user_name: str, existing_credential_ids: tuple[str, ...]
    ) -> tuple[str, bytes]:
        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_name=user_name,
            user_id=subject_id.encode("utf-8"),
            # Passkeys preferred: request a discoverable resident key with user verification.
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(cid))
                for cid in existing_credential_ids
            ],
        )
        return options_to_json(options), options.challenge

    def verify_registration(
        self, *, response: Mapping[str, object], expected_challenge: bytes
    ) -> WebAuthnRegistrationVerification:
        try:
            verified = verify_registration_response(
                credential=dict(response),
                expected_challenge=expected_challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
            )
        except (InvalidRegistrationResponse, ValueError, KeyError) as exc:
            raise MfaVerificationFailed("webauthn.registration_invalid") from exc
        return WebAuthnRegistrationVerification(
            credential_id=_bytes_to_base64url(verified.credential_id),
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            aaguid=verified.aaguid,
        )

    def build_authentication_options(
        self, *, allow_credential_ids: tuple[str, ...]
    ) -> tuple[str, bytes]:
        options = generate_authentication_options(
            rp_id=self._rp_id,
            user_verification=UserVerificationRequirement.PREFERRED,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(cid))
                for cid in allow_credential_ids
            ],
        )
        return options_to_json(options), options.challenge

    def verify_authentication(
        self,
        *,
        response: Mapping[str, object],
        expected_challenge: bytes,
        credential_public_key: bytes,
        current_sign_count: int,
    ) -> int:
        try:
            verified = verify_authentication_response(
                credential=dict(response),
                expected_challenge=expected_challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=credential_public_key,
                credential_current_sign_count=current_sign_count,
            )
        except (InvalidAuthenticationResponse, ValueError, KeyError) as exc:
            raise MfaVerificationFailed("webauthn.assertion_invalid") from exc
        # Defence in depth: reject a non-incrementing counter even if the library allowed it, so a
        # cloned authenticator (WebAuthn §6.1.1) can never re-use or regress its signature count.
        if verified.new_sign_count and verified.new_sign_count <= current_sign_count:
            raise MfaVerificationFailed("webauthn.sign_count_regression")
        return verified.new_sign_count


def _bytes_to_base64url(raw: bytes) -> str:
    """Encode raw bytes as unpadded Base64URL (the WebAuthn credential-id representation)."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


__all__ = ["PyWebAuthnVerifier"]
