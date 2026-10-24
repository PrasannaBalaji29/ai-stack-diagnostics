"""Use the diagnostics from a terminal, no Claude Desktop or browser needed.

    python -m diagnostics [PROJECT_FOLDER]            health report for a project (default: this folder)
    python -m diagnostics --json                      the same report as JSON
    python -m diagnostics --fail-on warning           exit code 1 when a warning or worse is found (for CI)
    python -m diagnostics --explain [PROJECT_FOLDER]  read an error message from stdin and explain it

Read-only, like everything else here. Exit codes: 0 fine, 1 problems at or above --fail-on, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from diagnostics.checks import explain_error
from diagnostics.diagnose import diagnose
from diagnostics.models import Finding
from diagnostics.safety import ProjectPathError, resolve_project_path

MAX_STDIN_CHARS = 200_000
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
_COLORS = {"critical": "31", "error": "31", "warning": "33", "info": "36", "ok": "32"}


def _use_color(stream) -> bool:
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return False
    if sys.platform == "win32":  # old Windows consoles print the escape codes as text
        return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") or os.environ.get("ANSICON"))
    return True


def _tag(severity: str, color: bool) -> str:
    text = f"[{severity}]"
    return f"\x1b[{_COLORS[severity]}m{text}\x1b[0m" if color and severity in _COLORS else text


def _fix_lines(title: str, detail: str, fix: str | None, number: int | None, severity: str, color: bool) -> list[str]:
    head = f"  {number}. " if number is not None else "  - "
    lines = [f"{head}{_tag(severity, color)} {title}"]
    if detail:
        lines.append(f"     {detail}")
    if fix:
        lines.append(f"     Fix: {fix}")
    return lines


def render_report(result: dict, color: bool = False) -> str:
    status = str(result.get("status", "")).upper()
    out = [f"Project: {result.get('project', '')}", f"Health:  {result.get('score', '?')}/100 - {status}", str(result.get("summary", "")), ""]
    fixes = result.get("fix_first") or []
    if fixes:
        out.append("What to fix first")
        for item in fixes:
            out.extend(_fix_lines(item["title"], item.get("detail", ""), item.get("fix"), item.get("step"), item["severity"], color))
        out.append("")
    else:
        out.extend(["Nothing to fix.", ""])
    if result.get("passed"):
        out.append("Passed: " + "; ".join(result["passed"]))
    infos = [i["title"] for i in result.get("info") or []]
    if infos:
        out.append("Notes:  " + "; ".join(infos))
    ran, every = result.get("checks_run") or [], result.get("all_checks") or []
    skipped = [c for c in every if c not in ran]
    out.append("Checks run: " + (", ".join(ran) or "none") + (f"   (not needed here: {', '.join(skipped)})" if skipped else ""))
    return "\n".join(out)


def render_findings(findings: list[Finding], color: bool = False) -> str:
    out: list[str] = []
    for number, f in enumerate(findings, 1):
        out.extend(_fix_lines(f.title, f.detail, f.fix, number, f.severity.value, color))
    return "\n".join(out)


def _worst_rank(result: dict) -> int:
    ranks = [SEVERITY_ORDER.get(item["severity"], 0) for item in result.get("fix_first") or []]
    return max(ranks, default=0)


def _read_stdin() -> str:
    if sys.stdin.isatty():
        end = "Ctrl+Z then Enter" if sys.platform == "win32" else "Ctrl+D"
        print(f"Paste the error text, then press {end}:", file=sys.stderr)
    return sys.stdin.read(MAX_STDIN_CHARS)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a Windows console must never crash on a character it cannot show
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
    parser = argparse.ArgumentParser(prog="python -m diagnostics", description="Read-only health check for an AI/ML project.")
    parser.add_argument("path", nargs="?", default=".", help="project folder (default: the current folder)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--fail-on", choices=["critical", "error", "warning", "never"], default="error",
                        help="exit with code 1 when a problem at this level or worse is found (default: error)")
    parser.add_argument("--explain", action="store_true", help="read an error message or traceback from stdin and explain it")
    args = parser.parse_args(argv)

    color = _use_color(sys.stdout)
    if args.explain:
        project = None
        if args.path != ".":
            try:
                project = resolve_project_path(args.path)
            except ProjectPathError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        findings = explain_error.run(_read_stdin(), project)
        if args.json:
            print(json.dumps([f.model_dump(mode="json") for f in findings], indent=2))
        else:
            print(render_findings(findings, color))
        return 0

    try:
        project = resolve_project_path(args.path)
    except ProjectPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if sys.stdout.isatty() and not args.json:
        print(f"Scanning {project} ...", file=sys.stderr)
    result = diagnose(project)
    print(json.dumps(result, indent=2) if args.json else render_report(result, color))
    if args.fail_on == "never":
        return 0
    return 1 if _worst_rank(result) >= SEVERITY_ORDER[args.fail_on] else 0


if __name__ == "__main__":
    sys.exit(main())
