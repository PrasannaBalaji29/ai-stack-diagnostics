"""Compares .env with its template. Reports key NAMES only, never values."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from diagnostics.models import Finding, Severity
from diagnostics.safety import read_text, safe_file

CHECK = "env_keys"
ENV_FILE = ".env"
TEMPLATE_FILES = (".env.example", ".env.sample", ".env.template", ".env.dist")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
PLACEHOLDER_PREFIXES = (
    "your", "<", "changeme", "change_me", "change-me", "replace", "todo",
    "xxx", "insert", "put_", "put-", "enter", "example",
)
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"hf_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
)
MAX_NAMES_SHOWN = 30


def parse_env(text: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    duplicates: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not KEY_RE.match(key):
            continue
        value = value.strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if key in values:
            duplicates.append(key)
        values[key] = value
    return values, duplicates


def is_unset(value: str) -> bool:
    v = value.strip().lower()
    return (
        v == ""
        or v in ("none", "null", "undefined")
        or v.startswith(PLACEHOLDER_PREFIXES)
        or v.endswith(("_here", "-here"))
        or re.fullmatch(r"[x*.\-_]+", v) is not None
    )


def _names(keys: list[str]) -> str:
    keys = sorted(keys)
    shown = ", ".join(keys[:MAX_NAMES_SHOWN])
    return shown + (f" (+{len(keys) - MAX_NAMES_SHOWN} more)" if len(keys) > MAX_NAMES_SHOWN else "")


def _gitignore_covers_env(project: Path) -> bool:
    path = safe_file(project, ".gitignore")
    text = read_text(path) if path else None
    ignored = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line.lstrip("!").strip().lstrip("/").removeprefix("**/")
        if not pattern or pattern.endswith("/"):
            continue  # a trailing slash only matches directories
        if fnmatch.fnmatch(ENV_FILE, pattern):
            ignored = not negated
    return ignored


def _inside_git_repo(project: Path) -> bool:
    return any((p / ".git").exists() for p in (project, *project.parents))


def _template(project: Path) -> tuple[str, Path] | None:
    for name in TEMPLATE_FILES:
        path = safe_file(project, name)
        if path is not None:
            return name, path
    return None


def run(project: Path) -> list[Finding]:
    env_path = safe_file(project, ENV_FILE)
    template = _template(project)
    findings: list[Finding] = []

    if env_path is None and template is None:
        return [Finding(
            check=CHECK, severity=Severity.info, title="No .env or template file found",
            detail="Nothing to compare. Projects that use API keys should ship a .env.example.",
        )]

    if template is not None:
        name, path = template
        text = read_text(path) or ""
        leaked = [k for k, v in parse_env(text)[0].items() if any(p.search(v) for p in SECRET_PATTERNS)]
        if leaked:
            findings.append(Finding(
                check=CHECK, severity=Severity.critical, title=f"{name} may contain real secrets",
                detail=f"These variables look like real keys: {_names(leaked)}. "
                       "Template files are usually committed to Git. Rotate the keys.",
                fix=f"Replace the values in {name} with placeholders",
            ))

    if env_path is not None and not _gitignore_covers_env(project):
        in_repo = _inside_git_repo(project)
        findings.append(Finding(
            check=CHECK, severity=Severity.critical if in_repo else Severity.warning,
            title=".env is not covered by .gitignore",
            detail="Your secrets could be committed and leaked, especially in a public repo. "
                   "(Only this folder's .gitignore was checked.)",
            fix='Add-Content .gitignore ".env"',
        ))

    if env_path is None:
        template_name = template[0] if template else "The template file"
        findings.append(Finding(
            check=CHECK, severity=Severity.warning, title=".env is missing",
            detail=f"{template_name} exists but there is no .env to hold the real values.",
            fix=f"Copy-Item {template_name} .env",
        ))
        return findings

    env, duplicates = parse_env(read_text(env_path) or "")
    expected = set(parse_env(read_text(template[1]) or "")[0]) if template else set(env)
    missing = sorted(expected - set(env))
    unset = sorted(k for k in expected & set(env) if is_unset(env[k]))
    if missing:
        findings.append(Finding(
            check=CHECK, severity=Severity.critical, title=f"{len(missing)} variable(s) missing from .env",
            detail="Missing: " + _names(missing), fix="Add these names to .env with real values",
        ))
    if unset:
        findings.append(Finding(
            check=CHECK, severity=Severity.critical, title=f"{len(unset)} variable(s) empty or still a placeholder",
            detail="Needs a real value: " + _names(unset), fix="Edit .env and fill in real values",
        ))
    if duplicates:
        findings.append(Finding(
            check=CHECK, severity=Severity.warning, title="Duplicate variables in .env",
            detail="Defined more than once (the last one wins): " + _names(sorted(set(duplicates))),
        ))
    if not missing and not unset:
        findings.append(Finding(
            check=CHECK, severity=Severity.ok, title="All expected variables are set",
            detail=f"{len(expected)} variable(s) checked. Values are never shown.",
        ))
    return findings
