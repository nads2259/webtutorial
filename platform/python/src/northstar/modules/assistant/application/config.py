"""Admin-configurable model registry for the assistant (presets + active selection).

Defaults come from ``models.txt``-style settings (an OpenAI-compatible gateway that puts the model in
the URL path). The active model is mutable at runtime so an admin can switch models without a redeploy.
Credentials are read from the environment (never hard-coded), so a secret manager can supply them.
"""

from __future__ import annotations

import os

from ..domain.model import AssistantModel
from .ports import AssistantSettingsPort

_DEFAULT_BASE = "https://owl.7sg.ai"

# Presets mirror the endpoints declared in models.txt.
_PRESETS: tuple[AssistantModel, ...] = (
    AssistantModel(id="qwen3-coder-next", label="Qwen3 Coder (code)", model="qwen3-coder-next", kind="coder"),
    AssistantModel(
        id="deepseek-v4-flash", label="DeepSeek v4 Flash (reasoning)", model="deepseek-v4-flash", kind="reasoning"
    ),
    AssistantModel(
        id="nemotron-3-ultra", label="Nemotron 3 Ultra", model="nemotron-3-ultra", kind="general"
    ),
    AssistantModel(id="minimax-m27", label="MiniMax M27 (planning)", model="minimax-m27", kind="planning"),
)


class AssistantModelStore:
    """Holds the preset models + the currently active one (admin-settable)."""

    def __init__(
        self,
        *,
        base_url: str,
        presets: tuple[AssistantModel, ...] = _PRESETS,
        active_id: str | None = None,
        settings: AssistantSettingsPort | None = None,
        tenant: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._presets = presets
        self._by_id = {m.id: m for m in presets}
        self._settings = settings
        self._tenant = tenant
        # A durably-persisted choice (admin-set) wins over env/default, so it survives restarts.
        persisted = (
            settings.get_active_model(organization_id=tenant)
            if settings is not None and tenant
            else None
        )
        chosen = persisted or active_id
        self._active_id = chosen if chosen in self._by_id else presets[0].id

    @property
    def base_url(self) -> str:
        return self._base_url

    def models(self) -> tuple[AssistantModel, ...]:
        return self._presets

    def active(self) -> AssistantModel:
        return self._by_id[self._active_id]

    def by_id(self, model_id: str | None) -> AssistantModel | None:
        return self._by_id.get(model_id) if model_id else None

    def set_active(self, model_id: str) -> bool:
        if model_id in self._by_id:
            self._active_id = model_id
            if self._settings is not None and self._tenant:
                self._settings.set_active_model(organization_id=self._tenant, model_id=model_id)
            return True
        return False


def default_store(
    *, settings: AssistantSettingsPort | None = None, tenant: str = ""
) -> AssistantModelStore:
    return AssistantModelStore(
        base_url=os.environ.get("NORTHSTAR_ASSISTANT_BASE", _DEFAULT_BASE),
        active_id=os.environ.get("NORTHSTAR_ASSISTANT_MODEL"),
        settings=settings,
        tenant=tenant,
    )
