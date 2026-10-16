"""ai-stack-diagnostics: a read-only MCP server that diagnoses AI/ML project setups."""

import logging
import sys

from diagnostics.checks import env_keys, ports, python_env
from diagnostics.registry import run_check
from diagnostics.safety import ProjectPathError, resolve_project_path
from diagnostics.scan import scan
from mcp.server.mcpserver import MCPServer

# stdout is reserved for the MCP protocol, so all logs go to stderr.
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ai-stack-diagnostics")

mcp = MCPServer("ai-stack-diagnostics")


@mcp.tool()
def ping() -> str:
    """Check that the server is alive."""
    return "ai-stack-diagnostics is running"


@mcp.tool()
def scan_project(path: str) -> dict:
    """Detect a project's AI stack (read-only) and suggest which checks to run."""
    try:
        return scan(resolve_project_path(path))
    except ProjectPathError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("scan_project crashed")
        return {"error": f"Scan failed unexpectedly: {type(exc).__name__}. See the server log."}


@mcp.tool()
def check_python_env(path: str) -> list[dict]:
    """Check a project's virtual environment against requirements.txt / pyproject.toml."""
    return run_check("python_env", lambda: python_env.run(resolve_project_path(path)))


@mcp.tool()
def check_env_and_keys(path: str) -> list[dict]:
    """Compare .env with .env.example. Reports key names only, never secret values."""
    return run_check("env_keys", lambda: env_keys.run(resolve_project_path(path)))


@mcp.tool()
def check_ports(ports_to_check: list[int] | None = None) -> list[dict]:
    """Check whether ports are free, and list common AI service ports in use."""
    return run_check("ports", lambda: ports.run(ports_to_check))


@mcp.resource("info://about")
def about() -> str:
    """Basic info about this server."""
    return "Read-only diagnostics for AI/ML project setups. It never modifies files or runs fixes."


@mcp.prompt()
def diagnose_prompt(path: str) -> str:
    """Template that asks Claude to diagnose a project."""
    return f"Diagnose the AI project at {path}. Start with scan_project, then run the suggested checks and list problems by priority."


if __name__ == "__main__":
    mcp.run()
