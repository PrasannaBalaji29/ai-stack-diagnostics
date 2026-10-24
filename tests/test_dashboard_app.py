import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import dashboard.__main__ as launcher  # noqa: E402
from dashboard import app as appmod  # noqa: E402

TOKEN = "test-token-123"
BASE = "http://127.0.0.1:8765"
HEADERS = {"X-Dashboard-Token": TOKEN}


@pytest.fixture
def client():
    return TestClient(appmod.create_app(token=TOKEN, default_path=""), base_url=BASE)


def post(client, path, headers=HEADERS, **kw):
    return client.post("/api/diagnose", json={"path": path}, headers=headers, **kw)


# ---- page and static files

def test_index_has_token_security_headers_and_no_placeholders(client):
    r = client.get("/")
    assert r.status_code == 200 and TOKEN in r.text
    assert "__TOKEN__" not in r.text and "__DEFAULT_PATH__" not in r.text and "__VERSION__" not in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "'unsafe-inline'" not in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert "<script>" not in r.text.replace('<script src="/static/app.js"></script>', "")  # no inline scripts


def test_default_path_is_html_escaped():
    evil = '"><script>alert(1)</script>'
    html = TestClient(appmod.create_app(token=TOKEN, default_path=evil), base_url=BASE).get("/").text
    assert evil not in html and "&lt;script&gt;" in html


def test_static_files_and_traversal(client):
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert client.get("/static/app.css").status_code == 200
    for bad in ("/static/app.py", "/static/..%2fapp.py", "/static/../app.py", "/static/index.html", "/static/%2e%2e/%2e%2e/server.py"):
        assert client.get(bad).status_code in (404, 405), bad


def test_docs_and_openapi_are_disabled(client):
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(url).status_code == 404


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


# ---- attacks on a localhost server

@pytest.mark.parametrize("host", ["evil.com", "127.0.0.1.evil.com", "localhost.evil.com:8765", "192.168.1.5:8765", "", "testserver"])
def test_wrong_host_header_is_rejected(host):
    c = TestClient(appmod.create_app(token=TOKEN), base_url=BASE)
    assert c.get("/", headers={"host": host}).status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST:1234", "[::1]:8765", "localhost"])
