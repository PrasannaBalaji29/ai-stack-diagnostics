import asyncio

import server


def test_all_tools_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"ping", "scan_project", "check_python_env", "check_env_and_keys", "check_ports", "check_ollama", "check_gpu", "check_imports", "check_compat", "diagnose"}


def test_ping():
    assert "running" in server.ping()


def test_bad_path_returns_error_finding_not_exception():
    for tool in (server.check_python_env, server.check_env_and_keys):
        out = tool("/definitely/not/here")
        assert out[0]["severity"] == "error" and "does not exist" in out[0]["detail"]
    assert "error" in server.scan_project("/definitely/not/here")


def test_check_gpu_bad_path_and_serialisable(project):
    import json
    out = server.check_gpu("/definitely/not/here")
    assert out[0]["severity"] == "error" and "does not exist" in out[0]["detail"]
    json.dumps(server.check_gpu(str(project)))


def test_crashing_check_is_contained(monkeypatch, project):
    from diagnostics.checks import python_env
    monkeypatch.setattr(python_env, "run", lambda p: 1 / 0)
    out = server.check_python_env(str(project))
    assert out[0]["severity"] == "error" and "ZeroDivisionError" in out[0]["detail"]


def test_scan_crash_is_contained(monkeypatch, project):
    monkeypatch.setattr(server, "scan", lambda p: 1 / 0)
    assert "error" in server.scan_project(str(project))


def test_results_are_json_serialisable(project):
    import json
    json.dumps(server.check_ports([65000]))
    json.dumps(server.scan_project(str(project)))
    json.dumps(server.check_env_and_keys(str(project)))


def test_new_tools_bad_path_and_serialisable(project):
    import json
    for tool in (server.check_imports, server.check_compat):
        out = tool("/definitely/not/here")
        assert out[0]["severity"] == "error" and "does not exist" in out[0]["detail"]
        json.dumps(tool(str(project)))


def test_server_tells_claude_it_runs_on_the_users_computer():
    assert "OWN computer" in server.INSTRUCTIONS and "diagnose" in server.INSTRUCTIONS
    assert "never returns secret values" in server.INSTRUCTIONS


def test_every_tool_has_a_useful_description():
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} >= {"diagnose", "scan_project", "check_gpu"}
    for tool in tools:
        assert tool.description and len(tool.description) >= 30, tool.name
    diag = next(t for t in tools if t.name == "diagnose")
    assert "own computer" in diag.description and "sandbox" in diag.description and "START HERE" in diag.description


def test_diagnose_prompt_is_registered_and_names_the_tool():
    import asyncio
    prompts = asyncio.run(server.mcp.list_prompts())
    assert [p.name for p in prompts] == ["diagnose_project"]  # exactly one, no stale duplicates
    result = asyncio.run(server.mcp.get_prompt("diagnose_project", {"path": r"C:\Users\x\proj"}))
    text = result.messages[0].content.text
    assert "diagnose" in text and r"C:\Users\x\proj" in text and "sandbox" in text
