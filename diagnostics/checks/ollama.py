"""Checks a LOCAL Ollama server: reachable, version, installed models, GPU use.

Read-only: only GET requests, only to a loopback address, never through a proxy,
and redirects are not followed.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from diagnostics.models import Finding, Severity

CHECK = "ollama"
DEFAULT_HOST = "127.0.0.1:11434"
DEFAULT_PORT = 11434
TIMEOUT = 2.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_MODELS_SHOWN = 25
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,127}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


# ProxyHandler({}) ignores HTTP_PROXY settings, which would break requests to localhost.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


class OllamaUnreachable(Exception):
    """Nothing is listening, or the connection timed out."""


class NotOllama(Exception):
    """Something answered, but not with a valid Ollama API response."""


def resolve_base_url(raw: str | None = None) -> str:
    value = (raw if raw is not None else os.environ.get("OLLAMA_HOST", "")).strip() or DEFAULT_HOST
    if "://" not in value:
        if value.count(":") > 1 and not value.startswith("["):  # a bare IPv6 address like ::1
            value = f"[{value}]"
        value = "http://" + value
    parts = urllib.parse.urlsplit(value)
    scheme = parts.scheme if parts.scheme in ("http", "https") else "http"
    host = parts.hostname or "127.0.0.1"
    port = parts.port or DEFAULT_PORT  # raises ValueError for an invalid port
    if host == "0.0.0.0":  # a server bind address, not something clients can connect to on Windows
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    netloc = f"[{host}]" if ":" in host else host
    return f"{scheme}://{netloc}:{port}"


def _is_loopback(base_url: str) -> bool:
    host = urllib.parse.urlsplit(base_url).hostname or ""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _get_json(base_url: str, path: str) -> dict:
    request = urllib.request.Request(base_url + path, method="GET", headers={"Accept": "application/json"})
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise NotOllama(f"HTTP {exc.code} from {path}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise OllamaUnreachable(str(exc)) from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise NotOllama("response too large")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise NotOllama("response is not JSON") from exc
    if not isinstance(data, dict):
        raise NotOllama("unexpected JSON shape")
    return data


def _ollama_installed() -> bool:
    if shutil.which("ollama"):
        return True
    local = os.environ.get("LOCALAPPDATA")
    return bool(local) and (Path(local) / "Programs" / "Ollama" / "ollama.exe").is_file()


def _gb(num_bytes: float) -> str:
    return f"{num_bytes / 1_000_000_000:.1f} GB"


def _model_names(data: dict) -> list[tuple[str, int]]:
    out = []
    for entry in data.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("model")
        if isinstance(name, str) and name:
            size = entry.get("size")
            out.append((name, size if isinstance(size, (int, float)) and not isinstance(size, bool) else 0))
    return out


def _check_requested_model(model: str, installed: list[str]) -> Finding:
    if not MODEL_RE.match(model):
        return Finding(
            check=CHECK, severity=Severity.warning, title="Invalid model name",
            detail="Model names may only contain letters, digits and . _ : / -",
        )
    wanted = model if ":" in model else model + ":latest"
    if model in installed or wanted in installed:
        return Finding(check=CHECK, severity=Severity.ok, title=f"Model '{model}' is installed", detail="Ready to use.")
    same_family = sorted(n for n in installed if n.split(":")[0] == model.split(":")[0])
    detail = f"'{model}' is not installed."
    if same_family:
        detail += (f" Other tags of that model are installed: {', '.join(same_family)}. "
                   f"A name without a tag means ':latest'.")
    return Finding(check=CHECK, severity=Severity.critical, title=f"Model '{model}' is not installed", detail=detail,
                   fix=f"ollama pull {model}")


def _gpu_findings(data: dict) -> list[Finding]:
    running = [m for m in (data.get("models") or []) if isinstance(m, dict)]
    if not running:
        return [Finding(check=CHECK, severity=Severity.info, title="No model is loaded right now",
                        detail="Ollama loads a model into memory on its first request.")]
    findings = []
    for m in running:
        name = m.get("name") or m.get("model") or "unknown"
        size, vram = m.get("size"), m.get("size_vram")
        if not isinstance(size, (int, float)) or not isinstance(vram, (int, float)) or size <= 0:
            findings.append(Finding(check=CHECK, severity=Severity.info, title=f"Model '{name}' is loaded",
                                    detail="GPU usage is not reported."))
        elif vram <= 0:
            findings.append(Finding(
                check=CHECK, severity=Severity.warning, title=f"Model '{name}' is running on the CPU only",
                detail="No GPU memory is used, so generation will be much slower. Check GPU drivers, "
                       "that your GPU is supported, and that the model fits in VRAM.",
                fix="nvidia-smi"))
        elif vram < size * 0.95:
            findings.append(Finding(
                check=CHECK, severity=Severity.info, title=f"Model '{name}' is partly on the GPU",
                detail=f"{vram / size:.0%} of the model is in GPU memory ({_gb(vram)} of {_gb(size)}). "
                       "A smaller or more quantized model may run faster."))
        else:
            findings.append(Finding(check=CHECK, severity=Severity.ok, title=f"Model '{name}' is fully on the GPU",
                                    detail=f"Using {_gb(vram)} of GPU memory."))
    return findings


def run(model: str | None = None, host: str | None = None) -> list[Finding]:
    try:
        base_url = resolve_base_url(host)
    except ValueError:
        return [Finding(check=CHECK, severity=Severity.warning, title="OLLAMA_HOST is not valid",
                        detail="The OLLAMA_HOST environment variable has an invalid port or format.",
                        fix="Set OLLAMA_HOST to something like 127.0.0.1:11434")]
    if not _is_loopback(base_url):
        return [Finding(check=CHECK, severity=Severity.info, title="Ollama host is not local",
                        detail="OLLAMA_HOST points to another machine. For safety, only a local Ollama is checked.")]

    try:
        version = _get_json(base_url, "/api/version").get("version")
    except OllamaUnreachable:
        if _ollama_installed():
            return [Finding(check=CHECK, severity=Severity.warning, title="Ollama is installed but not running",
                            detail=f"Nothing is listening at {base_url}.",
                            fix="ollama serve  (or open the Ollama app)")]
        return [Finding(check=CHECK, severity=Severity.warning, title="Ollama is not running and was not found",
                        detail=f"Nothing is listening at {base_url}, and no ollama program was found on PATH.",
                        fix="Install Ollama from https://ollama.com/download")]
    except NotOllama as exc:
        return [Finding(check=CHECK, severity=Severity.warning, title="The service on this port is not Ollama",
                        detail=f"Something answered at {base_url} but not like Ollama ({exc}). "
                               "Another program may be using the port.", fix=None)]

    findings = [Finding(check=CHECK, severity=Severity.ok, title="Ollama is running",
                        detail=f"Version {version if isinstance(version, str) else 'unknown'} at {base_url}.")]

    installed: list[str] = []
    try:
        models = _model_names(_get_json(base_url, "/api/tags"))
        installed = [n for n, _ in models]
        if models:
            shown = ", ".join(installed[:MAX_MODELS_SHOWN])
            extra = f" (+{len(installed) - MAX_MODELS_SHOWN} more)" if len(installed) > MAX_MODELS_SHOWN else ""
            findings.append(Finding(check=CHECK, severity=Severity.ok, title=f"{len(models)} model(s) installed",
                                    detail=f"{shown}{extra}. Total size {_gb(sum(s for _, s in models))}."))
        else:
            findings.append(Finding(check=CHECK, severity=Severity.warning, title="No models are installed",
                                    detail="Ollama is running but has no models to serve.", fix="ollama pull <model>"))
    except (OllamaUnreachable, NotOllama) as exc:
        findings.append(Finding(check=CHECK, severity=Severity.error, title="Could not list installed models",
                                detail=str(exc)))

    if model:
        findings.append(_check_requested_model(model.strip(), installed))

    try:
        findings.extend(_gpu_findings(_get_json(base_url, "/api/ps")))
    except (OllamaUnreachable, NotOllama) as exc:
        findings.append(Finding(check=CHECK, severity=Severity.info, title="Could not read loaded models",
                                detail=str(exc)))
    return findings