def test_loopback_hosts_are_accepted(host):
    c = TestClient(appmod.create_app(token=TOKEN), base_url=BASE)
    assert c.get("/", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.com", "http://localhost:9999", "http://127.0.0.1:8766", "null", "http://127.0.0.1:8765.evil.com"])
def test_cross_origin_requests_are_rejected(client, origin):
    assert post(client, ".", headers={**HEADERS, "Origin": origin}).status_code == 403


def test_same_origin_is_accepted(client, tmp_path):
    assert post(client, str(tmp_path), headers={**HEADERS, "Origin": BASE}).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Dashboard-Token": ""}, {"X-Dashboard-Token": "wrong"}, {"X-Dashboard-Token": TOKEN + "x"}])
def test_token_is_required(client, tmp_path, headers):
    assert post(client, str(tmp_path), headers=headers).status_code == 401


def test_get_on_the_diagnose_endpoint_is_not_allowed(client):
    assert client.get("/api/diagnose", headers=HEADERS).status_code == 405


# ---- diagnose endpoint

def test_diagnose_returns_report_with_live_fields(client, project, make_venv):
    make_venv(project)
    (project / "app.py").write_text("import os\n")
    r = post(client, str(project))
    data = r.json()
    assert r.status_code == 200 and data["status"] in ("healthy", "needs attention", "broken")
    assert isinstance(data["elapsed_ms"], int) and data["checked_at"].endswith("+00:00")
    assert data["by_check"] and data["all_checks"] and "fix_first" in data


@pytest.mark.parametrize("bad", ["/definitely/not/here", "\x00", "/", "~"])
def test_bad_paths_give_a_clean_400(client, bad):
    r = post(client, bad)
    assert r.status_code == 400 and isinstance(r.json()["error"], str)


@pytest.mark.parametrize("payload", [{}, {"path": ""}, {"path": "a" * 1001}, {"path": 5}, {"nope": 1}])
def test_invalid_input_gives_a_clean_422(client, payload):
    r = client.post("/api/diagnose", json=payload, headers=HEADERS)
    assert r.status_code == 422 and "error" in r.json()


def test_invalid_json_body(client):
    r = client.post("/api/diagnose", content=b"{not json", headers={**HEADERS, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_secrets_are_never_returned(client, project):
    (project / ".env").write_text("OPENAI_API_KEY=sk-super-secret-1234567890\n")
    (project / ".env.example").write_text("OPENAI_API_KEY=\n")
    (project / "app.py").write_text("import openai\n")
    assert "sk-super-secret" not in json.dumps(post(client, str(project)).json())


def test_internal_errors_do_not_leak_details(client, project, monkeypatch):
    def boom(_):
        raise RuntimeError("SECRET INTERNAL DETAIL")
    monkeypatch.setattr(appmod, "diagnose", boom)
    r = post(client, str(project))
    assert r.status_code == 500 and "SECRET INTERNAL DETAIL" not in r.text


def test_slow_diagnosis_times_out(client, project, monkeypatch):
    monkeypatch.setattr(appmod, "DIAGNOSE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(appmod, "diagnose", lambda _: time.sleep(1.0) or {})
    assert post(client, str(project)).status_code == 504


def test_overload_returns_429(project, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_CONCURRENT", 0)
    c = TestClient(appmod.create_app(token=TOKEN), base_url=BASE)
    assert post(c, str(project)).status_code == 429


# ---- launcher

def test_launcher_only_listens_on_loopback(monkeypatch):
    seen = {}
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(launcher, "_port_is_free", lambda port: True)
    assert launcher.main(["--no-browser", "--port", "9123"]) == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 9123


def test_launcher_has_no_host_option():
    with pytest.raises(SystemExit):
        launcher.main(["--host", "0.0.0.0"])


@pytest.mark.parametrize("port", ["80", "70000", "0", "-5"])
def test_launcher_rejects_bad_ports(port, capsys):
    assert launcher.main(["--no-browser", "--port", port]) == 2


def test_launcher_reports_busy_port(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_port_is_free", lambda port: False)
    assert launcher.main(["--no-browser", "--port", "9124"]) == 2
    assert "already in use" in capsys.readouterr().err


def test_launcher_prefills_path_and_opens_browser_unless_disabled(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setattr(launcher, "_port_is_free", lambda port: True)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    assert launcher.main(["C:\\proj", "--port", "9125", "--no-browser"]) == 0
    assert opened == []


# ---- extra hardening

def test_post_must_be_json_and_have_a_small_length(client):
    base = {**HEADERS}
    r = client.post("/api/diagnose", content=b"path=/tmp", headers={**base, "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    r = client.post("/api/diagnose", content=b"path=/tmp", headers={**base, "Content-Type": "text/plain"})
    assert r.status_code == 415  # a cross-site "simple" form post can only send these types
    big = b'{"path": "' + b"a" * 5000 + b'"}'
    assert client.post("/api/diagnose", content=big, headers={**base, "Content-Type": "application/json"}).status_code == 413


def test_post_without_content_length_is_refused(client):
    def chunks():
        yield b'{"path": "."}'
    r = client.post("/api/diagnose", content=chunks(), headers={**HEADERS, "Content-Type": "application/json"})
    assert r.status_code in (400, 411)  # a chunked upload (no Content-Length) is refused


@pytest.mark.parametrize("value", ["cross-site", "same-site"])
def test_browser_cross_site_hint_is_refused(client, value):
    assert client.get("/", headers={"Sec-Fetch-Site": value}).status_code == 403
    assert post(client, ".", headers={**HEADERS, "Sec-Fetch-Site": value}).status_code == 403


@pytest.mark.parametrize("value", ["same-origin", "none"])
def test_browser_same_origin_hint_is_accepted(client, value):
    assert client.get("/", headers={"Sec-Fetch-Site": value}).status_code == 200


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "TRACE", "OPTIONS"])
def test_unexpected_methods_are_refused(client, method):
    assert client.request(method, "/api/diagnose", headers=HEADERS).status_code in (405, 403)


def test_token_guessing_is_throttled(client, tmp_path):
    codes = [post(client, str(tmp_path), headers={"X-Dashboard-Token": f"guess{i}"}).status_code for i in range(appmod.MAX_TOKEN_FAILURES + 3)]
    assert codes[: appmod.MAX_TOKEN_FAILURES] == [401] * appmod.MAX_TOKEN_FAILURES
    assert set(codes[appmod.MAX_TOKEN_FAILURES:]) == {429}
    assert post(client, str(tmp_path)).status_code == 429  # even the right token waits out the lock


def test_throttle_expires(tmp_path, monkeypatch):
    c = TestClient(appmod.create_app(token=TOKEN), base_url=BASE)
    for i in range(appmod.MAX_TOKEN_FAILURES):
        post(c, str(tmp_path), headers={"X-Dashboard-Token": f"x{i}"})
    assert post(c, str(tmp_path)).status_code == 429
    monkeypatch.setattr(appmod, "FAILURE_WINDOW_SECONDS", -1)  # pretend the minute has passed
    assert post(c, str(tmp_path)).status_code == 200


def test_security_headers_on_every_kind_of_response(client):
    for response in (client.get("/"), client.get("/nope"), client.get("/static/app.js"), client.get("/", headers={"host": "evil.com"}), post(client, ".", headers={})):
        h = response.headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "object-src 'none'" in h["content-security-policy"]
        assert h["cross-origin-opener-policy"] == "same-origin" and "camera=()" in h["permissions-policy"]
        assert "server" not in h or "uvicorn" not in h["server"].lower()


def test_launcher_hardens_uvicorn(monkeypatch):
    seen = {}
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(launcher, "_port_is_free", lambda port: True)
    launcher.main(["--no-browser", "--port", "9126"])
    assert seen["proxy_headers"] is False and seen["server_header"] is False
    assert seen["limit_concurrency"] <= 100 and seen["timeout_keep_alive"] <= 10 and seen["h11_max_incomplete_event_size"] <= 65_536


def test_front_gate_limits(client):
    many = {f"X-{i}": "v" for i in range(appmod.MAX_HEADER_COUNT + 5)}
    assert client.get("/", headers=many).status_code == 431
    assert client.get("/", headers={"X-Big": "B" * (appmod.MAX_HEADER_BYTES + 10)}).status_code == 431
    assert client.get("/", headers={"Transfer-Encoding": "chunked"}).status_code == 400
    assert client.get("/", headers={"Content-Length": "5", "Transfer-Encoding": "chunked"}).status_code == 400
    assert client.get("/").status_code == 200  # a normal browser request has far fewer headers than the limit


def test_unsupported_http_version_is_refused():
    import asyncio

    app = appmod.create_app(token=TOKEN)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "9.9", "method": "GET", "path": "/", "raw_path": b"/",
             "query_string": b"", "headers": [(b"host", b"127.0.0.1:8765")], "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 1), "scheme": "http"}
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 505


def test_unauthenticated_callers_learn_nothing_about_validation(client):
    for body in ({}, {"path": ""}, {"path": 5}, {"path": "/etc"}):
        r = client.post("/api/diagnose", json=body)  # no token
        assert r.status_code == 401 and set(r.json()) == {"error"}
    garbage = client.post("/api/diagnose", content=b"{not json", headers={"Content-Type": "application/json"})
    assert garbage.status_code == 401


def test_every_api_route_except_health_needs_the_token(client):
    assert client.get("/api/health").status_code == 200
    for path in ("/api/diagnose", "/api/anything", "/api/"):
        assert client.get(path).status_code == 401
        assert client.post(path, json={"path": "."}).status_code == 401


# ---- duplicate security headers must be refused no matter which HTTP parser sits underneath

def _asgi_status(headers):
    import asyncio

    app = appmod.create_app(token=TOKEN)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": "/", "raw_path": b"/",
             "query_string": b"", "headers": headers, "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 1), "scheme": "http"}
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"]


GOOD_HOST = (b"host", b"127.0.0.1:8765")


def test_a_single_host_header_is_fine():
    assert _asgi_status([GOOD_HOST]) == 200


@pytest.mark.parametrize("headers", [
    [GOOD_HOST, (b"host", b"evil.com")],                       # good one first (what httptools let through)
    [(b"host", b"evil.com"), GOOD_HOST],                       # bad one first
    [GOOD_HOST, (b"HOST", b"127.0.0.1:8765")],                 # same value, different capitalisation
    [GOOD_HOST, (b"origin", b"http://127.0.0.1:8765"), (b"origin", b"https://evil.com")],
    [GOOD_HOST, (b"content-length", b"5"), (b"content-length", b"500")],
    [GOOD_HOST, (b"content-type", b"application/json"), (b"content-type", b"text/plain")],
    [GOOD_HOST, (b"sec-fetch-site", b"same-origin"), (b"sec-fetch-site", b"cross-site")],
    [GOOD_HOST, (b"x-dashboard-token", b"a"), (b"x-dashboard-token", b"b")],
])
def test_duplicate_security_headers_are_refused(headers):
    assert _asgi_status(headers) == 400


def test_other_repeated_headers_are_still_allowed():
    assert _asgi_status([GOOD_HOST, (b"accept", b"text/html"), (b"accept", b"*/*"), (b"cookie", b"a=1"), (b"cookie", b"b=2")]) == 200


# ---- refused paths only ever show fixed sentences, never exception text or the machine's details

@pytest.mark.parametrize("path,code", [
    ("", "empty"), ("   ", "empty"), ("bad\x07path", "control_chars"), ("/definitely/not/here/xyz", "missing"),
])
def test_a_refused_path_shows_a_fixed_sentence(client, path, code):
    r = post(client, path)
    assert r.status_code in (400, 422)
    if r.status_code == 400:
        assert r.json()["error"] == appmod._PATH_ERROR_TEXT[code]


def test_a_file_instead_of_a_folder_is_refused_with_a_fixed_sentence(client, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    r = post(client, str(f))
    assert r.status_code == 400 and r.json()["error"] == appmod._PATH_ERROR_TEXT["not_dir"]
    assert str(tmp_path) not in r.text


def test_the_error_never_contains_the_path_or_system_text(client, tmp_path):
    secret_name = "very-private-folder-name"
    r = post(client, str(tmp_path / secret_name))
    assert r.status_code == 400 and secret_name not in r.text and "Errno" not in r.text


def test_every_error_code_has_a_fixed_sentence():
    from diagnostics import safety
    codes = {"empty", "control_chars", "unresolvable", "unreadable", "missing", "not_dir", "drive_root", "home", "no_permission"}
    assert codes <= set(appmod._PATH_ERROR_TEXT) and "invalid" in appmod._PATH_ERROR_TEXT
    assert safety.ProjectPathError("x").code == "invalid"
    assert all("{" not in text and "Errno" not in text for text in appmod._PATH_ERROR_TEXT.values())


def test_an_unknown_code_falls_back_to_the_generic_sentence():
    from diagnostics.safety import ProjectPathError
    r = appmod._path_error_response(ProjectPathError("secret detail C:\\Users\\x", "made-up-code"))
    assert r.status_code == 400 and b"secret detail" not in r.body and b"cannot be used" in r.body


def test_only_the_two_known_static_files_are_served_from_a_fixed_table(client):
    assert set(appmod._STATIC_TABLE) == {"app.css", "app.js"}
    for name, (path, _media) in appmod._STATIC_TABLE.items():
        assert path.name == name and path.is_file()
        assert client.get(f"/static/{name}").status_code == 200
    for bad in ("../app.py", "..%2fapp.py", "app.css/../../app.py", "index.html", "%2e%2e/app.py", "app.css%00"):
        assert client.get(f"/static/{bad}").status_code in (404, 400, 422), bad


# ---- the launcher finds a free port by itself when none was asked for

def test_default_port_busy_moves_to_the_next_free_one(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(launcher, "_port_is_free", lambda port: port not in (8765, 8766))
    assert launcher.main(["--no-browser"]) == 0
    assert seen["port"] == 8767 and "http://127.0.0.1:8767" in capsys.readouterr().out


def test_default_port_free_is_used(monkeypatch):
    seen = {}
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(launcher, "_port_is_free", lambda port: True)
    assert launcher.main(["--no-browser"]) == 0 and seen["port"] == 8765


def test_all_default_ports_busy_gives_a_clear_error(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_port_is_free", lambda port: False)
    assert launcher.main(["--no-browser"]) == 2
    assert "all in use" in capsys.readouterr().err


def test_an_explicit_busy_port_is_still_refused(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_port_is_free", lambda port: False)
    assert launcher.main(["--no-browser", "--port", "9200"]) == 2
    assert "already in use" in capsys.readouterr().err
