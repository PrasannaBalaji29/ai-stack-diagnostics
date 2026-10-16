import socket
import time

from diagnostics.checks import ports
from tests.conftest import titles


def _listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s, s.getsockname()[1]


def test_busy_and_free_port():
    s, busy = _listener()
    try:
        free_sock, free = _listener()
        free_sock.close()
        f = ports.run([busy, free])
        assert f[0].severity.value == "warning" and f[0].fix
        assert f[1].severity.value == "ok"
    finally:
        s.close()


def test_none_and_empty():
    assert ports.run(None)[-1].title == "Common AI service ports in use"
    assert len(ports.run([])) == 1


def test_invalid_values_reported_not_crashing():
    f = ports.run([0, 70000, -1, True, "80", 3.5, None])
    assert "Ignored invalid port values" in titles(f)


def test_duplicates_collapsed():
    s, p = _listener()
    try:
        f = ports.run([p, p, p])
        assert sum(1 for x in f if str(p) in x.title) == 1
    finally:
        s.close()


def test_cap_on_port_count():
    f = ports.run(list(range(20000, 20000 + 80)))
    assert any("Only the first" in x.title for x in f)
    assert sum(1 for x in f if x.title.startswith("Port ")) == ports.MAX_PORTS


def test_detects_common_ai_port(monkeypatch):
    monkeypatch.setattr(ports, "_in_use", lambda p: p == 11434)
    assert "11434 (Ollama)" in ports.run([])[-1].detail


def test_ports_are_checked_in_parallel(monkeypatch):
    """Simulates Windows, where every closed port waits out the timeout."""
    def slow(port):
        time.sleep(0.3)
        return False
    monkeypatch.setattr(ports, "_in_use", slow)
    start = time.time()
    ports.run(list(range(20000, 20040)))  # 40 ports + 12 common, sequential would take ~15s
    assert time.time() - start < 3
