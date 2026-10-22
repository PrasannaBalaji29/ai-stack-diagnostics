"""Creates fixture projects for the browser-style test. Usage: python make_fixtures.py <outdir>"""
import sys
from pathlib import Path


def venv(root: Path, version: str, packages: dict[str, str]) -> None:
    v = root / ".venv"
    site = v / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    (v / "bin").mkdir()
    (v / "bin" / "python").write_text("")
    (v / "pyvenv.cfg").write_text(f"home = /usr\nversion = {version}\n")
    for name, ver in packages.items():
        info = site / f"{name}-{ver}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\n")
        (info / "top_level.txt").write_text(name + "\n")


def main(out: Path) -> None:
    healthy = out / "healthy"; healthy.mkdir(parents=True)
    venv(healthy, "3.13.0", {"requests": "2.32.0"})
    (healthy / "app.py").write_text("import requests\nimport os\n")
    (healthy / "requirements.txt").write_text("requests\n")

    broken = out / "broken"; broken.mkdir()
    venv(broken, "3.9.7", {"requests": "2.32.0"})
    (broken / "pyproject.toml").write_text('[project]\nname="x"\nrequires-python=">=3.10"\n')
    (broken / "app.py").write_text("import cv2\nimport requests\n")
    (broken / "requirements.txt").write_text("requests\n")

    evil = out / "<img src=x onerror=window.__xss=1>"; evil.mkdir()
    venv(evil, "3.9.7", {})
    (evil / "pyproject.toml").write_text('[project]\nname="x"\nrequires-python=">=3.10"\n')
    (evil / "app.py").write_text("import cv2\n")


main(Path(sys.argv[1]))
