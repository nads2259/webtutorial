"""SQLAlchemy-backed MFA credential stores (schema ``northstar_identity``, infra — rule 10).

Implements :class:`TotpCredentialStorePort` and :class:`WebAuthnCredentialStorePort` over the Core
tables in :mod:`.tables` (mirrored by migration ``000005_mfa``). Writes go through the kernel's
transactional unit of work; all access is fully parameterised (rule 50). The TOTP ``last_used_step``
and the WebAuthn ``sign_count`` are the persisted anti-replay / anti-clone cursors.

The TOTP shared secret is a ``restricted`` auth secret, so it is **encrypted at rest** (rule 50,
per the cryptography-and-key-management spec §2): the store is the persistence trust boundary that
seals the base32 secret through the injected :class:`EncryptionPort` before insert and opens it
only when a credential is loaded to verify a code. The ``secret`` column therefore holds a base64
AEAD token (version‖nonce‖ciphertext+tag), never the base32 plaintext. The AEAD associated data
binds each ciphertext to its owning ``subject_id``/``credential_id`` so a token cannot be relocated
to another subject's row.
"""

from __future__ import annotations

import base64

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from northstar.kernel.security.ports import EncryptionPort

from ..application.ports import TotpCredentialStorePort, WebAuthnCredentialStorePort
from ..domain.mfa import TotpCredential, WebAuthnCredential
from .tables import IdentityTables


def totp_secret_aad(*, subject_id: str, credential_id: str) -> bytes:
    """Associated data binding a TOTP ciphertext to its owning subject + credential id."""
    return f"identity.totp.secret:{subject_id}:{credential_id}".encode()


def _row_to_totp(row: object, *, encryptor: EncryptionPort) -> TotpCredential:
    token = base64.b64decode(row.secret)
    secret = encryptor.decrypt(
        token, totp_secret_aad(subject_id=row.subject_id, credential_id=row.credential_id)
    ).decode("ascii")
    return TotpCredential(
        credential_id=row.credential_id,
        subject_id=row.subject_id,
        secret=secret,
        digits=row.digits,
        period=row.period,
        algorithm=row.algorithm,
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
        last_used_step=row.last_used_step,
        label=row.label,
    )


def _row_to_webauthn(row: object) -> WebAuthnCredential:
    return WebAuthnCredential(
        credential_id=row.credential_id,
        subject_id=row.subject_id,
        public_key=bytes(row.public_key),
        sign_count=row.sign_count,
        rp_id=row.rp_id,
        origin=row.origin,
        created_at=row.created_at,
        aaguid=row.aaguid,
        transports=tuple(row.transports or ()),
        label=row.label,
    )


class SqlAlchemyTotpCredentialStore(TotpCredentialStorePort):
    """Persists the single active TOTP credential per subject and its replay cursor.

    The base32 shared secret is sealed with the injected :class:`EncryptionPort` before it ever
    reaches the database and is opened only on load, so the ``secret`` column holds an AEAD token
    (base64-encoded), never the plaintext secret (encrypted-at-rest, rule 50).
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: IdentityTables,
        encryptor: EncryptionPort,
    ) -> None:
        self._session_factory = session_factory
        self._table = tables.totp_credential
        self._encryptor = encryptor

    def _seal_secret(self, credential: TotpCredential) -> str:
        token = self._encryptor.encrypt(
            credential.secret.encode("ascii"),
            totp_secret_aad(
                subject_id=credential.subject_id, credential_id=credential.credential_id
            ),
        )
        return base64.b64encode(token).decode("ascii")

    def save(self, credential: TotpCredential) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                insert(self._table).values(
                    credential_id=credential.credential_id,
                    subject_id=credential.subject_id,
                    secret=self._seal_secret(credential),
                    digits=credential.digits,
                    period=credential.period,
                    algorithm=credential.algorithm,
                    created_at=credential.created_at,
                    confirmed_at=credential.confirmed_at,
                    last_used_step=credential.last_used_step,
                    label=credential.label,
                )
            )
            uow.commit()

    def get(self, subject_id: str) -> TotpCredential | None:
        with self._session_factory() as session:
            row = session.execute(
                select(self._table).where(self._table.c.subject_id == subject_id)
            ).first()
        return None if row is None else _row_to_totp(row, encryptor=self._encryptor)

    def replace(self, credential: TotpCredential) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                update(self._table)
                .where(self._table.c.credential_id == credential.credential_id)
                .values(
                    confirmed_at=credential.confirmed_at,
                    last_used_step=credential.last_used_step,
                )
            )
            uow.commit()

    def delete_for_subject(self, subject_id: str) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(delete(self._table).where(self._table.c.subject_id == subject_id))
            uow.commit()


class SqlAlchemyWebAuthnCredentialStore(WebAuthnCredentialStorePort):
    """Persists WebAuthn/passkey credentials (COSE public key + signature counter)."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: IdentityTables) -> None:
        self._session_factory = session_factory
        self._table = tables.webauthn_credential

    def save(self, credential: WebAuthnCredential) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                insert(self._table).values(
                    credential_id=credential.credential_id,
                    subject_id=credential.subject_id,
                    public_key=credential.public_key,
                    sign_count=credential.sign_count,
                    rp_id=credential.rp_id,
                    origin=credential.origin,
                    aaguid=credential.aaguid,
                    transports=list(credential.transports) or None,
                    label=credential.label,
                    created_at=credential.created_at,
                )
            )
            uow.commit()

    def get(self, *, credential_id: str) -> WebAuthnCredential | None:
        with self._session_factory() as session:
            row = session.execute(
                select(self._table).where(self._table.c.credential_id == credential_id)
            ).first()
        return None if row is None else _row_to_webauthn(row)

    def list_for_subject(self, subject_id: str) -> tuple[WebAuthnCredential, ...]:
        with self._session_factory() as session:
            rows = session.execute(
                select(self._table).where(self._table.c.subject_id == subject_id)
            ).all()
        return tuple(_row_to_webauthn(row) for row in rows)

    def set_sign_count(self, *, credential_id: str, sign_count: int) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                update(self._table)
                .where(self._table.c.credential_id == credential_id)
                .values(sign_count=sign_count)
            )
            uow.commit()

    def delete_for_subject(self, subject_id: str) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(delete(self._table).where(self._table.c.subject_id == subject_id))
            uow.commit()
