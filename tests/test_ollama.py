import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from diagnostics.checks import ollama
from tests.conftest import by_severity, titles

GB = 1_000_000_000
TAGS = {"models": [{"name": "llama3.2:latest", "size": 2 * GB}, {"name": "qwen2.5:7b", "size": 5 * GB}]}
GPU_FULL = {"models": [{"name": "llama3.2:latest", "size": 2 * GB, "size_vram": 2 * GB}]}


@pytest.fixture
def fake_ollama():
    servers = []

    def start(routes):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                route = routes.get(self.path)
                if route is None:
                    self.send_response(404); self.end_headers(); return
                if callable(route):
                    route = route()
                status, body, *headers = route
                if isinstance(body, (dict, list)):
                    body = json.dumps(body)
                self.send_response(status)
                for k, v in (headers[0] if headers else {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body.encode() if isinstance(body, str) else body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def healthy(**overrides):
    routes = {"/api/version": (200, {"version": "0.9.9"}), "/api/tags": (200, TAGS), "/api/ps": (200, GPU_FULL)}
    routes.update(overrides)
    return routes


def test_healthy(fake_ollama):
    f = ollama.run(host=fake_ollama(healthy()))
    assert not by_severity(f, "warning") and not by_severity(f, "critical")
    assert "Ollama is running" in titles(f)
    assert any("2 model(s)" in t for t in titles(f))
    assert any("fully on the GPU" in t for t in titles(f))


def test_no_models(fake_ollama):
    f = ollama.run(host=fake_ollama(healthy(**{"/api/tags": (200, {"models": []})})))
    assert "No models are installed" in titles(f)


def test_models_null_and_garbage_entries(fake_ollama):
    f = ollama.run(host=fake_ollama(healthy(**{"/api/tags": (200, {"models": [None, 5, {"x": 1}, {"name": "a:1", "size": "big"}]})})))
    assert any("1 model(s)" in t for t in titles(f))


@pytest.mark.parametrize("requested,ok", [("llama3.2", True), ("llama3.2:latest", True), ("qwen2.5:7b", True),
                                          ("qwen2.5", False), ("mistral", False)])
def test_requested_model(fake_ollama, requested, ok):
    f = ollama.run(model=requested, host=fake_ollama(healthy()))
    assert bool(by_severity(f, "critical")) is (not ok)


def test_wrong_tag_explains_latest(fake_ollama):
    f = ollama.run(model="qwen2.5", host=fake_ollama(healthy()))
    crit = by_severity(f, "critical")[0]
    assert "qwen2.5:7b" in crit.detail and crit.fix == "ollama pull qwen2.5"


@pytest.mark.parametrize("bad", ["llama; rm -rf /", "a b", "$(whoami)", "x\ny", "`id`", "../../etc"])
def test_malicious_model_name_never_reaches_fix_command(fake_ollama, bad):
    f = ollama.run(model=bad, host=fake_ollama(healthy()))
    assert "Invalid model name" in titles(f)
    assert all(x.fix is None or bad not in x.fix for x in f)


def test_cpu_only_warning(fake_ollama):
    cpu = {"models": [{"name": "big:70b", "size": 40 * GB, "size_vram": 0}]}
    f = ollama.run(host=fake_ollama(healthy(**{"/api/ps": (200, cpu)})))
    assert any("CPU only" in x.title for x in by_severity(f, "warning"))


def test_partial_offload_info(fake_ollama):
    part = {"models": [{"name": "m", "size": 10 * GB, "size_vram": 6 * GB}]}
    f = ollama.run(host=fake_ollama(healthy(**{"/api/ps": (200, part)})))
    assert any("partly on the GPU" in x.title and "60%" in x.detail for x in f)


def test_missing_size_vram_is_not_a_false_alarm(fake_ollama):
    f = ollama.run(host=fake_ollama(healthy(**{"/api/ps": (200, {"models": [{"name": "m", "size": 5}]})})))
    assert not by_severity(f, "warning")


def test_nothing_loaded(fake_ollama):
    f = ollama.run(host=fake_ollama(healthy(**{"/api/ps": (200, {"models": []})})))
    assert "No model is loaded right now" in titles(f)


def _closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); return port


def test_not_running_but_installed(monkeypatch):
    monkeypatch.setattr(ollama.shutil, "which", lambda n: "/usr/bin/ollama")
    f = ollama.run(host=f"127.0.0.1:{_closed_port()}")
    assert titles(f) == ["Ollama is installed but not running"] and "ollama serve" in f[0].fix


def test_not_running_not_installed(monkeypatch):
    monkeypatch.setattr(ollama.shutil, "which", lambda n: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    f = ollama.run(host=f"127.0.0.1:{_closed_port()}")
    assert titles(f) == ["Ollama is not running and was not found"] and "ollama.com/download" in f[0].fix


@pytest.mark.parametrize("routes", [
    {"/api/version": (200, "<html>hello</html>")},
    {"/api/version": (200, "[1,2,3]")},
    {"/api/version": (404, "nope")},
    {"/api/version": (500, "boom")},
    {"/api/version": (200, b"\xff\xfe\x00bin")},
])
def test_not_ollama(fake_ollama, routes):
    f = ollama.run(host=fake_ollama(routes))
    assert titles(f) == ["The service on this port is not Ollama"]


def test_redirect_is_not_followed(fake_ollama):
    f = ollama.run(host=fake_ollama({"/api/version": (302, "", {"Location": "http://example.com/"})}))
    assert titles(f) == ["The service on this port is not Ollama"]


def test_timeout_is_reported_not_hung(fake_ollama, monkeypatch):
    monkeypatch.setattr(ollama, "TIMEOUT", 0.3)
    monkeypatch.setattr(ollama.shutil, "which", lambda n: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    start = time.time()
    f = ollama.run(host=fake_ollama({"/api/version": lambda: (time.sleep(1.5), (200, {"version": "1"}))[1]}))
    assert time.time() - start < 1.2 and "not running" in f[0].title


def test_tags_failure_is_contained(fake_ollama):
    f = ollama.run(host=fake_ollama(healthy(**{"/api/tags": (500, "x")})))
    assert "Could not list installed models" in titles(f) and "Ollama is running" in titles(f)


def test_ps_failure_is_contained(fake_ollama):
    f = ollama.run(host=fake_ollama(healthy(**{"/api/ps": (200, "not json")})))
    assert "Could not read loaded models" in titles(f)


def test_remote_host_is_never_contacted(monkeypatch):
    monkeypatch.setattr(ollama._OPENER, "open", lambda *a, **k: pytest.fail("made a network request"))
    for host in ("http://10.1.2.3:11434", "example.com", "http://169.254.169.254:80"):
        assert titles(ollama.run(host=host)) == ["Ollama host is not local"]


def test_proxy_env_is_ignored(fake_ollama, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    assert "Ollama is running" in titles(ollama.run(host=fake_ollama(healthy())))


@pytest.mark.parametrize("raw,expected", [
    (None, "http://127.0.0.1:11434"), ("", "http://127.0.0.1:11434"), ("localhost", "http://localhost:11434"),
    ("127.0.0.1:5000", "http://127.0.0.1:5000"), (":5000", "http://127.0.0.1:5000"),
    ("http://localhost:5000", "http://localhost:5000"), ("0.0.0.0", "http://127.0.0.1:11434"),
    ("0.0.0.0:8080", "http://127.0.0.1:8080"), ("[::1]:5000", "http://[::1]:5000"), ("::", "http://[::1]:11434"),
    ("::1", "http://[::1]:11434"), ("HTTPS://LocalHost:443", "https://localhost:443"), ("ftp://localhost", "http://localhost:11434"),
])
def test_resolve_base_url(monkeypatch, raw, expected):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ollama.resolve_base_url(raw) == expected


def test_env_var_is_used(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:7777")
    assert ollama.resolve_base_url() == "http://127.0.0.1:7777"


def test_invalid_host_value():
    assert titles(ollama.run(host="127.0.0.1:notaport")) == ["OLLAMA_HOST is not valid"]


def test_tool_in_server(fake_ollama, monkeypatch):
    import server
    monkeypatch.setenv("OLLAMA_HOST", fake_ollama(healthy()))
    out = server.check_ollama("llama3.2")
    assert out[0]["title"] == "Ollama is running" and not [x for x in out if x["severity"] == "critical"]
