# Security

ai-stack-diagnostics reads project folders on **your own computer**. This page says what it protects, how that is tested, and what it does not protect against.

## Reporting a problem
Please report vulnerabilities privately through GitHub: **Security tab, "Report a vulnerability"**. Do not open a public issue for a security bug. Include the steps to reproduce.

## What the tool will and will not do
- **Read-only.** It never writes, deletes, installs or runs anything in your project. Fix commands are *suggestions* shown as text.
- **Only the folder you choose.** Paths are validated; drive roots and your whole home folder are refused; symlinks that point outside the project are not followed; reads are size-capped.
- **No secrets leave the machine through the tool.** `.env` files are read for key *names* only; values are never returned.
- **Only two things are executed or contacted:** `nvidia-smi` (fixed arguments, never a copy found inside the scanned project or the current folder) and `GET` requests to a local Ollama on loopback. To recognise AMD, Intel and Apple graphics it only *reads* the Windows display-adapter registry key, `/sys/class/drm` on Linux, and the processor type on macOS; it runs no program for that.
- **The terminal command (`python -m diagnostics`) is the same read-only code.** It reads a pasted error from standard input only when `--explain` is given, caps it at 200,000 characters, and never echoes secrets.
- **`explain_error` never runs what you paste.** The pasted error text is only matched against a fixed list of known errors (patterns are bounded, text is capped, only the last lines are read). It is never executed, and it is never copied into the answer: only a few strictly validated names (a module, a port, a model) are reused. If the text looks like it contains an API key or password, the tool warns you without repeating it.

## The local dashboard (`python -m dashboard`)
Any web server on `localhost` can be attacked by *other websites open in your browser*. Defences, each covered by tests:
- Listens on `127.0.0.1` only. There is deliberately no option to listen on other network interfaces.
- Only answers requests whose `Host` is `localhost`, `127.0.0.1` or `[::1]` (blocks DNS rebinding).
- A random token, created at every launch and known only to the page it serves, is required for every API call; checked first, in constant time, with a lockout after repeated wrong guesses.
- Cross-origin and cross-site requests are refused (`Origin`, `Sec-Fetch-Site`); only JSON `POST` is accepted.
- Strict Content-Security-Policy (no inline scripts, no external resources), plus `nosniff`, frame denial, no caching and a restrictive Permissions-Policy.
- Everything from a project (names, messages) is shown as plain text, never as HTML.
- Request limits: header count and size, body size, concurrency, keep-alive time; `Transfer-Encoding` and unknown HTTP versions are refused; a security-relevant header sent twice (`Host`, `Origin`, `Content-Length`, ...) is refused because HTTP parsers disagree about which copy wins.
- Every `/api/` route except `/api/health` needs the token, including the live stream, system info and the folder picker. The folder picker runs a fixed helper program with no input from the page and only returns the path you click.

## How it is tested
`pytest` runs 380+ tests, including: fuzzing of every parser with random and binary input; a check that no regular expression can be made to hang (ReDoS); ~40 raw-socket attacks against the real server process (malformed HTTP, request smuggling, header floods, traversal, connection floods); and a browser-engine test that feeds attack strings into every field of the page. CI also runs `bandit` (static analysis), `pip-audit` and `npm audit` (known vulnerabilities), CodeQL, and Dependabot keeps dependencies current.

## Known limits (honest list)
- **A malicious program already running as you** can do anything you can do; this tool cannot defend against that, and neither can any local tool.
- **Idle-connection exhaustion.** A local process can hold many idle connections open and make the dashboard slow until they close. This needs code running on your machine.
- **Bare line-feed tolerance.** The HTTP library accepts requests that end lines with `\n`. This only matters behind a proxy; the dashboard has none.
- **`pip-audit` and `npm audit` only know vulnerabilities that are already public.**
- **Windows and real NVIDIA GPUs** are covered by simulated tests plus the author's own machine; other setups may behave differently. Please report any difference.
- Only a virtual environment inside the project folder (`.venv`, `venv`, `env`) is inspected; conda environments are not.
- Do not scan folders you do not trust **and then run the fix commands blindly**: read each command first.

## Notes on code scanning (CodeQL)

- **"Uncontrolled data used in path expression" on `resolve_project_path`:** this is by design. Choosing the folder to scan is the purpose of the tool, so the path does come from the user. The function resolves the path, requires an existing, readable folder, and refuses drive roots and the whole home folder. The dashboard only listens on localhost and every request needs the page's secret token.
- **Static files:** the dashboard serves exactly two files from a fixed table. The requested name only selects a table entry; it is never joined to a folder path.
- **Error messages:** the dashboard answers a refused path with one of a fixed set of sentences, never with the exception text or the path.
