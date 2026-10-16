"""Shared result types. Every check returns a list of Finding objects."""

from enum import Enum

from pydantic import BaseModel


class Severity(str, Enum):
    critical = "critical"  # project is broken or secrets are at risk
    error = "error"  # the check itself failed, so the result is unknown
    warning = "warning"  # likely to cause trouble
    info = "info"  # useful context
    ok = "ok"  # verified fine


SEVERITY_RANK = {s: i for i, s in enumerate(Severity)}


class Finding(BaseModel):
    check: str
    severity: Severity
    title: str
    detail: str
    fix: str | None = None  # a suggested command; this server never runs it
