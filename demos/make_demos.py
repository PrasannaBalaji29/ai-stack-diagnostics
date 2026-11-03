"""Builds two small demo projects to try the diagnostics on.

    python demos/make_demos.py [OUTPUT_FOLDER]

Creates demo-healthy (clean pass) and demo-broken (3 planted problems) in your home folder, under
ai-stack-demos (or in OUTPUT_FOLDER). They are built OUTSIDE the repo on purpose: if they sat inside it,
scanning the repo would also read the demo code and report its fake problems as real ones.
Nothing is installed and no network is used: the "venv" is a stub made of plain text files.
Run it again any time: it rebuilds just those two folders and touches nothing else.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

OUT = Path.home() / "ai-stack-demos"


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def fake_venv(project: Path, python: str, packages: dict[str, str]) -> None:
    venv = project / ".venv"
    base = str(Path(sys.executable).resolve().parent)  # a real folder, so the venv looks healthy
    write(venv / "pyvenv.cfg", f"home = {base}\ninclude-system-site-packages = false\nversion = {python}\n")
    write(venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"), "")  # stub interpreter
    site = venv / ("Lib/site-packages" if sys.platform == "win32" else f"lib/python{python.rsplit('.', 1)[0]}/site-packages")
    for name, version in packages.items():
        dist = site / f"{name.replace('-', '_')}-{version}.dist-info"
        write(dist / "METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        write(dist / "top_level.txt", name.replace("-", "_") + "\n")
        write(dist / "RECORD", "")


def build_healthy() -> None:
    p = OUT / "demo-healthy"
    write(p / "requirements.txt", "requests==2.32.3\npython-dotenv==1.0.1\n")
    write(p / "pyproject.toml", '[project]\nname = "demo-healthy"\nversion = "0.1.0"\nrequires-python = ">=3.9"\n')
    write(p / "app.py", "import os\n\nimport requests\nfrom dotenv import load_dotenv\n\nload_dotenv()\nprint(requests.__name__, os.getenv('API_KEY') is not None)\n")
    write(p / ".env.example", "API_KEY=your_key_here\n")
    write(p / ".env", "API_KEY=demo-not-a-real-key\n")
    write(p / ".gitignore", ".venv/\n.env\n")
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    fake_venv(p, version, {"requests": "2.32.3", "python-dotenv": "1.0.1"})


def build_broken() -> None:
    p = OUT / "demo-broken"
    write(p / "requirements.txt", "requests==2.32.3\npython-dotenv==1.0.1\nnumpy==1.26.4\n")
    write(p / "pyproject.toml", '[project]\nname = "demo-broken"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n')
    write(p / "app.py", "import os\n\nimport numpy\nimport requests\nfrom dotenv import load_dotenv\n\nload_dotenv()\nprint(numpy.__name__, requests.__name__, os.getenv('API_KEY'))\n")
    write(p / ".env.example", "API_KEY=your_key_here\nOPENAI_API_KEY=your_key_here\n")
    write(p / ".env", "API_KEY=demo-not-a-real-key\n")  # OPENAI_API_KEY is missing on purpose
    write(p / ".gitignore", ".venv/\n.env\n")
    # numpy is NOT installed, and the venv Python (3.10) is older than the project needs (3.12+)
    fake_venv(p, "3.10.11", {"requests": "2.32.3", "python-dotenv": "1.0.1"})


if __name__ == "__main__":
    if len(sys.argv) > 1:
        OUT = Path(sys.argv[1]).expanduser().resolve()
    for name in ("demo-healthy", "demo-broken"):
        shutil.rmtree(OUT / name, ignore_errors=True)
    build_healthy()
    build_broken()
    print(f"Built: {OUT / 'demo-healthy'}\nBuilt: {OUT / 'demo-broken'}")
