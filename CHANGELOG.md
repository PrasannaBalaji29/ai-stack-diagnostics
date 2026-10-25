# Changelog

All notable changes. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-10-25
### Added
- Terminal command: `python -m diagnostics` (health report, `--json`, `--fail-on` exit codes for CI, `--explain` for pasted errors).
- `explain_error` tool: paste an error or traceback and get the cause and fix in plain words (45 known errors, project cross-check, secret-key warning).
- GPU checks beyond PyTorch: TensorFlow, JAX, ONNX Runtime, CuPy, mixed CUDA 11 and 12 libraries, flash-attn, and GPU memory almost full.
- AMD, Intel and Apple graphics: guidance on which library build can use them.
- README instructions for Windows, macOS and Linux; macOS added to the automated tests.
- Dashboard: automatic free port when 8765 is busy.
### Changed
- Dashboard light mode: no glow, clearer text colors (every text passes the WCAG AA contrast check), solid blue menu item.
- Dashboard layout: no sideways scrolling on 1366 to 1920 pixel screens.
- Dashboard shows only fixed, pre-written error sentences for refused paths, never exception text.
- Accessibility: heading order and expandable rows fixed (axe-core: 0 violations in light and dark mode).
### Fixed
- Windows: the installed-packages cache never hit because directory listing times changed between scans.
- A very long project path crashed path checking instead of giving a clean error.
- The ports note said "in use" when no common AI port was in use.

## [0.2.0]
- Local dashboard (localhost only, read-only) with live scan, history, settings and downloadable log.

## [0.1.0]
- MCP server with checks for Python, libraries, imports, `.env` keys, Ollama, ports, GPU and an overall `diagnose` report.
