import threading
import time

import pytest

from diagnostics import venvinfo
from diagnostics.checks.python_env import _site_packages


@pytest.fixture(autouse=True)
def fresh_cache():
    venvinfo.clear_cache()
    yield
    venvinfo.clear_cache()


@pytest.fixture
def counted(monkeypatch):
    calls = []
    real = venvinfo._load_one

    def spy(entry, root):
        calls.append(entry.name)
        return real(entry, root)
    monkeypatch.setattr(venvinfo, "_load_one", spy)
    return calls


def load(venv):
    return venvinfo.load_dists(_site_packages(venv), venv)


def test_second_load_reads_nothing(project, make_venv, make_dist, counted):
    venv = make_venv(project)
    for i in range(5):
        make_dist(venv, f"pkg{i}", "1.0")
    first = load(venv)
    assert len(counted) == 5
    second = load(venv)
    assert len(counted) == 5 and first == second and len(second) == 5  # served from the cache


def test_a_scan_with_several_checks_reads_the_files_once(project, make_venv, make_dist, counted):
    from diagnostics.diagnose import diagnose
    venv = make_venv(project)
    for i in range(6):
        make_dist(venv, f"pkg{i}", "1.0")
    (project / "app.py").write_text("import os\n")
    diagnose(project)  # compat, imports and the report facts all need the packages
    assert len(counted) == 6


def test_installing_a_package_invalidates_the_cache(project, make_venv, make_dist, counted):
    venv = make_venv(project)
    make_dist(venv, "alpha", "1.0")
    assert set(load(venv)) == {"alpha"}
    make_dist(venv, "beta", "2.0")
    assert set(load(venv)) == {"alpha", "beta"}


def test_removing_or_upgrading_a_package_is_noticed(project, make_venv, make_dist):
    import shutil
    venv = make_venv(project)
    make_dist(venv, "alpha", "1.0")
    make_dist(venv, "beta", "1.0")
    assert str(load(venv)["beta"].version) == "1.0"
    site = _site_packages(venv)[0]
    shutil.rmtree(site / "beta-1.0.dist-info")
    make_dist(venv, "beta", "2.0")
    assert str(load(venv)["beta"].version) == "2.0"
    shutil.rmtree(site / "alpha-1.0.dist-info")
    assert set(load(venv)) == {"beta"}


def test_different_environments_do_not_share_results(tmp_path, make_venv, make_dist):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    va, vb = make_venv(a), make_venv(b)
    make_dist(va, "only-in-a", "1.0")
    make_dist(vb, "only-in-b", "1.0")
    assert set(load(va)) == {"only-in-a"} and set(load(vb)) == {"only-in-b"}


def test_the_cache_is_bounded(tmp_path, make_venv, make_dist):
    for i in range(venvinfo.CACHE_SIZE + 4):
        folder = tmp_path / f"p{i}"
        folder.mkdir()
        venv = make_venv(folder)
        make_dist(venv, f"pkg{i}", "1.0")
        load(venv)
    assert len(venvinfo._cache) == venvinfo.CACHE_SIZE


def test_callers_cannot_poison_the_cache(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "alpha", "1.0")
    result = load(venv)
    result.clear()
    assert set(load(venv)) == {"alpha"}


def test_parallel_loading_matches_one_by_one(project, make_venv, make_dist):
    venv = make_venv(project)
    for i in range(30):
        make_dist(venv, f"pkg{i}", f"{i}.0", requires=["other>=1"] if i % 2 else [], modules=[f"mod{i}"])
    parallel = load(venv)
    root = venv.resolve()
    sequential = {d.name: d for d in (venvinfo._load_one(e, root) for e, _ in venvinfo._dist_entries(_site_packages(venv))) if d}
    assert parallel.keys() == sequential.keys()
    for name in parallel:
        assert parallel[name] == sequential[name]


def test_cap_on_package_count(project, make_venv, make_dist, monkeypatch):
    venv = make_venv(project)
    for i in range(12):
        make_dist(venv, f"pkg{i:02d}", "1.0")
    monkeypatch.setattr(venvinfo, "MAX_DISTS", 5)
    assert len(load(venv)) == 5


def test_broken_entries_are_skipped(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "good", "1.0")
    site = _site_packages(venv)[0]
    (site / "afile-1.0.dist-info").write_text("i am a file, not a folder")
    (site / "weird.dist-info").mkdir()
    assert "good" in load(venv)


def test_many_threads_loading_at_once(project, make_venv, make_dist):
    venv = make_venv(project)
    for i in range(20):
        make_dist(venv, f"pkg{i}", "1.0")
    results, errors = [], []

    def worker():
        try:
            results.append(load(venv))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(results) == 16 and all(r == results[0] for r in results)


def test_cached_reads_are_much_faster(project, make_venv, make_dist):
    venv = make_venv(project)
    for i in range(300):
        make_dist(venv, f"pkg{i}", "1.0", requires=["x>=1", "y>=2"])
    start = time.perf_counter()
    load(venv)
    cold = time.perf_counter() - start
    warm = float("inf")
    for _ in range(3):  # best of three: one slow moment on a busy CI machine must not fail the test
        start = time.perf_counter()
        load(venv)
        warm = min(warm, time.perf_counter() - start)
    assert warm < cold / 3 or warm < 0.05, f"cold {cold:.3f}s, warm {warm:.3f}s"


def test_cache_ignores_unreliable_directory_listing_times(project, make_venv, make_dist, counted, monkeypatch):
    """On Windows the folder time that os.scandir() reports can lag behind the real one and then
    change between two scans of an unchanged venv. The cache key must not depend on it."""
    import os
    from types import SimpleNamespace

    real_scandir = os.scandir
    ticks = iter(range(1, 10_000))

    class _Entry:
        def __init__(self, entry):
            self._entry = entry
            self.name, self.path = entry.name, entry.path

        def is_dir(self):
            return self._entry.is_dir()

        def stat(self):  # a different time every call, like a lagging Windows listing
            return SimpleNamespace(st_mtime_ns=next(ticks))

    class _Scan:
        def __init__(self, path):
            self._scan = real_scandir(path)

        def __enter__(self):
            return (_Entry(e) for e in self._scan.__enter__())

        def __exit__(self, *exc):
            return self._scan.__exit__(*exc)

    monkeypatch.setattr(venvinfo.os, "scandir", _Scan)
    venv = make_venv(project)
    for i in range(5):
        make_dist(venv, f"pkg{i}", "1.0")
    load(venv)
    load(venv)
    assert len(counted) == 5
