from mcp.server.mcpserver import MCPServer

mcp = MCPServer("ai-stack-diagnostics")


@mcp.tool()
def ping() -> str:
    """Check that the server is alive."""
    return "ai-stack-diagnostics is running"


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