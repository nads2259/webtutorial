"""Bridge: expose the transactional email service as identity's ``TransactionalEmailPort``.

Structurally matches ``northstar.modules.identity.application.local_ports.TransactionalEmailPort`` so
the composition root can inject it into the local-auth capabilities without either module importing
the other's internals (LAW-13). Rendering/recording/delivery all happen inside the messaging service.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..application.transactional import TransactionalEmailService


class MessagingEmailSender:
    """Adapts :class:`TransactionalEmailService` to identity's transactional-email port."""

    def __init__(self, *, service: TransactionalEmailService) -> None:
        self._service = service

    def send(
        self,
        *,
        organization_id: str,
        template_id: str,
        to_email: str,
        variables: Mapping[str, str],
    ) -> None:
        self._service.send(
            organization_id=organization_id,
            template_id=template_id,
            to_email=to_email,
            variables=variables,
        )
