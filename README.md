# ai-stack-diagnostics

**Why won't this AI project run, and what do I fix first?**
Point it at a project folder. It reads the project (never changes it), runs only the checks that apply, and gives **one prioritized report**: a health score and a numbered list of fixes.

Use it two ways: as an **MCP server** inside Claude Desktop, or through a **local dashboard** in your browser. Both run only on your computer.

## What it checks
| Layer | Examples | Tool |
|---|---|---|
| Python | venv Python vs the project's `requires-python`; packages that do not support your Python | `check_compat` |
| Libraries | missing or wrong-version packages; package conflicts | `check_python_env`, `check_compat` |
| Code vs environment | libraries imported in code but not installed or not listed; standard-library modules removed in new Python (`distutils`, `imp`, `cgi`...) | `check_imports` |
| Settings | `.env` missing or keys left empty (key names only, never values) | `check_env_and_keys` |
| Services | local Ollama and its models; ports already in use | `check_ollama`, `check_ports` |
| Hardware | PyTorch CUDA build vs NVIDIA driver | `check_gpu` |
| Everything | scan, run the needed checks, one report | **`diagnose`** |

See [ROADMAP.md](ROADMAP.md) for what is next.

## Install (Windows PowerShell)
```powershell
git clone https://github.com/PrasannaBalaji29/ai-stack-diagnostics.git
cd ai-stack-diagnostics
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Use it in Claude Desktop
Add this to `claude_desktop_config.json` (use your own paths), then fully quit and reopen Claude Desktop:
```json
{
  "mcpServers": {
    "ai-stack-diagnostics": {
      "command": "C:\\path\\to\\ai-stack-diagnostics\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\ai-stack-diagnostics\\server.py"]
    }
  }
}
```
In a new chat, click **+**, choose **Diagnose my project**, and enter a folder path. Or ask: *"Use the diagnose tool from ai-stack-diagnostics on C:\path\to\my-project"*.

## Use the dashboard
```powershell
python -m dashboard C:\path\to\my-project
```
A page opens at `http://127.0.0.1:8765` (your computer only):
- **Project Health:** an animated score gauge and a status (Healthy / Needs attention / Broken).
- **Layers Checked:** one tile per layer (Python, libraries, imports, keys, Ollama, ports, GPU) that fills in live while the scan runs. Click a tile to read its findings.
- **What to Fix First:** a numbered list, most important first, with **Copy** buttons for the suggested commands.
- **Details & Logs:** a real log of each step with timings, an Environment tab, a System Info tab, and **Download Log**.
- **Browse** opens your computer's own folder window; **Live Mode** re-scans every 5/10/30 seconds and tells you what got fixed or newly broke.
- **Scan History**, **Settings** (Dark / Light / System theme, intervals, animations) and **About**. History and settings stay in your browser only.

## Safe by design
Read-only; only the folder you choose; secrets never returned; the dashboard is locked down against other websites. Details, tests and honest limits: [SECURITY.md](SECURITY.md).

## Development
```powershell
python -m pytest -q          # all tests (about 400)
ruff check . ; mypy diagnostics server.py dashboard
bandit -c pyproject.toml -r diagnostics server.py dashboard -ll -ii
pip-audit -r requirements.txt
npm ci --prefix tests/frontend ; python tests/frontend/run_e2e.py   # browser-engine test (needs Node.js)
```
