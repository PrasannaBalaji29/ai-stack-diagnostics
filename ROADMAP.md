# ai-stack-diagnostics: what it checks and where it fails

Question it answers: **"Why won't this AI project run, and what do I fix first?"** Read-only. Nothing is installed, changed or run except `nvidia-smi` (fixed arguments) and a GET to local Ollama.

| # | Where AI projects fail | Tool | Status |
|---|---|---|---|
| 1 | Python version wrong for the project or its packages | `check_compat` | done |
| 2 | Libraries missing or wrong version vs requirements | `check_python_env` | done |
| 3 | Libraries imported in code but not installed or not listed | `check_imports` | done |
| 4 | Packages conflicting with each other, missing dependencies | `check_compat` | done |
| 5 | Settings and API keys (`.env`) | `check_env_and_keys` | done |
| 6 | Services not running (Ollama, ports in use) | `check_ollama`, `check_ports` | done |
| 7 | GPU driver vs PyTorch, TensorFlow, JAX, ONNX Runtime, CuPy; mixed CUDA libraries; GPU memory; AMD, Intel, Apple graphics | `check_gpu` | done |
| 8 | Runtime errors: paste an error or traceback, get the cause and fix (about 45 known errors: missing library, CUDA out of memory, wrong model name, 401, 429, Ollama, ChromaDB, MCP, pip, Windows) | `explain_error` | done |
| - | One prioritized report from all checks | `diagnose` | done (start here) |
| - | Local dashboard (localhost only) | FastAPI + web page (`python -m dashboard`): live streaming, folder picker, history, settings, themes | done |
| - | Terminal command, no Claude Desktop needed | `python -m diagnostics` (report, `--json`, CI exit codes with `--fail-on`, `--explain` for pasted errors) | done |

`scan_project` reads the project first and suggests which checks to run. It lists every third-party library the code imports (`other_libraries`), so no library is missed because it is not on a built-in list.

Known limits: only a venv inside the project folder is inspected (`.venv`, `venv`, `env`); conda environments are not read; local module names that match a library name can hide a missing library. The GPU check compares NVIDIA drivers exactly; for AMD, Intel and Apple graphics it only says which library build can use them (there is no single driver number to compare, and GPU support lists change often). `llama-cpp-python` is not checked for a CUDA build because pip does not record it.

## Ideas for later
- Publish to PyPI so it installs with `pipx` or `uvx` (no clone needed).
- Read conda environments (today only a venv inside the project folder is inspected).
- A "copy the report as Markdown" button in the dashboard, to paste into an issue.
- More known errors for `explain_error` (it has about 45 today).
- Translations of the dashboard text.
