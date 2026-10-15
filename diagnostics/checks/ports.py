import socket

from diagnostics.models import Finding, Severity

CHECK = "ports"

COMMON_AI_PORTS = {
    8000: "FastAPI / Uvicorn",
    8501: "Streamlit",
    8888: "Jupyter",
    11434: "Ollama",
    6333: "Qdrant",
    8001: "ChromaDB",
    5432: "PostgreSQL",
    6379: "Redis",
}


def _in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run(wanted_ports: list[int] | None = None) -> list[Finding]:
    findings: list[Finding] = []

    for port in wanted_ports or []:
        if _in_use(port):
            findings.append(Finding(
                check=CHECK, severity=Severity.warning,
                title=f"Port {port} is already in use",
                detail="Another program is listening on it, so your app may fail to start.",
                fix=f"netstat -ano | findstr :{port}",
            ))
        else:
            findings.append(Finding(
                check=CHECK, severity=Severity.ok,
                title=f"Port {port} is free",
                detail="Nothing is listening on it.",
            ))

    running = [f"{p} ({name})" for p, name in COMMON_AI_PORTS.items() if _in_use(p)]
    findings.append(Finding(
        check=CHECK, severity=Severity.info,
        title="Common AI service ports in use",
        detail=", ".join(running) if running else "None of the common AI ports are in use.",
    ))
    return findings