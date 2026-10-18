"""Checks whether ports are already in use on this machine (connect only, read-only)."""

from __future__ import annotations

import socket
import sys
from concurrent.futures import ThreadPoolExecutor

from diagnostics.models import Finding, Severity

CHECK = "ports"
MAX_PORTS = 50
CONNECT_TIMEOUT = 0.3
MAX_WORKERS = 32
COMMON_AI_PORTS = {
    5000: "Flask / MLflow",
    5432: "PostgreSQL",
    6006: "TensorBoard",
    6333: "Qdrant",
    6379: "Redis",
    7860: "Gradio",
    8000: "FastAPI / Uvicorn",
    8001: "ChromaDB",
    8501: "Streamlit",
    8888: "Jupyter",
    11434: "Ollama",
    19530: "Milvus",
}


def _in_use(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(CONNECT_TIMEOUT)
                if sock.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue  # e.g. IPv6 is disabled on this machine
    return False


def _probe(ports: list[int]) -> dict[int, bool]:
    """Check many ports at once. On Windows a closed port waits out the whole timeout,
    so checking them one by one would make a single call take many seconds."""
    if not ports:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(ports))) as pool:
        return dict(zip(ports, pool.map(_in_use, ports), strict=True))


def _who_uses(port: int) -> str:
    if sys.platform.startswith("win"):
        return f"netstat -ano | findstr :{port}"
    return f"lsof -i :{port}"


def _clean(ports: list | None) -> tuple[list[int], list[str]]:
    valid: list[int] = []
    bad: list[str] = []
    for p in ports or []:
        if isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535:
            if p not in valid:
                valid.append(p)
        else:
            bad.append(repr(p))
    return valid, bad


def run(wanted_ports: list | None = None) -> list[Finding]:
    findings: list[Finding] = []
    valid, bad = _clean(wanted_ports)
    if bad:
        findings.append(Finding(
            check=CHECK, severity=Severity.warning, title="Ignored invalid port values",
            detail="Ports must be whole numbers from 1 to 65535. Ignored: " + ", ".join(bad[:10]),
        ))
    if len(valid) > MAX_PORTS:
        findings.append(Finding(
            check=CHECK, severity=Severity.info, title=f"Only the first {MAX_PORTS} ports were checked",
            detail=f"{len(valid) - MAX_PORTS} port(s) were skipped to keep the check fast.",
        ))
        valid = valid[:MAX_PORTS]

    status = _probe(valid + [p for p in COMMON_AI_PORTS if p not in valid])

    for port in valid:
        if status[port]:
            findings.append(Finding(
                check=CHECK, severity=Severity.warning, title=f"Port {port} is already in use",
                detail="Another program is listening on it, so your app may fail to start.",
                fix=_who_uses(port),
            ))
        else:
            findings.append(Finding(check=CHECK, severity=Severity.ok, title=f"Port {port} is free", detail="Nothing is listening on it."))

    running = [f"{p} ({name})" for p, name in COMMON_AI_PORTS.items() if status[p]]
    findings.append(Finding(
        check=CHECK, severity=Severity.info, title="Common AI service ports in use",
        detail=", ".join(running) if running else "None of the common AI ports are in use.",
    ))
    return findings
