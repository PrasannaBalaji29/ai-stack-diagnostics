from enum import Enum

from pydantic import BaseModel


class Severity(str, Enum):
    ok = "ok"
    info = "info"
    warning = "warning"
    critical = "critical"


class Finding(BaseModel):
    check: str
    severity: Severity
    title: str
    detail: str
    fix: str | None = None  # suggested command; never executed