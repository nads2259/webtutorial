"""Reference sandbox: run user code in a locked-down child process (:class:`CodeSandboxPort`).

SECURITY BOUNDARY. This adapter is the trust boundary for executing untrusted, learner-submitted
code. The reference implementation applies, on Linux/POSIX:

* a fresh process in its own session/process-group (so a timeout kills the whole tree);
* POSIX resource limits via ``preexec_fn`` — CPU seconds (``RLIMIT_CPU``), address space
  (``RLIMIT_AS`` = memory), created-file size (``RLIMIT_FSIZE``), and subprocess count
  (``RLIMIT_NPROC``) — so runaway CPU/memory/fork-bombs are capped by the kernel;
* a wall-clock timeout with a hard kill of the process group;
* an isolated temporary working directory and a stripped environment (no inherited secrets);
* stdout/stderr capture with a hard output-size cap;
* interpreter isolation flags (``-I -B -S``) so the user program cannot import site customisations.

Network egress is NOT namespaced by default (that needs privileges / user-namespaces). Set
``NORTHSTAR_CODELAB_UNSHARE_NET=1`` to wrap execution in ``unshare -n`` where the host permits it.
For production, swap this adapter for a container / microVM / gVisor / nsjail backend behind the same
:class:`CodeSandboxPort` — no domain or capability change required.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

from ..domain.model import CodeLimits, ExecResult

try:  # POSIX only; the reference sandbox targets Linux.
    import resource
except ImportError:  # pragma: no cover - non-POSIX fallback
    resource = None  # type: ignore[assignment]


class SubprocessSandbox:
    """Execute code in a resource-limited child process (reference :class:`CodeSandboxPort`)."""

    def __init__(self, *, python_executable: str | None = None) -> None:
        self._python = python_executable or sys.executable

    def run(self, *, language: str, code: str, stdin: str, limits: CodeLimits) -> ExecResult:
        if language != "python":
            return ExecResult(
                stdout="",
                stderr=f"unsupported language: {language}",
                exit_code=2,
                duration_ms=0,
            )
        workdir = tempfile.mkdtemp(prefix="codelab-")
        argv = self._argv(code)
        env = self._env(workdir, limits)
        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv is fully controlled; input is sandboxed
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir,
                env=env,
                start_new_session=True,
                preexec_fn=_limit_preexec(limits) if resource is not None else None,
                text=True,
            )
            try:
                stdout, stderr = proc.communicate(
                    input=stdin or "", timeout=limits.wall_seconds
                )
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
                stdout, stderr = proc.communicate()
                exit_code = 124
        except OSError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            return ExecResult(
                stdout="", stderr=f"sandbox error: {exc}", exit_code=1, duration_ms=0
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, so_trunc = _cap(stdout, limits.max_output_bytes)
        stderr, se_trunc = _cap(stderr, limits.max_output_bytes)
        if timed_out and not stderr:
            stderr = f"execution timed out after {limits.wall_seconds}s"
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
            truncated=so_trunc or se_trunc,
        )

    def _argv(self, code: str) -> list[str]:
        # -I isolated, -B no .pyc, -S no site; run the program from -c so nothing hits disk.
        base = [self._python, "-I", "-B", "-S", "-c", code]
        if os.environ.get("NORTHSTAR_CODELAB_UNSHARE_NET") == "1" and shutil.which("unshare"):
            return ["unshare", "-n", *base]
        return base

    def _env(self, workdir: str, limits: CodeLimits) -> dict[str, str]:
        return {
            "PATH": "/usr/bin:/bin",
            "HOME": workdir,
            "TMPDIR": workdir,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
            "NORTHSTAR_CODELAB": "1",
        }


def _limit_preexec(limits: CodeLimits):  # noqa: ANN202 - returns a preexec closure
    def _apply() -> None:  # pragma: no cover - runs in the child before exec
        assert resource is not None
        cpu = max(1, limits.cpu_seconds)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        mem = max(64, limits.memory_mb) * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        except (ValueError, OSError):
            pass
        fsize = 8 * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError):
            pass
        # NB: the process group / session is established by ``start_new_session=True`` before this
        # hook runs; do not call setsid() here (it would already be the session leader -> EPERM).

    return _apply


def _kill_group(proc: subprocess.Popen) -> None:
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _cap(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n...[output truncated]", True
