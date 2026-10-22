"""One prioritized report: scan the project, run only the checks it needs, sort the findings."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from diagnostics.checks import compat, env_keys, gpu, imports, ollama, ports, python_env
from diagnostics.checks.python_env import _find_venvs, _read_cfg, _site_packages
from diagnostics.models import SEVERITY_RANK, Finding, Severity
from diagnostics.registry import run_check
from diagnostics.scan import scan
from diagnostics.venvinfo import load_dists, python_version_of

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _facts(project: Path, scanned: dict) -> dict:
    """Plain facts about the project, shown in the dashboard's Environment tab."""
    facts: dict = {
        "project": str(project),
        "python_files_scanned": scanned.get("python_files_scanned", 0),
        "has_venv": bool(scanned.get("has_venv")),
        "venv_name": None, "venv_python": None, "installed_packages": None,
        "detected_stack": scanned.get("detected_stack", []),
        "other_libraries": list(scanned.get("other_libraries", []))[:30],
    }
    try:
        venvs = _find_venvs(project)
        if venvs:
            facts["venv_name"] = venvs[0].name
            py = python_version_of(_read_cfg(venvs[0]))
            facts["venv_python"] = f"{py[0]}.{py[1]}.{py[2]}" if py else None
            facts["installed_packages"] = len(load_dists(_site_packages(venvs[0]), venvs[0]))
    except Exception:  # noqa: BLE001 - facts are a bonus, never a reason to fail
        logger.exception("could not collect environment facts")
    return facts


def _build_report(project: Path, scanned: dict, wanted: list[str], findings: list[dict]) -> dict:
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

    by_check: dict[str, dict] = {}
    for item in unique:
        entry = by_check.setdefault(item["check"], {"status": "ok", "findings": []})
        entry["findings"].append({k: item[k] for k in ("severity", "title", "detail", "fix")})
        if SEVERITY_RANK[Severity(item["severity"])] < SEVERITY_RANK[Severity(entry["status"])]:
            entry["status"] = item["severity"]

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
        "all_checks": [name for name, _ in _RUNNERS.values()],
        "by_check": by_check,
        "passed": [i["title"] for i in unique if i["severity"] == "ok"],
        "info": [{"check": i["check"], "title": i["title"], "detail": i["detail"]}
                 for i in unique if i["severity"] == "info"][:MAX_INFO],
        "detected_stack": scanned.get("detected_stack", []),
        "facts": _facts(project, scanned),
    }


def diagnose_events(project: Path) -> Iterator[dict]:
    """Yields progress events as the work happens, and finally {"type": "result", "result": <report>}."""
    yield {"type": "start", "t": _now(), "project": str(project)}
    try:
        scanned = scan(project)
    except Exception:  # noqa: BLE001 - fall back to the one check every project needs
        logger.exception("scan failed inside diagnose")
        scanned = {"suggested_checks": ["check_python_env"], "suggested_ports": []}

    wanted = [t for t in scanned.get("suggested_checks", []) if t in _RUNNERS]
    if "check_python_env" not in wanted:
        wanted.append("check_python_env")
    wanted.sort(key=lambda t: _ORDER[t])
    yield {"type": "scanned", "t": _now(), "checks": [_RUNNERS[t][0] for t in wanted],
           "all_checks": [name for name, _ in _RUNNERS.values()],
           "detected_stack": scanned.get("detected_stack", []),
           "python_files": scanned.get("python_files_scanned", 0)}

    findings: list[dict] = []
    for tool in wanted:
        name, runner = _RUNNERS[tool]
        yield {"type": "check_start", "t": _now(), "check": name}
        started = time.perf_counter()
        part = _run_one(name, runner, project, scanned)
        findings.extend(part)
        worst = min((Severity(i["severity"]) for i in part), key=lambda s: SEVERITY_RANK[s], default=Severity.ok)
        yield {"type": "check_done", "t": _now(), "check": name, "status": worst.value,
               "findings": len(part), "ms": round((time.perf_counter() - started) * 1000)}

    yield {"type": "result", "t": _now(), "result": _build_report(project, scanned, wanted, findings)}


def diagnose(project: Path) -> dict:
    result: dict = {}
    for event in diagnose_events(project):
        if event["type"] == "result":
            result = event["result"]
    return result
