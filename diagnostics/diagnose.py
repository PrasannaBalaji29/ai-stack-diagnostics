"""One prioritized report: scan the project, run only the checks it needs, sort the findings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from diagnostics.checks import compat, env_keys, gpu, imports, ollama, ports, python_env
from diagnostics.models import SEVERITY_RANK, Finding, Severity
from diagnostics.registry import run_check
from diagnostics.scan import scan

logger = logging.getLogger("ai-stack-diagnostics")

MAX_FIX_STEPS = 12
MAX_INFO = 20
PENALTY = {Severity.critical: 30, Severity.error: 15, Severity.warning: 5}

# tool name -> (check name, how to run it). Order here is also the tie-break order for the report:
# fix the Python/venv first, because fixing it can change every later result.
_RUNNERS: dict[str, tuple[str, Callable[[Path, dict], list[Finding]]]] = {
    "check_compat": ("compat", lambda p, s: compat.run(p)),
    "check_python_env": ("python_env", lambda p, s: python_env.run(p)),
    "check_imports": ("imports", lambda p, s: imports.run(p)),
    "check_env_and_keys": ("env_keys", lambda p, s: env_keys.run(p)),
    "check_ollama": ("ollama", lambda p, s: ollama.run(None)),
    "check_ports": ("ports", lambda p, s: ports.run(s.get("suggested_ports") or None)),
    "check_gpu": ("gpu", lambda p, s: gpu.run(p)),
}
_ORDER = {name: i for i, name in enumerate(_RUNNERS)}
_CHECK_ORDER = {check: i for i, (check, _) in enumerate(_RUNNERS.values())}


def _status(counts: dict[str, int]) -> str:
    if counts["critical"]:
        return "broken"
    if counts["error"] or counts["warning"]:
        return "needs attention"
    return "healthy"


def _score(counts: dict[str, int]) -> int:
    return max(0, 100 - sum(PENALTY[s] * counts[s.value] for s in PENALTY))


def _sort_key(item: dict) -> tuple[int, int]:
    return SEVERITY_RANK[Severity(item["severity"])], _CHECK_ORDER.get(item["check"], 99)


def _run_one(name: str, runner: Callable[[Path, dict], list[Finding]], project: Path, scanned: dict) -> list[dict]:
    return run_check(name, lambda: runner(project, scanned))


def diagnose(project: Path) -> dict:
    try:
        scanned = scan(project)
    except Exception:  # noqa: BLE001 - fall back to the one check every project needs
        logger.exception("scan failed inside diagnose")
        scanned = {"suggested_checks": ["check_python_env"], "suggested_ports": []}

    wanted = [t for t in scanned.get("suggested_checks", []) if t in _RUNNERS]
    if "check_python_env" not in wanted:
        wanted.append("check_python_env")
    wanted.sort(key=lambda t: _ORDER[t])

    findings: list[dict] = []
    for tool in wanted:
        name, runner = _RUNNERS[tool]
        findings.extend(_run_one(name, runner, project, scanned))

    seen: set[tuple[str, str, str]] = set()
    unique = []
    for item in findings:
        key = (item["check"], item["title"], item["detail"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    counts = {s.value: 0 for s in Severity}
    for item in unique:
        counts[item["severity"]] += 1

    problems = sorted((i for i in unique if i["severity"] in ("critical", "error", "warning")), key=_sort_key)
    fix_first = [
        {"step": n, "severity": i["severity"], "check": i["check"], "title": i["title"],
         "detail": i["detail"], "fix": i["fix"]}
        for n, i in enumerate(problems[:MAX_FIX_STEPS], start=1)
    ]

    notes: list[str] = []
    if any(i["check"] == "compat" and i["severity"] == "critical" and "Python" in i["title"] for i in unique):
        notes.append("The venv's Python version is the root problem. Fix it first, then run diagnose again: "
                     "other results may change once the environment is rebuilt.")
    if len(problems) > MAX_FIX_STEPS:
        notes.append(f"{len(problems) - MAX_FIX_STEPS} lower-priority problem(s) are not listed here; run the individual checks to see them.")

    status = _status(counts)
    if problems:
        parts = [f"{counts[s]} {s}" for s in ("critical", "error", "warning") if counts[s]]
        summary = f"{', '.join(parts)}. Start with: {problems[0]['title']}."
    else:
        summary = "No problems found by the checks that apply to this project."

    return {
        "project": str(project),
        "status": status,
        "score": _score(counts),
        "summary": summary,
        "fix_first": fix_first,
        "notes": notes,
        "counts": counts,
        "checks_run": [_RUNNERS[t][0] for t in wanted],
        "passed": [i["title"] for i in unique if i["severity"] == "ok"],
        "info": [{"check": i["check"], "title": i["title"], "detail": i["detail"]}
                 for i in unique if i["severity"] == "info"][:MAX_INFO],
        "detected_stack": scanned.get("detected_stack", []),
    }
