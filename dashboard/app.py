"""FastAPI app for the local dashboard.

Security model (a localhost web server can be attacked by *other websites* open in your browser):
  * it only answers requests whose Host header is localhost / 127.0.0.1 (blocks DNS rebinding);
  * API calls need a random per-launch token that only the served page knows (blocks other sites);
  * cross-origin requests are refused;
  * strict Content-Security-Policy: the page runs no inline scripts and loads nothing from the internet;
  * it is read-only: it only calls the same diagnose() code as the MCP server.
"""

from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import platform
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from dashboard import __version__
from dashboard.folder_dialog import choose_folder
from diagnostics.diagnose import diagnose, diagnose_events
from diagnostics.safety import ProjectPathError, resolve_project_path

logger = logging.getLogger("ai-stack-diagnostics.dashboard")

STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})
TOKEN_HEADER = "x-dashboard-token"  # noqa: S105 - a header name, not a password
MAX_PATH_CHARS = 1000
DIAGNOSE_TIMEOUT_SECONDS = 90
MAX_CONCURRENT = 2
MAX_BODY_BYTES = 4096
MAX_HEADER_COUNT = 64
MAX_HEADER_BYTES = 16_384
ALLOWED_HTTP_VERSIONS = frozenset({"1.0", "1.1"})
# Headers our security decisions depend on. Sending one of these twice is refused outright, because different
# HTTP parsers disagree about which copy "wins" (h11 rejects duplicates, httptools passes them through).
SINGLE_VALUE_HEADERS = frozenset({
    b"host", b"origin", b"content-length", b"content-type", b"sec-fetch-site", b"x-dashboard-token", b"transfer-encoding",
})
MAX_TOKEN_FAILURES = 10
FAILURE_WINDOW_SECONDS = 60

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
}
_STATIC_FILES = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}


class DiagnoseRequest(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)


