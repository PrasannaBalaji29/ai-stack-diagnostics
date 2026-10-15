from diagnostics.checks import python_env
from diagnostics.safety import resolve_project_path
from mcp.server.mcpserver import MCPServer
from diagnostics.checks import env_keys

mcp = MCPServer("ai-stack-diagnostics")


@mcp.tool()
def ping() -> str:
    """Check that the server is alive."""
    return "ai-stack-diagnostics is running"


@mcp.tool()
def check_python_env(path: str) -> list[dict]:
    """Check a project's virtual environment and requirements.txt."""
    findings = python_env.run(resolve_project_path(path))
    return [f.model_dump(mode="json") for f in findings]


@mcp.tool()
def check_env_and_keys(path: str) -> list[dict]:
    """Check .env against .env.example. Never returns secret values."""
    findings = env_keys.run(resolve_project_path(path))
    return [f.model_dump(mode="json") for f in findings]


@mcp.resource("info://about")
def about() -> str:
    """Basic info about this server."""
    return "Read-only diagnostics for AI/ML project setups."


@mcp.prompt()
def diagnose_prompt(path: str) -> str:
    """Template that asks Claude to diagnose a project."""
    return f"Diagnose the AI project at {path} and list problems by priority."


if __name__ == "__main__":
    mcp.run()