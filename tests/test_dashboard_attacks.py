"""Attacks the REAL dashboard process over raw sockets: malformed HTTP, smuggling, floods, traversal."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def raw(port: int, payload: bytes, wait: float = 2.0, limit: int = 200_000) -> bytes:
    """Send bytes, return whatever the server answers (b'' if it just closes the connection)."""
    out = b""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=wait) as s:
            s.settimeout(wait)
            try:
                s.sendall(payload)
            except OSError:
                pass  # the server may hang up before we finish sending a huge request
            try:
                while len(out) < limit:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    out += chunk
            except (TimeoutError, OSError):
                pass
    except OSError:
        pass
    return out


def status(response: bytes) -> int:
    try:
        return int(response.split(b" ", 2)[1])
    except (IndexError, ValueError):
        return 0  # connection closed without a reply


@pytest.fixture(scope="module")
def server():
    port = free_port()
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "dashboard", "--no-browser", "--port", str(port)],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if status(raw(port, b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")) == 200:
                break
            time.sleep(0.1)
        else:
            pytest.fail("dashboard did not start")
        yield port
    finally:
        proc.kill()
        proc.wait(timeout=10)


def get(port, path="/", extra=b""):
    return raw(port, b"GET " + path.encode() + b" HTTP/1.1\r\nHost: 127.0.0.1:" + str(port).encode() + b"\r\nConnection: close\r\n" + extra + b"\r\n")


def assert_alive(port):
    assert status(get(port, "/api/health")) == 200, "the server stopped answering after the attack"


def test_baseline_page_and_headers(server):
    r = get(server)
    assert status(r) == 200 and b"content-security-policy" in r.lower() and b"uvicorn" not in r.lower().split(b"\r\n\r\n")[0]


def test_giant_request_line_is_refused(server):
    assert status(get(server, "/" + "A" * 200_000)) != 200
    assert_alive(server)


def test_giant_header_is_refused(server):
    assert status(get(server, "/", b"X-Big: " + b"B" * 300_000 + b"\r\n")) != 200
    assert_alive(server)


def test_thousands_of_headers_are_refused(server):
    many = b"".join(b"X-%d: v\r\n" % i for i in range(6000))
    assert status(get(server, "/", many)) != 200
    assert_alive(server)


@pytest.mark.parametrize("payload", [
    b"GET / HTTP/9.9\r\nHost: 127.0.0.1\r\n\r\n",
    b"\x00\x01\x02\xff\xfe garbage\r\n\r\n",
    b"GET /\x00 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
    b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nBad Header Without Colon\r\n\r\n",
    b"GET / HTTP/1.1\r\nHost : 127.0.0.1\r\n\r\n",
    b"G\x00ET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
    b"CONNECT evil.com:443 HTTP/1.1\r\nHost: evil.com:443\r\n\r\n",
    b"TRACE / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
    b"GET / HTTP/1.0\r\n\r\n",
    b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
])
def test_malformed_requests_never_succeed(server, payload):
    r = raw(server, payload)
    assert status(r) != 200
    assert b"X-Dashboard-Token" not in r and b"dashboard-token" not in r  # the page (with its token) is never served
    assert_alive(server)


def test_duplicate_host_headers_are_refused(server):
    r = raw(server, b"GET / HTTP/1.1\r\nHost: evil.com\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
    assert status(r) != 200
    r = raw(server, b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nHost: evil.com\r\nConnection: close\r\n\r\n")
    assert status(r) != 200


def test_dns_rebinding_host_is_refused(server):
    for host in (b"attacker.example", b"127.0.0.1.attacker.example", b"localhost.attacker.example:%d" % server, b"0.0.0.0"):
        r = raw(server, b"GET / HTTP/1.1\r\nHost: " + host + b"\r\nConnection: close\r\n\r\n")
        assert status(r) == 400 and b"dashboard-token" not in r


def test_request_smuggling_attempts_fail(server):
    body = b'{"path":"."}'
    both = (b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
            b"Content-Length: %d\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n0\r\n\r\n" % len(body))
    assert status(raw(server, both + body)) in (0, 400, 401, 403, 411, 413)
    # Correct framing: a 4-byte body followed by a second, separate request gives two separate answers.
    pipelined = (b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: 4\r\n\r\n"
                 b"{}  GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
    r = raw(server, pipelined)
    assert r.count(b"HTTP/1.1 ") == 2 and b"HTTP/1.1 401" in r and b"HTTP/1.1 200" in r  # the POST was judged on its own (no token)
    assert_alive(server)


def test_chunked_upload_to_the_api_is_refused(server):
    chunk = b"1000\r\n" + b"A" * 4096 + b"\r\n"
    payload = (b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
               b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + chunk * 200 + b"0\r\n\r\n")
    assert status(raw(server, payload)) in (0, 400, 401, 403, 411, 413)
    assert_alive(server)


def test_huge_declared_body_is_refused_before_it_is_read(server):
    r = raw(server, b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: 999999999\r\nConnection: close\r\n\r\n")
    assert status(r) in (401, 413)  # without the token: turned away at once, the body is never read


def test_crlf_injection_in_path_cannot_add_headers(server):
    r = get(server, "/%0d%0aSet-Cookie:%20pwned=1")
    head = r.split(b"\r\n\r\n")[0].lower()
    assert b"set-cookie" not in head


@pytest.mark.parametrize("path", [
    "/static/../../etc/passwd", "/static/%2e%2e/%2e%2e/etc/passwd", "/static/..%2f..%2fetc%2fpasswd", "/static/..%5c..%5cwindows%5cwin.ini",
    "/static/app.js/../../server.py", "/static/%00app.js", "//etc/passwd", "/..%2fserver.py", "/static/app.js%00.txt", "/static/index.html",
    "/docs", "/openapi.json", "/api/diagnose/../health/../../etc/passwd",
])
def test_path_traversal_and_probing_only_get_404s(server, path):
    r = get(server, path)
    assert status(r) in (400, 401, 404, 405, 0)
    assert b"root:" not in r and b"[extensions]" not in r and b"import " not in r.split(b"\r\n\r\n", 1)[-1][:200]


def test_a_stalled_client_does_not_block_others(server):
    with socket.create_connection(("127.0.0.1", server), timeout=3) as slow:
        slow.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Slow: ")  # never finishes the request
        start = time.perf_counter()
        assert status(get(server, "/api/health")) == 200
        assert time.perf_counter() - start < 2.0


def test_many_idle_connections_do_not_break_the_server(server):
    socks = []
    try:
        for _ in range(40):
            socks.append(socket.create_connection(("127.0.0.1", server), timeout=3))
        assert status(get(server, "/api/health")) == 200
    finally:
        for s in socks:
            s.close()
    time.sleep(0.5)
    assert_alive(server)


def test_connection_flood_then_recovery(server):
    socks = []
    try:
        for _ in range(250):
            try:
                socks.append(socket.create_connection(("127.0.0.1", server), timeout=2))
            except OSError:
                break
    finally:
        for s in socks:
            s.close()
    for _ in range(40):  # the server must come back by itself once the flood stops
        if status(get(server, "/api/health")) == 200:
            return
        time.sleep(0.25)
    pytest.fail("the server did not recover after a connection flood")


def test_keepalive_pipelined_flood_of_bad_requests(server):
    burst = b"GET /nope HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n" * 300 + b"GET / HTTP/1.1\r\nHost: evil.com\r\nConnection: close\r\n\r\n"
    r = raw(server, burst)
    assert r.count(b"HTTP/1.1 404") >= 1 and b"dashboard-token" not in r
    assert_alive(server)


def test_wrong_token_flood_locks_out_guessing(server):
    base = (b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n"
            b"Content-Length: 12\r\nX-Dashboard-Token: guess%d\r\nConnection: close\r\n\r\n" + b'{"path":"."}')
    codes = [status(raw(server, base % (server, i))) for i in range(25)]
    assert 401 in codes and codes[-1] == 429


def test_server_is_still_healthy_after_everything(server):
    page = get(server)
    assert status(page) == 200 and b'name="dashboard-token"' in page


@pytest.mark.parametrize("route", ["/api/diagnose/stream", "/api/browse", "/api/diagnose"])
def test_new_routes_turn_unauthenticated_callers_away(server, route):
    body = b'{"path":"."}'
    r = raw(server, b"POST " + route.encode() + b" HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
    assert status(r) in (401, 429)  # 429 if an earlier attack test already triggered the wrong-token lockout
    assert status(get(server, "/api/system")) in (401, 429)
    assert b"python" not in get(server, "/api/system").lower()  # nothing about the machine leaks
    assert_alive(server)
