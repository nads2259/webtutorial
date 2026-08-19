"""Pure value objects for codelab: execution limits, results, and tracked run evidence.

Infrastructure-free (rule 10). A :class:`CodeRun` is an IMMUTABLE record of one execution with a
``record_sha256`` computed over its canonical projection, so the tracked action log is tamper-evident
(mirrors the simulation evidence hashing). The sandbox itself lives behind a port; the domain only
models the request/result/record shapes and their integrity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from .errors import CodeInvalid

# The languages the reference sandbox can execute. Kept explicit (deny-by-default).
SUPPORTED_LANGUAGES = ("python",)

MAX_CODE_BYTES = 200_000


@dataclass(frozen=True, slots=True)
class CodeLimits:
    """Resource limits enforced by the sandbox adapter (defense-in-depth)."""

    cpu_seconds: int = 5
    wall_seconds: int = 10
    memory_mb: int = 512
    max_output_bytes: int = 64_000


@dataclass(frozen=True, slots=True)
class ExecResult:
    """The observable result of one sandboxed execution."""

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False

    @property
    def outcome(self) -> str:
        if self.timed_out:
            return "timeout"
        return "success" if self.exit_code == 0 else "error"


@dataclass(frozen=True, slots=True)
class CodeRun:
    """An immutable, tracked record of a single code execution (the audited user action)."""

    run_id: str
    organization_id: str
    actor_id: str
    language: str
    code: str
    lesson_id: str | None
    block_id: str | None
    result: ExecResult
    created_at: datetime
    record_sha256: str = ""

    def with_hash(self) -> CodeRun:
        """Return a copy carrying the canonical integrity hash over this run's evidence."""
        material = {
            "run_id": self.run_id,
            "organization_id": self.organization_id,
            "actor_id": self.actor_id,
            "language": self.language,
            "code_sha256": _sha256(self.code),
            "lesson_id": self.lesson_id,
            "block_id": self.block_id,
            "exit_code": self.result.exit_code,
            "stdout_sha256": _sha256(self.result.stdout),
            "stderr_sha256": _sha256(self.result.stderr),
            "duration_ms": self.result.duration_ms,
            "timed_out": self.result.timed_out,
            "created_at": self.created_at.isoformat(),
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return _replace_hash(self, f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}")


def validate_submission(*, language: str, code: str) -> None:
    """Reject an unsupported language or an empty/oversized submission (deny-by-default)."""
    if language not in SUPPORTED_LANGUAGES:
        raise CodeInvalid(f"unsupported language {language!r}")
    if not code.strip():
        raise CodeInvalid("code must not be empty")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise CodeInvalid("code exceeds the maximum allowed size")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replace_hash(run: CodeRun, digest: str) -> CodeRun:
    return CodeRun(
        run_id=run.run_id,
        organization_id=run.organization_id,
        actor_id=run.actor_id,
        language=run.language,
        code=run.code,
        lesson_id=run.lesson_id,
        block_id=run.block_id,
        result=run.result,
        created_at=run.created_at,
        record_sha256=digest,
    )


RES_CODELAB = "codelab.run"
