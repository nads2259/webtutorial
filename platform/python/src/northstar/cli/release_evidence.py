"""Release evidence-pack generator (ARCH-025, ARCH-011, NFR-OPS-001).

Closes the ``verify_012`` core finding — most release gates were not machine-provable because
they lacked *committed* per-evaluation evidence, even though the test suite is green. This module
persists honest evidence so ``northstar release verify`` can decide each gate mechanically.

Honesty rules (LAW-20, ``NOT RUN != PASS``):

* An evaluation is ``passed`` only when it has **concrete committed evidence**. Sources, in order:
  (1) the committed ``tests/evaluation_evidence_map.py`` traceability map — SPECIFIC proving pytest
  node id(s) that must all be present and passing in the actual JUnit run (never a name substring);
  (2) the mapped pytest cases discovered because a test names the ``EVAL-…`` id (legacy fallback);
  (3) a harness output under ``evidence/local`` grades it pass; (4) a previously-committed
  evaluation-result pack carries it.
* Inherently human/infra-graded evaluations (:data:`HUMAN_NOT_RUN`) are ``not_run`` with the exact
  reason — never faked to ``passed``.
* Every other evaluation with no mapped evidence is ``not_run`` with a precise gap reason.

The generated pack follows ``spec/evaluations/evidence-pack-layout.md`` and mirrors
``spec/audit/sample-evidence-pack``: ``evaluation-results/``, ``scorecard.yaml``, ``sbom/``,
``approvals.yaml`` and ``MANIFEST.sha256`` with **real** SHA-256 digests (no zero placeholders —
fixes the ``verify_005`` LOW finding).
"""

from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import yaml

RELEASE = "0.3.0"
SUITE_VERSION = "0.3.0"
SUBJECT = {"type": "release", "id": "northstar", "version": RELEASE}
EVAL_TOKEN_RE = re.compile(r"EVAL-[A-Z0-9]+-\d{3}")

# Inherently human/infra-graded evaluations. These can NEVER be machine-passed; they are recorded
# not_run with an honest reason so a gate that blocks on them is reported blocked-pending-human.
HUMAN_NOT_RUN: dict[str, str] = {
    "EVAL-AI-009": (
        "Pedagogy/instructional-quality is human-graded against a subjective rubric "
        "(spec/evaluations/human-eval-rubrics.md); no automated test can assert it. "
        "Requires qualified educator review."
    ),
    "EVAL-PERF-002": (
        "Core Web Vitals (LCP/INP/CLS) require live-browser/RUM field measurement that is not "
        "available in this offline suite; budget is recorded in "
        "evidence/local/perf/EVAL-PERF-002-core-web-vitals-budget.json but the field run is "
        "pending."
    ),
    "EVAL-OPS-005": (
        "Incident-response tabletop is a facilitated human exercise "
        "(evidence/local/ops/EVAL-OPS-005-incident-tabletop.md); needs live participant sign-off."
    ),
    "EVAL-LEG-001": (
        "Legal/market-launch review requires qualified legal counsel sign-off; not machine-graded."
    ),
    "EVAL-LEGAL-001": (
        "Legal/market-launch review requires qualified legal counsel sign-off; not machine-graded."
    ),
}

GAP_REASON = (
    "No committed automated test, harness output, or evaluation-result pack maps to this "
    "evaluation for release {release} (NOT RUN != PASS)."
)

# Previously-committed evidence packs whose graded results are reused as committed evidence.
_COMMITTED_PACKS = ("kernel-beta", "identity-ga", "knowledge-ga")


def _now_z() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --- status resolution model -----------------------------------------------------------


# reason_kind: passed | failed | human | gap
@dataclass
class EvalOutcome:
    eval_id: str
    status: str  # passed | failed | not_run
    reason_kind: str
    detail: str
    metrics: list[dict[str, Any]] = field(default_factory=list)
    # pack-relative evidence uris (files must exist in the pack before MANIFEST is written)
    evidence_uris: list[str] = field(default_factory=list)


