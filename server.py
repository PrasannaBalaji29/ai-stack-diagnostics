"""ai-stack-diagnostics: a read-only MCP server that diagnoses AI/ML project setups."""

import logging
import sys

from diagnostics.checks import compat, env_keys, explain_error as explain_error_check, gpu, imports, ollama, ports, python_env
from diagnostics.diagnose import diagnose as run_diagnose
from diagnostics.registry import run_check
from diagnostics.safety import ProjectPathError, resolve_project_path
from diagnostics.scan import scan
from mcp.server.mcpserver import MCPServer

# stdout is reserved for the MCP protocol, so all logs go to stderr.
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ai-stack-diagnostics")

INSTRUCTIONS = (
    "This server runs on the user's OWN computer and can read their local project folders directly "
    "(for example C:\\Users\\name\\my-project). When the user asks to diagnose, debug, health-check or "
    "troubleshoot an AI/ML project on their machine, or gives a local folder path, call the `diagnose` tool "
    "with that path instead of using a sandbox, shell or file upload. It is read-only: it never changes files, "
    "never installs anything, and never returns secret values. Use the individual check_* tools only when the "
    "user asks about one specific area. When the user pastes an error message or traceback and asks what it means "
    "or how to fix it, call `explain_error` with the text (and the project path if they gave one)."
)

mcp = MCPServer(
    "ai-stack-diagnostics",
    title="AI Stack Diagnostics",
    description="Read-only diagnosis of AI/ML project setups on the user's own computer.",
    instructions=INSTRUCTIONS,
)


@mcp.tool()
def ping() -> str:
    """Check that the server is alive."""
    return "ai-stack-diagnostics is running"


@mcp.tool()
def scan_project(path: str) -> dict:
    """Detect the AI stack of a project folder on the user's own computer (read-only) and list the libraries it imports. Prefer `diagnose` unless only the stack overview is wanted."""
    try:
        return scan(resolve_project_path(path))
    except ProjectPathError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("scan_project crashed")
        return {"error": f"Scan failed unexpectedly: {type(exc).__name__}. See the server log."}


@mcp.tool()
def diagnose(path: str) -> dict:
    """START HERE to diagnose, debug or health-check an AI/ML project folder on the user's own computer (a local path such as C:\\Users\\name\\project). Runs on the user's machine and reads the folder directly, so use it instead of any sandbox. Read-only. Scans the project, runs only the checks it needs, and returns one prioritized report: status, score, and a numbered list of what to fix first."""
    try:
        return run_diagnose(resolve_project_path(path))
    except ProjectPathError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("diagnose crashed")
        return {"error": f"Diagnosis failed unexpectedly: {type(exc).__name__}. See the server log."}


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


@mcp.tool()
def check_ollama(model: str | None = None) -> list[dict]:
    """Check the local Ollama server: running, version, installed models, and GPU use. Optionally verify one model name."""
    return run_check("ollama", lambda: ollama.run(model))


@mcp.tool()
def check_gpu(path: str) -> list[dict]:
    """Check the GPU setup on this machine: whether the project's PyTorch, TensorFlow, JAX, ONNX Runtime and CuPy builds match the NVIDIA driver and CUDA version, mixed CUDA libraries, and how full the GPU memory is."""
    return run_check("gpu", lambda: gpu.run(resolve_project_path(path)))


@mcp.tool()
def check_imports(path: str) -> list[dict]:
    """Compare the libraries the code imports with what is installed and listed in requirements (finds missing libraries and removed standard-library modules)."""
    return run_check("imports", lambda: imports.run(resolve_project_path(path)))


@mcp.tool()
def check_compat(path: str) -> list[dict]:
    """Check the venv's Python version against the project, and installed packages for version conflicts or missing dependencies."""
    return run_check("compat", lambda: compat.run(resolve_project_path(path)))


@mcp.tool()
def explain_error(error_text: str, path: str = "") -> list[dict]:
    """Explain a pasted error message or Python traceback in plain words: the likely cause and the exact fix. Knows about 45 common AI/ML project errors (missing library, wrong Python, CUDA out of memory, API key or model name rejected, rate limits, Ollama, ChromaDB, MCP, pip and Windows problems). Optionally pass the project folder path so the answer can check what that project actually has installed. Read-only; never repeats secret keys."""
    return run_check("explain_error", lambda: explain_error_check.run(error_text, resolve_project_path(path) if path.strip() else None))


@mcp.prompt(
    name="diagnose_project",
    title="Diagnose my project",
    description="Run a full read-only diagnosis of an AI/ML project folder on this computer.",
)
def diagnose_project(path: str) -> str:
    return (
        f"Use the `diagnose` tool from the ai-stack-diagnostics server on this local folder: {path}\n\n"
        "Do not use a sandbox or ask me to upload anything; the tool reads the folder on my computer directly. "
        "Then explain the result in plain words: the status and score, what to fix first and why, and the exact "
        "commands to run. If nothing is wrong, say so briefly. Do not run any fix command yourself."
    )


@mcp.resource("info://about")
def about() -> str:
    """Basic info about this server."""
    return "Read-only diagnostics for AI/ML project setups. It never modifies files or runs fixes."


if __name__ == "__main__":
    mcp.run()
