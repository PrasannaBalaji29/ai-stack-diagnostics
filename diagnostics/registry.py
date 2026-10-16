"""Runs a check so that one failing check can never break the whole report."""

from __future__ import annotations

import logging
from collections.abc import Callable

from diagnostics.models import Finding, Severity
from diagnostics.safety import ProjectPathError

logger = logging.getLogger("ai-stack-diagnostics")


def run_check(name: str, func: Callable[[], list[Finding]]) -> list[dict]:
    try:
        findings = func()
    except ProjectPathError as exc:
        findings = [Finding(check=name, severity=Severity.error, title="Invalid project path", detail=str(exc))]
    except Exception as exc:  # noqa: BLE001 - a check must never crash the server
        logger.exception("Check %s crashed", name)
        findings = [Finding(
            check=name, severity=Severity.error, title="Check failed unexpectedly",
            detail=f"{type(exc).__name__}: {exc}. See the server log for the traceback.",
        )]
    return [f.model_dump(mode="json") for f in findings]