def build_test_pass_index(junit_path: str) -> dict[str, tuple[int, int]]:
    """Map a test module (dotted, e.g. ``tests.cli.test_release_verify``) to ``(total, failed)``."""
    index: dict[str, tuple[int, int]] = {}
    tree = ET.parse(junit_path)  # noqa: S314 - trusted, self-produced junit file
    for case in tree.iter("testcase"):
        # ``classname`` is the dotted test-module path for pytest function-style tests.
        classname = case.get("classname") or ""
        failed = any(child.tag in ("failure", "error") for child in case)
        total, fails = index.get(classname, (0, 0))
        index[classname] = (total + 1, fails + (1 if failed else 0))
    return index


def build_node_pass_index(junit_path: str) -> dict[tuple[str, str], tuple[int, int]]:
    """Map ``(classname, base_func_name)`` to ``(total, failed)`` from a pytest JUnit file.

    ``base_func_name`` strips any ``[param]`` suffix so a parametrised test is matched by its
    function name (all of its cases must pass for the aggregate to be failure-free).
    """
    index: dict[tuple[str, str], tuple[int, int]] = {}
    tree = ET.parse(junit_path)  # noqa: S314 - trusted, self-produced junit file
    for case in tree.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        base = name.split("[", 1)[0]
        failed = any(child.tag in ("failure", "error") for child in case)
        total, fails = index.get((classname, base), (0, 0))
        index[(classname, base)] = (total + 1, fails + (1 if failed else 0))
    return index


def node_id_to_key(node_id: str) -> tuple[str, str]:
    """Convert a pytest node id (``path/file.py::[Class::]func``) to ``(classname, func)``.

    The classname mirrors pytest's JUnit ``classname`` attribute: the dotted module path, with any
    intermediate class names appended (e.g. ``tests.foo.test_bar.Cls`` for a method).
    """
    file_part, _, rest = node_id.partition("::")
    module_dotted = file_part[:-3].replace("/", ".") if file_part.endswith(".py") else file_part
    segments = [s for s in rest.split("::") if s]
    if not segments:
        return module_dotted, ""
    func = segments[-1]
    classes = segments[:-1]
    classname = ".".join([module_dotted, *classes]) if classes else module_dotted
    return classname, func


