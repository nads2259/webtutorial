"""northstar CLI — Phase 0 skeleton.

Implements the companion command surface from spec/reference/bootstrap-contract.md and
docs/19. Machine commands emit output conforming to contracts/schemas/cli-output.schema.json.
Unimplemented commands return a clear `blocked` status (exit 2) — never a false `pass`
(NOT RUN != PASS). Stdlib-only so it runs from a clean checkout without installation.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jsonschema.protocols import Validator

from northstar import __version__
from northstar.cli import generators
from northstar.cli.generators import GeneratorError

SCHEMA_VERSION = "1.0.0"

# exit codes (mirror contracts/schemas/cli-output.schema.json / audit-and-verify spec)
EXIT_PASS, EXIT_FAIL, EXIT_BLOCKED, EXIT_USAGE, EXIT_ENV = 0, 1, 2, 3, 4

COMPANION_COMMANDS = [
    "bootstrap",
    "doctor",
    "up",
    "down",
    "migrate",
    "seed",
    "test",
    "verify",
    "audit",
    "evidence",
    "logs",
    "config",
    "reset",
    "version",
]


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


@dataclass
class Result:
    id: str
    kind: str
    result: str  # pass|fail|warn|not_run|blocked
    detail: str | None = None
    requirement_ids: list[str] = field(default_factory=list)
    evidence_uri: str | None = None


@dataclass
class Finding:
    severity: str  # critical|high|medium|low|info
    code: str
    message: str


def _status_and_code(results: list[Result], findings: list[Finding]) -> tuple[str, int]:
    if any(f.severity in ("critical", "high") for f in findings) or any(
        r.result == "fail" for r in results
    ):
        return "fail", EXIT_FAIL
    if any(r.result in ("blocked", "not_run") for r in results):
        return "blocked", EXIT_BLOCKED
    return "pass", EXIT_PASS


def _emit(
    command: str,
    invocation: str,
    started: str,
    results: list[Result],
    findings: list[Finding],
    as_json: bool,
) -> int:
    status, code = _status_and_code(results, findings)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "invocation": invocation,
        "status": status,
        "exit_code": code,
        "started_at": started,
        "finished_at": _now(),
        "summary": {
            "checks_total": len(results),
            "checks_passed": sum(r.result == "pass" for r in results),
            "checks_failed": sum(r.result == "fail" for r in results),
            "checks_not_run": sum(r.result in ("not_run", "blocked") for r in results),
        },
        "results": [
            {
                k: v
                for k, v in {
                    "id": r.id,
                    "kind": r.kind,
                    "result": r.result,
                    "requirement_ids": r.requirement_ids or [],
                    "evidence_uri": r.evidence_uri,
                    "detail": r.detail,
                }.items()
                if v is not None or k in ("requirement_ids",)
            }
            for r in results
        ],
        "findings": [
            {"severity": f.severity, "code": f.code, "message": f.message} for f in findings
        ],
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"{command}: {status} (exit {code})")
        for r in results:
            print(f"  [{r.result}] {r.id} — {r.detail or ''}")
        for f in findings:
            print(f"  ({f.severity}) {f.code}: {f.message}")
    return code


def _repo_root() -> str:
    # platform/python/src/northstar/cli/__init__.py -> repo root is five parents up
    here = os.path.abspath(__file__)
    return os.path.abspath(os.path.join(here, "..", "..", "..", "..", "..", ".."))


# ---- commands -------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    started = _now()
    results: list[Result] = []
    findings: list[Finding] = []

    py_ok = sys.version_info[:2] >= (3, 13)
    results.append(
        Result(
            "py-version",
            "doctor_check",
            "pass" if py_ok else "fail",
            detail=f"python {sys.version.split()[0]} (need >= 3.13)",
            requirement_ids=["NFR-DX-002"],
        )
    )
    if not py_ok:
        findings.append(Finding("high", "PY_VERSION", "Python 3.13+ required (NFR-DX-002)."))

    for tool, sev in [("git", "medium"), ("docker", "low"), ("python3", "high")]:
        present = shutil.which(tool) is not None
        results.append(
            Result(
                f"tool-{tool}",
                "doctor_check",
                "pass" if present else "warn",
                detail=f"{tool} {'found' if present else 'not found'}",
                requirement_ids=["NFR-DX-002"],
            )
        )
        if not present and sev == "high":
            findings.append(Finding("high", "MISSING_TOOL", f"{tool} not found on PATH."))

    try:
        free_gb = shutil.disk_usage(_repo_root()).free / (1024**3)
        results.append(
            Result(
                "disk-free",
                "doctor_check",
                "pass" if free_gb >= 2 else "warn",
                detail=f"{free_gb:.1f} GiB free",
            )
        )
    except OSError:
        results.append(Result("disk-free", "doctor_check", "warn", detail="could not determine"))

    return _emit("doctor", "northstar doctor", started, results, findings, args.json)


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError as e:
        return EXIT_ENV, str(e)


def cmd_test(args: argparse.Namespace) -> int:
    started = _now()
    root = _repo_root()
    suite = args.suite
    results: list[Result] = []
    findings: list[Finding] = []
    if suite in ("unit", "integration", "contract", "all"):
        marker = [] if suite == "all" else ["-m", suite]
        path = ["--", args.path] if getattr(args, "path", None) else []
        rc, out = _run([sys.executable, "-m", "pytest", "-q", *marker, *path], root)
        # rc 5 = no tests collected (acceptable in Phase 0)
        res = "pass" if rc in (0, 5) else "fail"
        results.append(
            Result(
                f"pytest-{suite}",
                "doctor_check",
                res,
                detail=("no tests collected" if rc == 5 else f"pytest rc={rc}"),
            )
        )
        if res == "fail":
            findings.append(Finding("high", "TEST_FAILURE", out[-800:]))
    elif suite == "conformance":
        rc, out = _run([sys.executable, "scripts/check_architecture.py"], root)
        results.append(
            Result(
                "arch-conformance",
                "audit_check",
                "pass" if rc == 0 else "fail",
                detail=out.strip()[-400:],
            )
        )
        if rc != 0:
            findings.append(Finding("high", "ARCH_VIOLATION", out[-800:]))
    else:
        findings.append(Finding("low", "USAGE", f"unknown suite '{suite}'"))
        return (
            _emit("test", f"northstar test {suite}", started, results, findings, args.json)
            or EXIT_USAGE
        )
    return _emit("test", f"northstar test {suite}", started, results, findings, args.json)


def cmd_verify(args: argparse.Namespace) -> int:
    started = _now()
    root = _repo_root()
    results: list[Result] = []
    findings: list[Finding] = []
    for name, script in [
        ("spec-validation", "scripts/validate_spec.py"),
        ("architecture-secret-gate", "scripts/check_architecture.py"),
    ]:
        rc, out = _run([sys.executable, script], root)
        results.append(
            Result(
                name,
                "audit_check",
                "pass" if rc == 0 else "fail",
                detail=out.strip().splitlines()[-1] if out.strip() else "",
            )
        )
        if rc != 0:
            findings.append(Finding("high", "VERIFY_FAILURE", f"{name}: {out[-600:]}"))
    return _emit("verify", "northstar verify", started, results, findings, args.json)


def cmd_evidence(args: argparse.Namespace) -> int:
    started = _now()
    root = _repo_root()
    rc, out = _run([sys.executable, "scripts/snapshot_evidence_log.py"], root)
    results = [
        Result(
            "snapshot-build-log",
            "audit_check",
            "pass" if rc == 0 else "fail",
            detail=out.strip()[-300:],
        )
    ]
    findings = [] if rc == 0 else [Finding("medium", "EVIDENCE", out[-400:])]
    return _emit("evidence", "northstar evidence collect", started, results, findings, args.json)


# ---- release verify / audit (IMPL-022, ARCH-025 / ARCH-011) ---------------------------
#
# Machine rule (spec/audit/northstar-audit-and-verify-spec.md §3): a gate PASSES iff every
# blocking_evaluation_ids entry (from spec/evaluations/release-gates.yaml) has an
# evaluation-result with status=passed in the evidence pack. A missing result or status
# not_run => the gate is `blocked` (exit 2, NOT RUN != PASS); a failed/invalid result =>
# `fail` (exit 1). `fail` dominates `blocked` in the overall exit code.

_EVAL_ID_RE = re.compile(r"EVAL-[A-Z0-9]+(?:-[A-Z0-9]+)*")


def _schema(root: str, name: str) -> dict[str, Any]:
    with open(os.path.join(root, "spec", "contracts", "schemas", name), encoding="utf-8") as f:
        return json.load(f)


def _make_eval_validator(root: str) -> Validator:
    import jsonschema

    return jsonschema.Draft202012Validator(_schema(root, "evaluation-result.schema.json"))


def _eval_status(evidence_dir: str, eval_id: str, validator: Validator) -> tuple[str, str | None]:
    """Resolve an evaluation-result in the evidence pack.

    Returns ``(status, evidence_uri)`` where status is one of
    ``passed|failed|warning|not_run|missing|invalid``.
    """
    path = os.path.join(evidence_dir, "evaluation-results", f"{eval_id}.json")
    if not os.path.isfile(path):
        return "missing", None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return "invalid", path
    if list(validator.iter_errors(data)):
        return "invalid", path
    return str(data.get("status", "not_run")), path


def _gate_result(statuses: list[str]) -> str:
    """Fold blocking-evaluation statuses into a gate result (fail dominates blocked)."""
    if any(s in ("failed", "invalid", "warning") for s in statuses):
        return "fail"
    if any(s in ("missing", "not_run") for s in statuses):
        return "blocked"
    return "pass"


_EVAL_STATUS_TO_RESULT = {
    "passed": "pass",
    "failed": "fail",
    "warning": "warn",
    "not_run": "not_run",
    "missing": "not_run",
    "invalid": "fail",
}


def cmd_release_verify(args: argparse.Namespace) -> int:
    started = _now()
    root = _repo_root()
    as_json: bool = args.json
    gates_arg: list[str] = args.gate
    invocation = "northstar release verify " + " ".join(f"--gate {g}" for g in gates_arg)
    results: list[Result] = []
    findings: list[Finding] = []

    import yaml

    gates_path = os.path.join(root, "spec", "evaluations", "release-gates.yaml")
    try:
        with open(gates_path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except OSError as exc:
        findings.append(Finding("high", "ENV", f"cannot read release-gates.yaml: {exc}"))
        _emit("release verify", invocation, started, results, findings, as_json)
        return EXIT_ENV

    gate_map = {g["gate_id"]: g for g in doc.get("gates", [])}
    unknown = [g for g in gates_arg if g not in gate_map]
    if unknown:
        findings.append(Finding("low", "USAGE", f"unknown gate(s): {', '.join(unknown)}"))
        _emit("release verify", invocation, started, results, findings, as_json)
        return EXIT_USAGE

    evidence_dir = os.path.abspath(args.evidence)
    evidence_ok = os.path.isdir(evidence_dir)
    if not evidence_ok:
        findings.append(
            Finding("medium", "EVIDENCE_MISSING", f"evidence dir not found: {args.evidence}")
        )

    from northstar.modules.governance.domain.model import no_expired_exception

    now = datetime.datetime.now(datetime.UTC)
    exceptions, exc_warnings = ([], []) if not evidence_ok else _load_pack_exceptions(evidence_dir)
    for warn in exc_warnings:
        findings.append(Finding("medium", "EXCEPTION_MALFORMED", warn))

    validator = _make_eval_validator(root)
    for gate_id in gates_arg:
        gate = gate_map[gate_id]
        blocking: list[str] = gate.get("blocking_evaluation_ids", []) or []
        statuses: list[str] = []
        eval_results: list[Result] = []
        for eval_id in blocking:
            status, uri = (
                ("missing", None)
                if not evidence_ok
                else _eval_status(evidence_dir, eval_id, validator)
            )
            statuses.append(status)
            eval_results.append(
                Result(
                    eval_id,
                    "evaluation",
                    _EVAL_STATUS_TO_RESULT.get(status, "not_run"),
                    detail=f"{gate_id}: evaluation status={status}",
                    evidence_uri=uri,
                )
            )
        gate_res = _gate_result(statuses)

        # Wire the authoritative governance no_expired_exception check into the gate machine rule
        # (spec/evaluations/release-gates.yaml: "... and no_expired_exception"). A gate scoped to a
        # live (non-expired, approved) exception may rely on it; an expired/revoked exception must
        # NOT rescue a gate. A gate with no exception passes purely on its evaluations.
        scoped = [e for e in exceptions if e.control == gate_id]
        has_live_exception = no_expired_exception(gate_id, now, exceptions)
        if scoped and not has_live_exception:
            # every exception scoped to this gate is expired/revoked -> it cannot be relied upon
            gate_res = "fail" if gate_res == "pass" else gate_res
            findings.append(
                Finding(
                    "high",
                    "GATE_EXPIRED_EXCEPTION",
                    f"{gate_id}: only expired/revoked exception(s) scoped to this gate; "
                    "an expired exception never rescues a gate (no_expired_exception=False).",
                )
            )
        elif gate_res != "pass" and args.allow_waived and has_live_exception:
            gate_res = "pass"
            # Record the accepted-risk transparently: not_run/blocked evals become warn (not fail)
            # so the waived gate does not keep the overall run blocked, while the eval status stays
            # visible in the detail.
            for er in eval_results:
                if er.result in ("not_run", "blocked"):
                    er.result = "warn"
            findings.append(
                Finding(
                    "info",
                    "GATE_WAIVED",
                    f"{gate_id}: blocking evaluation(s) waived by a live, approved, non-expired "
                    "control exception (--allow-waived).",
                )
            )

        results.extend(eval_results)
        results.append(
            Result(
                gate_id,
                "gate",
                gate_res,
                detail=(
                    f"{len(blocking)} blocking evaluation(s); "
                    f"{sum(s == 'passed' for s in statuses)} passed; "
                    f"live_exception={has_live_exception}"
                ),
            )
        )
        if gate_res == "fail":
            findings.append(
                Finding("high", "GATE_FAILED", f"{gate_id}: blocking evaluation failed/invalid.")
            )
        elif gate_res == "blocked":
            findings.append(
                Finding(
                    "medium",
                    "GATE_BLOCKED",
                    f"{gate_id}: blocking evaluation missing or not_run (NOT RUN != PASS).",
                )
            )
    return _emit("release verify", invocation, started, results, findings, as_json)


def _load_pack_exceptions(evidence_dir: str) -> tuple[list[Any], list[str]]:
    """Load control exceptions declared in the pack's ``approvals.yaml`` (governance engine input).

    Returns ``(exceptions, warnings)``. Exceptions are :class:`ControlException` objects so the
    authoritative governance ``no_expired_exception`` check decides whether a gate may rely on a
    waiver — an expired/revoked exception never rescues a gate.
    """
    import yaml

    from northstar.kernel.context import Actor, ActorType
    from northstar.modules.governance.domain.model import ControlException, ExceptionStatus

    path = os.path.join(evidence_dir, "approvals.yaml")
    exceptions: list[Any] = []
    warnings: list[str] = []
    if not os.path.isfile(path):
        return exceptions, warnings
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        return exceptions, [f"cannot read approvals.yaml: {exc}"]
    for raw in doc.get("exceptions", []) or []:
        try:
            exceptions.append(
                ControlException(
                    exception_id=str(raw["exception_id"]),
                    organization_id=str(raw.get("organization_id", "release")),
                    control=str(raw["control"]),
                    subject=str(raw.get("subject", raw["control"])),
                    approver=Actor(ActorType.OPERATOR, str(raw["approver_id"])),
                    granted_by=Actor(
                        ActorType.OPERATOR, str(raw.get("granted_by", raw["approver_id"]))
                    ),
                    rationale=str(raw.get("rationale", "recorded release exception")),
                    expiry=datetime.datetime.fromisoformat(str(raw["expiry"])),
                    granted_at=datetime.datetime.fromisoformat(str(raw["granted_at"])),
                    status=ExceptionStatus(str(raw.get("status", "active"))),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            warnings.append(f"ignoring malformed exception entry: {exc}")
    return exceptions, warnings


def cmd_release_evidence(args: argparse.Namespace) -> int:
    """Generate the release evidence pack from committed test/harness evidence (ARCH-025)."""
    from northstar.cli import release_evidence as gen

    started = _now()
    root = _repo_root()
    as_json: bool = args.json
    release: str = args.release
    invocation = f"northstar release evidence --release {release}"
    results: list[Result] = []
    findings: list[Finding] = []

    if release != gen.RELEASE:
        findings.append(
            Finding("low", "USAGE", f"only release {gen.RELEASE} is supported (got {release}).")
        )
        _emit("release evidence", invocation, started, results, findings, as_json)
        return EXIT_USAGE

    junit_path = args.junit
    if not junit_path:
        junit_path = os.path.join(root, "evidence", release, "checks", "pytest-junit.xml")
        if not os.path.isfile(junit_path):
            findings.append(
                Finding(
                    "high",
                    "JUNIT_MISSING",
                    "no --junit supplied and no prior pytest-junit.xml in the pack; run "
                    "`.venv/bin/python -m pytest --junitxml=<path>` and pass --junit <path> "
                    "so evaluation status is derived from a REAL test run (NOT RUN != PASS).",
                )
            )
            _emit("release evidence", invocation, started, results, findings, as_json)
            return EXIT_BLOCKED
    junit_path = os.path.abspath(junit_path)

    report = gen.generate(root, junit_path)
    results.append(
        Result(
            "evidence-pack",
            "audit_check",
            "pass",
            detail=(
                f"release {report.release}: {report.evaluations_total} evaluation-results "
                f"({report.passed} passed, {report.failed} failed, "
                f"{report.not_run_human} not-run-human, {report.not_run_gap} not-run-gap); "
                f"gates {report.machine_green} machine-green / "
                f"{report.blocked_pending_human} pending-human / {report.blocked_gap} gap"
            ),
            requirement_ids=["ARCH-025", "ARCH-011", "NFR-OPS-001"],
            evidence_uri=os.path.relpath(report.pack_dir, root),
        )
    )
    for gate in report.gates:
        if gate["category"] == "machine-green":
            continue
        blockers = ", ".join(f"{b['id']}({b['reason']})" for b in gate["blockers"])
        findings.append(
            Finding(
                "info" if gate["category"] == "blocked-pending-human" else "medium",
                "GATE_" + gate["category"].upper().replace("-", "_"),
                f"{gate['gate_id']}: {gate['category']} — blockers: {blockers}",
            )
        )
    return _emit("release evidence", invocation, started, results, findings, as_json)


def _read_must_requirements(root: str, scope: str) -> list[dict[str, str]]:
    req_path = os.path.join(root, "spec", "matrices", "requirements.csv")
    rows: list[dict[str, str]] = []
    with open(req_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            priority = (row.get("priority") or "").strip()
            risk = (row.get("risk") or "").strip()
            if priority != "Must" and risk != "Critical":
                continue
            if scope != "all" and scope.lower() not in (row.get("owning_module") or "").lower():
                continue
            rows.append(row)
    return rows


def cmd_audit(args: argparse.Namespace) -> int:
    started = _now()
    root = _repo_root()
    as_json: bool = args.json
    scope: str = args.scope
    invocation = f"northstar audit --scope {scope}"
    results: list[Result] = []
    findings: list[Finding] = []

    evidence_dir = os.path.abspath(args.evidence) if args.evidence else None
    validator = _make_eval_validator(root) if evidence_dir else None

    not_evaluated = 0
    for row in _read_must_requirements(root, scope):
        rid = row["requirement_id"].strip()
        eval_ids = _EVAL_ID_RE.findall(row.get("test_or_evaluation") or "")
        if evidence_dir is None or not eval_ids:
            result = "not_run"
            not_evaluated += 1
            reason = "no evidence dir supplied" if evidence_dir is None else "no mapped evaluation"
            detail = f"NOT EVALUATED: {reason}"
            uri = None
        else:
            statuses = [_eval_status(evidence_dir, e, validator)[0] for e in eval_ids]
            if all(s == "passed" for s in statuses):
                result = "pass"
            elif any(s in ("failed", "invalid") for s in statuses):
                result = "fail"
            else:
                result = "not_run"
                not_evaluated += 1
            detail = f"evaluations {','.join(eval_ids)}: {','.join(statuses)}"
            uri = os.path.join(evidence_dir, "evaluation-results")
        results.append(
            Result(rid, "contract", result, detail=detail, requirement_ids=[rid], evidence_uri=uri)
        )

    rc, out = _run([sys.executable, "scripts/check_architecture.py"], root)
    results.append(
        Result(
            "architecture-secret-gate",
            "audit_check",
            "pass" if rc == 0 else "fail",
            detail=(out.strip().splitlines()[-1] if out.strip() else f"rc={rc}"),
            requirement_ids=["ARCH-011"],
        )
    )
    if rc != 0:
        findings.append(Finding("high", "ARCH_VIOLATION", out[-600:]))
    if not_evaluated:
        findings.append(
            Finding(
                "info",
                "NOT_EVALUATED",
                f"{not_evaluated} Must/Critical requirement(s) lack passing evaluation "
                f"evidence and are reported NOT EVALUATED (NOT RUN != PASS).",
            )
        )
    return _emit("audit", invocation, started, results, findings, as_json)


def _not_implemented(command: str, as_json: bool, req: list[str] | None = None) -> int:
    started = _now()
    results = [
        Result(
            command,
            "doctor_check",
            "blocked",
            detail=f"'{command}' is declared but not implemented in the Phase 0 skeleton "
            f"(runtime not built yet). This is a blueprint stop, not a pass.",
            requirement_ids=req or [],
        )
    ]
    findings = [
        Finding(
            "info",
            "NOT_IMPLEMENTED",
            f"northstar {command} arrives in a later phase; see MASTER_BUILD_PLAN.md.",
        )
    ]
    return _emit(command, f"northstar {command}", started, results, findings, as_json)


_BOOTSTRAP_STEP_KIND = {
    "doctor": "doctor_check",
    "migrate": "migration",
    "seed": "audit_check",
    "smoke": "doctor_check",
}
_BOOTSTRAP_STEP_REQS = {
    "doctor": ["NFR-DX-002"],
    "migrate": ["FR-DX-001"],
    "seed": ["FR-DX-001"],
    "smoke": ["FR-DX-001", "NFR-OPS-001"],
}


def _cmd_bootstrap_ci(args: argparse.Namespace) -> int:
    """Run the real, deterministic one-touch bootstrap state machine for ``--profile ci``."""
    from northstar.cli.bootstrap import run_bootstrap

    started = _now()
    report = run_bootstrap("ci")
    results: list[Result] = []
    findings: list[Finding] = []
    for step in report.results:
        results.append(
            Result(
                f"bootstrap-{step.name}",
                _BOOTSTRAP_STEP_KIND.get(step.name, "doctor_check"),
                step.status,
                detail=step.detail,
                requirement_ids=_BOOTSTRAP_STEP_REQS.get(step.name, ["FR-DX-001"]),
            )
        )
        if step.status == "fail":
            findings.append(
                Finding(
                    "high",
                    f"BOOTSTRAP_{step.name.upper()}_FAILED",
                    f"{step.detail}" + (f" — recovery: {step.recovery}" if step.recovery else ""),
                )
            )
        elif step.status == "blocked":
            findings.append(
                Finding(
                    "medium",
                    f"BOOTSTRAP_{step.name.upper()}_BLOCKED",
                    f"{step.detail}" + (f" — recovery: {step.recovery}" if step.recovery else ""),
                )
            )
    return _emit(
        "bootstrap", "northstar bootstrap --profile ci", started, results, findings, args.json
    )


def cmd_bootstrap(args: argparse.Namespace) -> int:
    if args.profile == "ci":
        return _cmd_bootstrap_ci(args)
    # Interactive/container profiles need Docker-based dependency services that are not part of
    # this in-process slice: check prerequisites and report the staged plan as blocked (honest,
    # never a false pass) — the deterministic `--profile ci` path is fully implemented above.
    started = _now()
    results: list[Result] = []
    findings: list[Finding] = []
    py_ok = sys.version_info[:2] >= (3, 13)
    results.append(
        Result(
            "preflight-doctor",
            "doctor_check",
            "pass" if py_ok else "fail",
            detail=f"profile={args.profile}; python ok={py_ok}",
            requirement_ids=["ARCH-017"],
        )
    )
    results.append(
        Result(
            "services",
            "doctor_check",
            "blocked",
            detail="dependency services start is owned by the Docker profile path (docs/18); "
            "use `--profile ci` for the in-process deterministic bootstrap",
            requirement_ids=["ARCH-017"],
        )
    )
    findings.append(
        Finding(
            "info",
            "BLUEPRINT",
            "interactive profiles check prerequisites here; container service start/migrate/seed "
            "run through the Docker path (bootstrap-contract.md). `--profile ci` runs end-to-end.",
        )
    )
    return _emit(
        "bootstrap",
        f"northstar bootstrap --profile {args.profile}",
        started,
        results,
        findings,
        args.json,
    )


_MAKE_KINDS = {
    "make:module": (generators.generate_module, "NFR-DX-001"),
    "make:plugin": (generators.generate_plugin, "NFR-DX-001"),
    "make:theme": (generators.generate_theme, "NFR-DX-001"),
}


def cmd_make(args: argparse.Namespace) -> int:
    started = _now()
    command = args.command
    kind = command.split(":", 1)[1]
    generate, req = _MAKE_KINDS[command]
    base_dir = os.path.abspath(args.dest) if getattr(args, "dest", None) else _repo_root()
    results: list[Result] = []
    findings: list[Finding] = []
    try:
        outcome = generate(args.name, base_dir)
    except GeneratorError as exc:
        results.append(
            Result(
                f"make-{kind}",
                "audit_check",
                "fail",
                detail=str(exc),
                requirement_ids=[req],
            )
        )
        findings.append(Finding("high", "GENERATE_REFUSED", str(exc)))
        return _emit(
            command, f"northstar {command} {args.name}", started, results, findings, args.json
        )
    rel_dir = os.path.relpath(outcome.target_dir, base_dir)
    results.append(
        Result(
            f"make-{kind}",
            "audit_check",
            "pass",
            detail=f"scaffolded {rel_dir} ({len(outcome.files)} files, marked generated)",
            requirement_ids=[req],
            evidence_uri=outcome.target_dir,
        )
    )
    return _emit(command, f"northstar {command} {args.name}", started, results, findings, args.json)


def cmd_recipe(args: argparse.Namespace) -> int:
    started = _now()
    command = args.command
    action = command.split(":", 1)[1]
    results: list[Result] = []
    findings: list[Finding] = []
    try:
        plan = generators.plan_recipe(action, args.recipe)
    except GeneratorError as exc:
        results.append(
            Result(
                f"recipe-{action}",
                "audit_check",
                "fail",
                detail=str(exc),
                requirement_ids=["NFR-DX-001"],
            )
        )
        findings.append(Finding("high", "RECIPE_INVALID", str(exc)))
        return _emit(
            command, f"northstar {command} {args.recipe}", started, results, findings, args.json
        )
    detail = f"plan for '{plan.recipe}': " + "; ".join(plan.steps)
    results.append(
        Result(
            f"recipe-{action}",
            "audit_check",
            "blocked",
            detail=detail,
            requirement_ids=["NFR-DX-001"],
        )
    )
    findings.append(
        Finding(
            "info",
            "RECIPE_PLAN_ONLY",
            "recipe application is owned by the distribution/installer runtime (docs/38); "
            "this scaffold emits a plan, not a false success.",
        )
    )
    return _emit(
        command, f"northstar {command} {args.recipe}", started, results, findings, args.json
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="northstar", description="Northstar framework CLI (Phase 0 skeleton)."
    )
    p.add_argument("--json", action="store_true", help="emit cli-output.schema.json JSON")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="print version")
    d = sub.add_parser("doctor", help="diagnose supported runtimes (NFR-DX-002)")
    d.add_argument("--json", action="store_true")
    b = sub.add_parser("bootstrap", help="one-touch bootstrap (ARCH-017)")
    b.add_argument(
        "--profile", default="minimal", choices=["minimal", "full", "ai", "simulation", "ci"]
    )
    b.add_argument("--json", action="store_true")
    t = sub.add_parser("test", help="run test suites")
    t.add_argument("suite", choices=["unit", "integration", "contract", "conformance", "all"])
    t.add_argument("--path")
    t.add_argument("--json", action="store_true")
    v = sub.add_parser("verify", help="run spec + architecture verification")
    v.add_argument("--json", action="store_true")
    e = sub.add_parser("evidence", help="collect release evidence (build log snapshot)")
    e.add_argument("action", nargs="?", default="collect", choices=["collect"])
    e.add_argument("--json", action="store_true")
    for name in ["make:module", "make:plugin", "make:theme"]:
        m = sub.add_parser(name, help=f"scaffold a {name.split(':')[1]} (NFR-DX-001)")
        m.add_argument("name", help="lowercase identifier for the generated artifact")
        m.add_argument("--dest", help="parent directory for generation (default: repo root)")
        m.add_argument("--json", action="store_true")
    for name in ["recipe:add", "recipe:remove"]:
        r = sub.add_parser(name, help=f"{name} (structured plan; NFR-DX-001)")
        r.add_argument("recipe", help="recipe identifier")
        r.add_argument("--json", action="store_true")
    au = sub.add_parser("audit", help="repository conformance audit (Must/Critical -> evidence)")
    au.add_argument("--scope", default="all")
    au.add_argument("--evidence", help="evidence pack directory (optional)")
    au.add_argument("--requirements", help="requirements CSV override (reserved)")
    au.add_argument("--json", action="store_true")
    rel = sub.add_parser("release", help="release gate evaluation (ARCH-025)")
    rel_sub = rel.add_subparsers(dest="subcommand", required=True)
    rv = rel_sub.add_parser("verify", help="verify release gates against an evidence pack")
    rv.add_argument("--gate", action="append", required=True, help="gate id (repeatable)")
    rv.add_argument("--evidence", required=True, help="evidence pack directory")
    rv.add_argument("--allow-waived", action="store_true")
    rv.add_argument("--json", action="store_true")
    re_ = rel_sub.add_parser("evidence", help="generate the release evidence pack (ARCH-025)")
    re_.add_argument("--release", default="0.3.0", help="release id (only 0.3.0 supported)")
    re_.add_argument(
        "--junit", help="pytest --junitxml path used to derive automated evaluation status"
    )
    re_.add_argument("--json", action="store_true")
    for name in ["up", "down", "migrate", "seed", "logs", "config", "reset"]:
        s = sub.add_parser(name, help=f"{name} (declared; implemented in a later phase)")
        s.add_argument("rest", nargs="*")
        s.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = getattr(args, "json", False)
    cmd = args.command
    if cmd == "version":
        print(__version__ if not as_json else json.dumps({"version": __version__}))
        return EXIT_PASS
    if cmd == "doctor":
        return cmd_doctor(args)
    if cmd == "bootstrap":
        return cmd_bootstrap(args)
    if cmd == "test":
        return cmd_test(args)
    if cmd == "verify":
        return cmd_verify(args)
    if cmd == "evidence":
        return cmd_evidence(args)
    if cmd == "audit":
        return cmd_audit(args)
    if cmd == "release":
        sub = getattr(args, "subcommand", None)
        if sub == "verify":
            return cmd_release_verify(args)
        if sub == "evidence":
            return cmd_release_evidence(args)
        return _not_implemented("release", as_json, req=["ARCH-025"])
    if cmd in _MAKE_KINDS:
        return cmd_make(args)
    if cmd in ("recipe:add", "recipe:remove"):
        return cmd_recipe(args)
    return _not_implemented(cmd, as_json, req=["ARCH-017"])


if __name__ == "__main__":
    raise SystemExit(main())
