## What does this change and why?

## Checklist
- [ ] It stays read-only (nothing is installed, written, deleted or run inside a scanned project) and never shows secret values
- [ ] Tests added or updated; `python -m pytest -q` passes
- [ ] `ruff check .` and `mypy diagnostics server.py dashboard` are clean
- [ ] Works on Python 3.10 (no 3.11-only syntax or modules)
- [ ] README or CHANGELOG updated if behavior changed
