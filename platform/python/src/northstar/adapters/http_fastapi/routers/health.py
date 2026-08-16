"""Health/readiness/startup router (FR-KRN-006, docs/18 §12).

Projects the kernel :class:`~northstar.kernel.health.ports.HealthProbePort` and
:class:`~northstar.kernel.health.ports.VersionPort` to the wire:

* ``GET /health/live``    — process alive; no expensive dependency checks.
* ``GET /health/ready``   — safe to receive work (incl. database/schema compatibility).
* ``GET /health/startup`` — one-time startup/migration completion.

A non-serving (``unhealthy``) probe returns ``503`` so orchestrators stop routing traffic; every
response also carries framework version + schema-compatibility. Detailed diagnostics are never
exposed here (docs/18 §12).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from northstar.kernel.health.ports import HealthReport, VersionInfo

from ..dependencies import AppDependencies, get_dependencies
from ..schemas import HealthResponse, VersionView

router = APIRouter(prefix="/health", tags=["health"])


def _version_view(info: VersionInfo) -> VersionView:
    return VersionView(
        framework_version=info.framework_version,
        contract_api=info.contract_api,
        schema_compatible=info.schema_compatible,
    )


def _project(report: HealthReport, info: VersionInfo, response: Response) -> HealthResponse:
    if not report.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=report.state.value,
        detail=report.detail,
        version=_version_view(info),
    )


@router.get("/live", response_model=HealthResponse)
def live(
    response: Response,
    deps: Annotated[AppDependencies, Depends(get_dependencies)],
) -> HealthResponse:
    return _project(deps.health.liveness(), deps.version.version(), response)


@router.get("/ready", response_model=HealthResponse)
def ready(
    response: Response,
    deps: Annotated[AppDependencies, Depends(get_dependencies)],
) -> HealthResponse:
    return _project(deps.health.readiness(), deps.version.version(), response)


@router.get("/startup", response_model=HealthResponse)
def startup(
    response: Response,
    deps: Annotated[AppDependencies, Depends(get_dependencies)],
) -> HealthResponse:
    return _project(deps.health.startup(), deps.version.version(), response)