def _hostname(host_header: str) -> str:
    """'localhost:8765' -> 'localhost', '[::1]:8765' -> '[::1]'."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.split(":")[0]


def _reject(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers=SECURITY_HEADERS)


def create_app(token: str | None = None, default_path: str = "") -> FastAPI:
    secret = token or secrets.token_urlsafe(32)
    app = FastAPI(title="AI Stack Diagnostics", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    gate = asyncio.Semaphore(MAX_CONCURRENT)
    failures: deque[float] = deque()

    def locked_out() -> bool:
        now = time.monotonic()
        while failures and now - failures[0] > FAILURE_WINDOW_SECONDS:
            failures.popleft()
        return len(failures) >= MAX_TOKEN_FAILURES
    index_template = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        scope = request.scope
        if str(scope.get("http_version", "1.1")) not in ALLOWED_HTTP_VERSIONS:
            return _reject(505, "HTTP version not supported.")
        raw_headers = scope.get("headers", [])
        if len(raw_headers) > MAX_HEADER_COUNT or sum(len(k) + len(v) for k, v in raw_headers) > MAX_HEADER_BYTES:
            return _reject(431, "Request headers are too large.")
        seen: set[bytes] = set()
        for name, _value in raw_headers:
            lowered = name.lower()
            if lowered in SINGLE_VALUE_HEADERS:
                if lowered in seen:
                    return _reject(400, "A header that must appear once was sent more than once.")
                seen.add(lowered)
        if "transfer-encoding" in request.headers:  # never needed here; also blocks request-smuggling tricks
            return _reject(400, "Transfer-Encoding is not supported.")
        host = request.headers.get("host", "")
        if _hostname(host) not in ALLOWED_HOSTNAMES:
            return _reject(400, "Unexpected Host header. This dashboard only answers on localhost.")
        origin = request.headers.get("origin")
        if origin is not None and (origin == "null" or urlsplit(origin).netloc.lower() != host.strip().lower()):
            return _reject(403, "Cross-origin requests are not allowed.")
        if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return _reject(403, "Cross-site requests are not allowed.")
        if request.method not in ("GET", "HEAD", "POST"):
            return _reject(405, "Method not allowed.")
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            # authenticate FIRST: an unauthenticated caller learns nothing about how requests are validated
            if locked_out():
                return _reject(429, "Too many wrong tokens. Wait a minute, then reload the page.")
            supplied = request.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(supplied.encode(), secret.encode()):
                failures.append(time.monotonic())
                return _reject(401, "Missing or wrong dashboard token. Reload the page.")
        if request.method == "POST":
            if not request.headers.get("content-type", "").lower().startswith("application/json"):
                return _reject(415, "Send JSON (Content-Type: application/json).")
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return _reject(411, "A Content-Length header is required.")
            if int(length) > MAX_BODY_BYTES:
                return _reject(413, "The request is too large.")
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def bad_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _reject(422, f"Enter a project folder path (1 to {MAX_PATH_CHARS} characters).")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = index_template.replace("__TOKEN__", html.escape(secret, quote=True))
        page = page.replace("__DEFAULT_PATH__", html.escape(default_path, quote=True))
        page = page.replace("__VERSION__", html.escape(__version__, quote=True))
        return HTMLResponse(page)

    @app.get("/static/{name}")
    async def static_file(name: str) -> Response:
        media = _STATIC_FILES.get(name)  # only two known files: no path tricks possible
        if media is None:
            return _reject(404, "Not found.")
        return FileResponse(STATIC_DIR / name, media_type=media)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.post("/api/diagnose")
    async def api_diagnose(body: DiagnoseRequest) -> JSONResponse:
        try:
            project = resolve_project_path(body.path)
        except ProjectPathError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if gate.locked():
            return _reject(429, "Two diagnoses are already running. Try again in a moment.")
        started = time.perf_counter()
        async with gate:
            try:
                result = await asyncio.wait_for(run_in_threadpool(diagnose, project), DIAGNOSE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                return _reject(504, "The diagnosis took too long and was stopped.")
            except Exception:  # noqa: BLE001
                logger.exception("dashboard diagnose crashed")
                return _reject(500, "Diagnosis failed unexpectedly. See the terminal for details.")
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        result["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return JSONResponse(result, headers=SECURITY_HEADERS)

    browse_lock = asyncio.Lock()

    @app.post("/api/diagnose/stream")
    async def api_diagnose_stream(body: DiagnoseRequest) -> Response:
        """Same diagnosis, but each step is sent as one JSON line the moment it happens."""
        try:
            project = resolve_project_path(body.path)
        except ProjectPathError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if gate.locked():
            return _reject(429, "Two diagnoses are already running. Try again in a moment.")
        await gate.acquire()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        def worker() -> None:
            try:
                for event in diagnose_events(project):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception:  # noqa: BLE001
                logger.exception("dashboard stream crashed")
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "error": "Diagnosis failed unexpectedly. See the terminal for details."})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, worker)

        def line(event: dict) -> bytes:
            return (json.dumps(event, separators=(",", ":")) + "\n").encode()

        async def stream():  # type: ignore[no-untyped-def]
            started = time.perf_counter()
            deadline = time.monotonic() + DIAGNOSE_TIMEOUT_SECONDS
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    try:
                        event = await asyncio.wait_for(queue.get(), max(remaining, 0.001))
                    except asyncio.TimeoutError:
                        yield line({"type": "error", "error": "The diagnosis took too long and was stopped."})
                        return
                    if event is None:
                        return
                    if event.get("type") == "result":
                        event["result"]["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                        event["result"]["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    yield line(event)
            finally:
                gate.release()

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers=SECURITY_HEADERS)

    @app.get("/api/system")
    async def system_info() -> dict:
        def version(name: str) -> str:
            try:
                return metadata.version(name)
            except metadata.PackageNotFoundError:
                return "not installed"

        return {
            "dashboard": __version__,
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "os": f"{platform.system()} {platform.release()}".strip(),
            "machine": platform.machine(),
            "listening": "127.0.0.1 (this computer only)",
            "packages": {name: version(name) for name in ("fastapi", "uvicorn", "starlette", "mcp", "pydantic")},
        }

    @app.post("/api/browse")
    async def api_browse() -> JSONResponse:
        if browse_lock.locked():
            return _reject(429, "A folder window is already open.")
        async with browse_lock:
            result = await run_in_threadpool(choose_folder)
        return JSONResponse(result, headers=SECURITY_HEADERS)

    return app
