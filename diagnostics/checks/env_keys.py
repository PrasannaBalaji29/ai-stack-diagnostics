from pathlib import Path

from diagnostics.models import Finding, Severity

CHECK = "env_keys"
PLACEHOLDERS = ("your", "changeme", "xxx", "replace", "todo", "<")


def _parse_env(file: Path) -> dict[str, str]:
    values = {}
    for raw in file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        values[key] = value.strip().strip("\"'")
    return values


def _is_unset(value: str) -> bool:
    v = value.lower()
    return v == "" or any(v.startswith(p) or p in v for p in PLACEHOLDERS)


def _env_is_gitignored(project: Path) -> bool:
    gitignore = project / ".gitignore"
    if not gitignore.is_file():
        return False
    lines = {l.strip() for l in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()}
    return bool(lines & {".env", ".env*", "*.env", ".env/", "/.env"})


def run(project: Path) -> list[Finding]:
    env_file, example_file = project / ".env", project / ".env.example"
    findings: list[Finding] = []

    if not env_file.is_file() and not example_file.is_file():
        return [Finding(
            check=CHECK, severity=Severity.info,
            title="No .env or .env.example found",
            detail="Nothing to compare. Projects with API keys should ship a .env.example.",
        )]

    if env_file.is_file() and not _env_is_gitignored(project):
        findings.append(Finding(
            check=CHECK, severity=Severity.critical,
            title=".env is not in .gitignore",
            detail="Your secrets could be committed and leaked, especially in a public repo.",
            fix='Add-Content .gitignore ".env"',
        ))

    if example_file.is_file() and not env_file.is_file():
        findings.append(Finding(
            check=CHECK, severity=Severity.warning,
            title=".env is missing",
            detail=".env.example exists but there is no .env to hold the real values.",
            fix="Copy-Item .env.example .env",
        ))
        return findings

    if env_file.is_file():
        env = _parse_env(env_file)
        expected = set(_parse_env(example_file)) if example_file.is_file() else set(env)
        missing = sorted(expected - set(env))
        unset = sorted(k for k in expected & set(env) if _is_unset(env[k]))
        if missing:
            findings.append(Finding(
                check=CHECK, severity=Severity.critical,
                title=f"{len(missing)} variable(s) missing from .env",
                detail="Missing: " + ", ".join(missing),
                fix="Add these names to .env with real values",
            ))
        if unset:
            findings.append(Finding(
                check=CHECK, severity=Severity.critical,
                title=f"{len(unset)} variable(s) empty or still a placeholder",
                detail="Needs a real value: " + ", ".join(unset),
                fix="Edit .env and fill in real values",
            ))
        if not missing and not unset:
            findings.append(Finding(
                check=CHECK, severity=Severity.ok,
                title="All expected variables are set",
                detail=f"{len(expected)} variable(s) checked. Values are never shown.",
            ))
    return findings