def load_evidence_map(repo_root: str) -> dict[str, Any]:
    """Load the committed ``tests/evaluation_evidence_map.py`` traceability map by file path.

    Loaded standalone (no dependency on the ``tests`` package being importable) so the generator
    works from a clean checkout. Returns ``{}`` if the file is absent (e.g. synthetic test roots).
    """
    path = os.path.join(repo_root, "tests", "evaluation_evidence_map.py")
    if not os.path.isfile(path):
        return {}
    import sys

    module_name = "ns_eval_evidence_map"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass annotation resolution can find the module (``__future__``
    # string annotations look the owning module up in ``sys.modules``).
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return dict(getattr(module, "EVAL_EVIDENCE_MAP", {}))


def build_eval_test_map(tests_dir: str, repo_root: str) -> dict[str, set[str]]:
    """Map each ``EVAL-…`` id to the set of dotted test modules that reference it.

    A test file that names an evaluation id is a *committed claim* that it exercises that
    evaluation; that is the honest, deterministic evidence link used to grade automated evals.
    """
    mapping: dict[str, set[str]] = {}
    for dirpath, _dirs, files in os.walk(tests_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            ids = set(EVAL_TOKEN_RE.findall(text))
            if not ids:
                continue
            rel = os.path.relpath(path, repo_root)
            dotted = rel[:-3].replace(os.sep, ".")
            for eid in ids:
                mapping.setdefault(eid, set()).add(dotted)
    return mapping


def _grade_harness(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
    """Grade a committed harness output into ``(status, metrics)`` or ``None`` if not gradable."""
    result = data.get("result")
    if isinstance(result, str):
        status = {"pass": "passed", "fail": "failed", "not-run": "not_run"}.get(result)
        if status is None:
            return None
        return status, [
            {
                "name": "harness_result",
                "value": result,
                "threshold": "pass",
                "result": "pass"
                if status == "passed"
                else ("fail" if status == "failed" else "info"),
            }
        ]
    # accessibility report schema: zero serious/critical axe violations across all runs -> passed.
    summary = data.get("axe_results_summary")
    if isinstance(summary, dict) and isinstance(summary.get("runs"), list):
        runs = summary["runs"]
        worst = max(
            (
                int(r.get("violations_critical", 0)) + int(r.get("violations_serious", 0))
                for r in runs
            ),
            default=0,
        )
        return ("passed" if worst == 0 else "failed"), [
            {
                "name": "axe_serious_critical_violations",
                "value": worst,
                "threshold": 0,
                "result": "pass" if worst == 0 else "fail",
            }
        ]
    return None


def _discover_harness(local_dir: str) -> dict[str, tuple[str, str, list[dict[str, Any]]]]:
    """Map ``eval_id -> (abs_path, status, metrics)`` for gradable harness outputs in local_dir."""
    found: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}
    for dirpath, _dirs, files in os.walk(local_dir):
        for name in files:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            eid = data.get("evaluation_id")
            if isinstance(eid, str):
                graded = _grade_harness(data)
                if graded is None:
                    continue
                # keep the first pass/fail; do not let a later file downgrade a pass
                if eid not in found:
                    found[eid] = (path, graded[0], graded[1])
                continue
            # Combined multi-eval report (e.g. the accessibility harness): credit each
            # ``evaluation_coverage`` entry whose result is ``pass`` ONLY when corroborated by the
            # report summary showing zero critical/serious axe violations (honest — the automatable
            # slice is genuinely proven; manual/human entries are not marked pass by the harness).
            for entry, status, metrics in _grade_multi_eval_report(data):
                if entry not in found:
                    found[entry] = (path, status, metrics)
    return found


def _grade_multi_eval_report(
    data: dict[str, Any],
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """Grade a combined report's ``evaluation_coverage`` against its own ``summary`` (fail-closed).

    Two corroboration shapes are credited; both require the report to attest its own passing state
    before ANY ``evaluation_coverage`` entry is credited:

    * **accessibility axe-zero** (original) — the ``summary`` shows zero critical AND zero serious
      axe violations across all swept surfaces; or
    * **generic frontend suite** — the committed suite ``summary`` shows ``suite_passed == true``
      AND ``tests_failed == 0``. This lets a parallel Studio/marketplace/DX task's committed
      evaluation-coverage report be folded in honestly on the next regeneration.

    In either shape, only an entry whose ``result`` is ``pass`` is credited ``passed``; any entry
    with a non-``pass`` result is simply not credited (it remains ``not_run`` upstream). If NEITHER
    corroboration holds (e.g. ``suite_passed`` false or ``tests_failed`` > 0, or non-zero axe
    violations), nothing is credited — the report is fail-closed.
    """
    coverage = data.get("evaluation_coverage")
    summary = data.get("summary")
    if not isinstance(coverage, list) or not isinstance(summary, dict):
        return []
    axe_corroborated = (
        int(summary.get("total_critical", 1)) == 0 and int(summary.get("total_serious", 1)) == 0
    )
    suite_corroborated = (
        summary.get("suite_passed") is True and int(summary.get("tests_failed", 1)) == 0
    )
    if not (axe_corroborated or suite_corroborated):
        return []
    if axe_corroborated:
        metrics = [
            {
                "name": "axe_serious_critical_violations",
                "value": int(summary.get("total_critical", 0))
                + int(summary.get("total_serious", 0)),
                "threshold": 0,
                "result": "pass",
            }
        ]
    else:
        metrics = [
            {
                "name": "frontend_suite_tests_failed",
                "value": int(summary.get("tests_failed", 0)),
                "threshold": 0,
                "result": "pass",
            }
        ]
    graded: list[tuple[str, str, list[dict[str, Any]]]] = []
    for entry in coverage:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("evaluation_id")
        result = entry.get("result")
        if not isinstance(eid, str) or result != "pass":
            continue
        graded.append((eid, "passed", [dict(m) for m in metrics]))
    return graded


def _discover_committed_packs(evidence_root: str) -> dict[str, str]:
    """Map ``eval_id -> abs path`` for previously-committed evaluation-result packs."""
    found: dict[str, str] = {}
    for pack in _COMMITTED_PACKS:
        results_dir = os.path.join(evidence_root, pack, "evaluation-results")
        if not os.path.isdir(results_dir):
            continue
        for name in sorted(os.listdir(results_dir)):
            if name.endswith(".json"):
                found.setdefault(name[:-5], os.path.join(results_dir, name))
    return found


@dataclass
class GeneratorContext:
    repo_root: str
    evidence_root: str
    catalog: dict[str, dict[str, Any]]
    gates: list[dict[str, Any]]
    junit_path: str
    test_pass_index: dict[str, tuple[int, int]]
    node_pass_index: dict[tuple[str, str], tuple[int, int]]
    eval_test_map: dict[str, set[str]]
    evidence_map: dict[str, Any]
    harness: dict[str, tuple[str, str, list[dict[str, Any]]]]
    committed: dict[str, str]


def _resolve_from_evidence_map(
    eval_id: str, ctx: GeneratorContext
) -> tuple[EvalOutcome, list[tuple[str, str]]]:
    """Grade an evaluation from the committed ``tests/evaluation_evidence_map.py`` entry.

    An eval is ``passed`` only when EVERY mapped proving node id is present in the actual JUnit run
    AND none of them failed (and any mapped harness artifact grades pass). A mapped node missing
    from the run is ``not_run`` (the proof was not actually executed — NOT RUN != PASS); a mapped
    node that ran and failed is ``failed``. This never grades by name substring.
    """
    copies: list[tuple[str, str]] = []
    entry = ctx.evidence_map[eval_id]
    nodes: tuple[str, ...] = tuple(getattr(entry, "nodes", ()) or ())
    harness_paths: tuple[str, ...] = tuple(getattr(entry, "harness", ()) or ())

    present = 0
    failed = 0
    missing: list[str] = []
    for node in nodes:
        key = node_id_to_key(node)
        total, node_failed = ctx.node_pass_index.get(key, (0, 0))
        if total == 0:
            missing.append(node)
            continue
        present += 1
        failed += node_failed

    evidence_uris: list[str] = []
    harness_ok = True
    harness_detail: list[str] = []
    for rel in harness_paths:
        src = os.path.join(ctx.repo_root, rel)
        graded = None
        if os.path.isfile(src):
            try:
                with open(src, encoding="utf-8") as fh:
                    graded = _grade_harness(json.load(fh))
            except (OSError, json.JSONDecodeError):
                graded = None
        if graded is None or graded[0] != "passed":
            harness_ok = False
            harness_detail.append(f"harness {rel} not gradable-pass")
            continue
        dest_rel = f"checks/harness/{os.path.basename(src)}"
        copies.append((dest_rel, src))
        evidence_uris.append(dest_rel)
        harness_detail.append(f"harness {rel} graded pass")

    if nodes:
        evidence_uris.append("checks/pytest-junit.xml")

    if missing:
        detail = (
            f"mapped proving test(s) not present in the run: {', '.join(sorted(missing))} "
            "(NOT RUN != PASS)"
        )
        return EvalOutcome(eval_id, "not_run", "gap", detail), copies
    if failed:
        detail = f"{failed} mapped proving pytest case(s) failed in the run"
        return EvalOutcome(eval_id, "failed", "failed", detail, evidence_uris=evidence_uris), copies
    if not harness_ok:
        return (
            EvalOutcome(eval_id, "not_run", "gap", "; ".join(harness_detail)),
            copies,
        )

    metrics = [
        {
            "name": "mapped_proving_nodes_failed",
            "value": failed,
            "threshold": 0,
            "result": "pass",
        },
        {
            "name": "mapped_proving_nodes_total",
            "value": present + len(harness_paths),
            "threshold": max(1, len(nodes) + len(harness_paths)),
            "result": "pass",
            "slice": "all",
        },
    ]
    detail = (
        f"committed traceability map: {present} mapped pytest node(s) present and passing"
        + (f"; {len(harness_paths)} harness artifact(s) pass" if harness_paths else "")
        + f" — {entry.rationale}"
    )
    return EvalOutcome(eval_id, "passed", "passed", detail, metrics, evidence_uris), copies


def _resolve_eval(
    eval_id: str, ctx: GeneratorContext, pack_dir: str
) -> tuple[EvalOutcome, list[tuple[str, str]]]:
    """Resolve one evaluation into an outcome and the artifacts to copy into the pack.

    Returns ``(outcome, [(pack_relpath, source_abspath), ...])``.
    """
    copies: list[tuple[str, str]] = []

    if eval_id in HUMAN_NOT_RUN:
        return (
            EvalOutcome(eval_id, "not_run", "human", HUMAN_NOT_RUN[eval_id]),
            copies,
        )

    if eval_id in ctx.evidence_map:
        return _resolve_from_evidence_map(eval_id, ctx)

    if eval_id in ctx.harness:
        src, status, metrics = ctx.harness[eval_id]
        rel = f"checks/harness/{os.path.basename(src)}"
        copies.append((rel, src))
        detail = f"harness evidence {os.path.relpath(src, ctx.repo_root)} graded {status}"
        kind = "passed" if status == "passed" else ("failed" if status == "failed" else "gap")
        return EvalOutcome(eval_id, status, kind, detail, metrics, [rel]), copies

    if eval_id in ctx.eval_test_map:
        modules = sorted(ctx.eval_test_map[eval_id])
        total = sum(ctx.test_pass_index.get(m, (0, 0))[0] for m in modules)
        failed = sum(ctx.test_pass_index.get(m, (0, 0))[1] for m in modules)
        if total > 0:
            status = "passed" if failed == 0 else "failed"
            metrics = [
                {
                    "name": "mapped_tests_failed",
                    "value": failed,
                    "threshold": 0,
                    "result": "pass" if failed == 0 else "fail",
                },
                {
                    "name": "mapped_tests_total",
                    "value": total,
                    "threshold": 1,
                    "result": "pass",
                    "slice": "all",
                },
            ]
            detail = f"{total} mapped pytest case(s) in {len(modules)} module(s); {failed} failed"
            return (
                EvalOutcome(
                    eval_id,
                    status,
                    "passed" if failed == 0 else "failed",
                    detail,
                    metrics,
                    ["checks/pytest-junit.xml"],
                ),
                copies,
            )

    if eval_id in ctx.committed:
        src = ctx.committed[eval_id]
        try:
            with open(src, encoding="utf-8") as fh:
                data = json.load(fh)
            status = str(data.get("status", "not_run"))
        except (OSError, json.JSONDecodeError):
            status = "not_run"
        if status == "passed":
            rel = f"checks/reused/{eval_id}.json"
            copies.append((rel, src))
            detail = f"reused committed evaluation-result {os.path.relpath(src, ctx.repo_root)}"
            metrics = [
                {"name": "mapped_checks_failed", "value": 0, "threshold": 0, "result": "pass"}
            ]
            return EvalOutcome(eval_id, "passed", "passed", detail, metrics, [rel]), copies

    return (
        EvalOutcome(eval_id, "not_run", "gap", GAP_REASON.format(release=RELEASE)),
        copies,
    )


def _evaluation_result_json(outcome: EvalOutcome, artifact_sha: dict[str, str]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if outcome.status == "not_run":
        findings.append({"severity": "info", "code": "NOT_RUN", "message": outcome.detail})
    elif outcome.status == "failed":
        findings.append({"severity": "high", "code": "EVAL_FAILED", "message": outcome.detail})
    evidence = [
        {"uri": uri, "sha256": artifact_sha[uri]}
        for uri in outcome.evidence_uris
        if uri in artifact_sha
    ]
    return {
        "evaluation_id": outcome.eval_id,
        "suite_id": "northstar-release-evidence",
        "suite_version": SUITE_VERSION,
        "subject": dict(SUBJECT),
        "run_at": _now_z(),
        "status": outcome.status,
        "metrics": outcome.metrics,
        "findings": findings,
        "evidence": evidence,
    }


def _classify_gate(
    gate: dict[str, Any], status_by_eval: dict[str, str], kind_by_eval: dict[str, str]
) -> dict[str, Any]:
    blocking = list(gate.get("blocking_evaluation_ids", []) or [])
    rows = []
    non_passed = []
    for eid in blocking:
        st = status_by_eval.get(eid, "not_run")
        kind = kind_by_eval.get(eid, "gap")
        rows.append({"id": eid, "status": st, "reason_kind": kind})
        if st != "passed":
            non_passed.append((eid, st, kind))
    has_gap = any(kind in ("gap", "failed") for _e, _s, kind in non_passed)
    has_human = any(kind == "human" for _e, _s, kind in non_passed)
    if not non_passed:
        category = "machine-green"
    elif has_gap:
        category = "blocked-gap"
    elif has_human:
        category = "blocked-pending-human"
    else:  # defensive
        category = "blocked-gap"
    return {
        "gate_id": gate["gate_id"],
        "category": category,
        "machine_check_pass": category == "machine-green",
        "blocking_evaluation_ids": blocking,
        "passed": sum(1 for r in rows if r["status"] == "passed"),
        "blocking_detail": rows,
        "blockers": [{"id": e, "status": s, "reason": kind} for e, s, kind in non_passed],
    }


def _fold_approvals(md_path: str) -> dict[str, Any]:
    """Fold ``evidence/local/approvals/gate-approvals.md`` council sign-offs into schema shape."""
    approvals: list[dict[str, Any]] = []
    header_re = re.compile(r"^##\s+(GATE-[A-Z0-9-]+)\s+.*APPROVED\s*\(([^)]+)\)", re.IGNORECASE)
    date_re = re.compile(r"^-\s*date:\s*(\S+)")
    if os.path.isfile(md_path):
        current: dict[str, Any] | None = None
        with open(md_path, encoding="utf-8") as fh:
            for line in fh:
                m = header_re.match(line.strip())
                if m:
                    current = {
                        "gate_id": m.group(1),
                        "approver_role": m.group(2).strip(),
                        "approver_id": "council:"
                        + m.group(2).strip().split(",")[0].lower().replace(" ", "-"),
                        "decision": "approved",
                        "decided_at": None,
                        "evidence_reviewed": ["evidence/local/approvals/gate-approvals.md"],
                    }
                    approvals.append(current)
                    continue
                if current is not None:
                    dm = date_re.match(line.strip())
                    if dm and current["decided_at"] is None:
                        current["decided_at"] = dm.group(1)
        for a in approvals:
            if a["decided_at"] is None:
                a["decided_at"] = "unknown"
    return {
        "release": RELEASE,
        "note": (
            "Council approvals folded from evidence/local/approvals/gate-approvals.md. Human "
            "approval is additional to, never a substitute for, machine-green blocking evaluations."
        ),
        "approvals": approvals,
        "exceptions": [],
    }


def _source_sbom(repo_root: str) -> dict[str, Any]:
    """Build a CycloneDX source SBOM from the platform project's declared dependencies."""
    pyproject = os.path.join(repo_root, "platform", "python", "pyproject.toml")
    components: list[dict[str, Any]] = []
    version = RELEASE
    try:
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        project = data.get("project", {})
        version = str(project.get("version", RELEASE))
        for dep in project.get("dependencies", []):
            name = re.split(r"[<>=!~\[ ]", dep, maxsplit=1)[0].strip()
            spec = dep[len(name) :].strip()
            components.append(
                {
                    "type": "library",
                    "name": name,
                    "version": spec or "*",
                    "purl": f"pkg:pypi/{name}",
                    "scope": "required",
                }
            )
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": _now_z(),
            "component": {"type": "application", "name": "northstar", "version": version},
            "tools": [
                {"vendor": "northstar", "name": "release-evidence-generator", "version": version}
            ],
            "properties": [
                {"name": "sbom:kind", "value": "source"},
                {
                    "name": "sbom:note",
                    "value": (
                        "Source SBOM from platform/python/pyproject.toml declared dependencies. "
                        "The CI supply-chain job (anchore/sbom-action) produces the signed "
                        "release SBOM."
                    ),
                },
            ],
        },
        "components": components,
    }


def _write_manifest(pack_dir: str) -> str:
    """Write ``MANIFEST.sha256`` with real digests over every pack file (excluding itself)."""
    lines: list[str] = []
    for dirpath, _dirs, files in os.walk(pack_dir):
        for name in sorted(files):
            if name == "MANIFEST.sha256":
                continue
            abspath = os.path.join(dirpath, name)
            rel = os.path.relpath(abspath, pack_dir).replace(os.sep, "/")
            lines.append(f"{_sha256_file(abspath)}  ./{rel}")
    lines.sort(key=lambda line: line.split("  ./", 1)[1])
    manifest_path = os.path.join(pack_dir, "MANIFEST.sha256")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return manifest_path


@dataclass
class GenerationReport:
    release: str
    pack_dir: str
    evaluations_total: int
    passed: int
    failed: int
    not_run_human: int
    not_run_gap: int
    gates: list[dict[str, Any]]
    files_written: int

    @property
    def machine_green(self) -> int:
        return sum(1 for g in self.gates if g["category"] == "machine-green")

    @property
    def blocked_pending_human(self) -> int:
        return sum(1 for g in self.gates if g["category"] == "blocked-pending-human")

    @property
    def blocked_gap(self) -> int:
        return sum(1 for g in self.gates if g["category"] == "blocked-gap")


def generate(repo_root: str, junit_path: str) -> GenerationReport:
    """Generate the release ``0.3.0`` evidence pack from committed test/harness evidence."""
    evidence_root = os.path.join(repo_root, "evidence")
    pack_dir = os.path.join(evidence_root, RELEASE)
    tests_dir = os.path.join(repo_root, "tests")

    catalog_doc = _load_yaml(os.path.join(repo_root, "spec", "evaluations", "catalog.yaml"))
    catalog = {e["id"]: e for e in catalog_doc.get("evaluations", [])}
    gates_doc = _load_yaml(os.path.join(repo_root, "spec", "evaluations", "release-gates.yaml"))
    gates = gates_doc.get("gates", [])

    ctx = GeneratorContext(
        repo_root=repo_root,
        evidence_root=evidence_root,
        catalog=catalog,
        gates=gates,
        junit_path=junit_path,
        test_pass_index=build_test_pass_index(junit_path),
        node_pass_index=build_node_pass_index(junit_path),
        eval_test_map=build_eval_test_map(tests_dir, repo_root),
        evidence_map=load_evidence_map(repo_root),
        harness=_discover_harness(os.path.join(evidence_root, "local")),
        committed=_discover_committed_packs(evidence_root),
    )

    results_dir = os.path.join(pack_dir, "evaluation-results")
    os.makedirs(results_dir, exist_ok=True)

    # Resolve every catalog evaluation and gather artifacts to copy.
    outcomes: dict[str, EvalOutcome] = {}
    artifact_copies: dict[str, str] = {}  # pack_relpath -> source_abspath
    for eval_id in sorted(catalog):
        outcome, copies = _resolve_eval(eval_id, ctx, pack_dir)
        outcomes[eval_id] = outcome
        for rel, src in copies:
            artifact_copies.setdefault(rel, src)

    # Always copy the junit file if any eval relies on it.
    if any("checks/pytest-junit.xml" in o.evidence_uris for o in outcomes.values()):
        artifact_copies.setdefault("checks/pytest-junit.xml", junit_path)

    # Copy artifacts, then compute their real digests.
    artifact_sha: dict[str, str] = {}
    for rel, src in artifact_copies.items():
        dest = os.path.join(pack_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
        artifact_sha[rel] = _sha256_file(dest)

    files_written = 0
    for eval_id in sorted(outcomes):
        payload = _evaluation_result_json(outcomes[eval_id], artifact_sha)
        with open(os.path.join(results_dir, f"{eval_id}.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        files_written += 1

    status_by_eval = {e: o.status for e, o in outcomes.items()}
    kind_by_eval = {e: o.reason_kind for e, o in outcomes.items()}
    gate_rows = [_classify_gate(g, status_by_eval, kind_by_eval) for g in gates]

    scorecard = {
        "scorecard_version": RELEASE,
        "release": RELEASE,
        "subject": dict(SUBJECT),
        "generated_at": _now_z(),
        "note": (
            "Authoritative machine-readable release scorecard. A gate is machine-green only when "
            "all blocking evaluations are result==passed and no expired exception applies "
            "(spec/evaluations/release-gates.yaml). NOT RUN != PASS."
        ),
        "summary": {
            "gates_total": len(gate_rows),
            "machine_green": sum(1 for g in gate_rows if g["category"] == "machine-green"),
            "blocked_pending_human": sum(
                1 for g in gate_rows if g["category"] == "blocked-pending-human"
            ),
            "blocked_gap": sum(1 for g in gate_rows if g["category"] == "blocked-gap"),
            "evaluations_total": len(outcomes),
            "evaluations_passed": sum(1 for o in outcomes.values() if o.status == "passed"),
            "evaluations_failed": sum(1 for o in outcomes.values() if o.status == "failed"),
            "evaluations_not_run": sum(1 for o in outcomes.values() if o.status == "not_run"),
        },
        "gates": [
            {
                "gate_id": g["gate_id"],
                "result": g["category"],
                "blocking_evaluation_ids": g["blocking_evaluation_ids"],
                "blockers": g["blockers"],
            }
            for g in gate_rows
        ],
        "evaluations": [
            {"id": e, "status": outcomes[e].status, "reason_kind": outcomes[e].reason_kind}
            for e in sorted(outcomes)
        ],
    }
    with open(os.path.join(pack_dir, "scorecard.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(scorecard, fh, sort_keys=False, default_flow_style=False)
    files_written += 1

    approvals = _fold_approvals(
        os.path.join(evidence_root, "local", "approvals", "gate-approvals.md")
    )
    with open(os.path.join(pack_dir, "approvals.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(approvals, fh, sort_keys=False, default_flow_style=False)
    files_written += 1

    sbom_dir = os.path.join(pack_dir, "sbom")
    os.makedirs(sbom_dir, exist_ok=True)
    with open(os.path.join(sbom_dir, "source-sbom.cyclonedx.json"), "w", encoding="utf-8") as fh:
        json.dump(_source_sbom(repo_root), fh, indent=2)
        fh.write("\n")
    files_written += 1

    _write_manifest(pack_dir)
    files_written += 1

    return GenerationReport(
        release=RELEASE,
        pack_dir=pack_dir,
        evaluations_total=len(outcomes),
        passed=sum(1 for o in outcomes.values() if o.status == "passed"),
        failed=sum(1 for o in outcomes.values() if o.status == "failed"),
        not_run_human=sum(1 for o in outcomes.values() if o.reason_kind == "human"),
        not_run_gap=sum(1 for o in outcomes.values() if o.reason_kind == "gap"),
        gates=gate_rows,
        files_written=files_written,
    )
