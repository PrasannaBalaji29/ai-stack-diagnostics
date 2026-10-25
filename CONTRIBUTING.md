# Contributing

Thanks for taking a look. Bug reports, new "known error" patterns and small fixes are all welcome.

## The one rule: read-only
This tool inspects a project, it never changes it. It must not install, delete, write or run anything inside the project it scans, and it must never show secret values (only key names). Please keep every change inside that rule. See [SECURITY.md](SECURITY.md).

## Set up
Windows (PowerShell):
```powershell
git clone https://github.com/PrasannaBalaji29/ai-stack-diagnostics.git
cd ai-stack-diagnostics
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
macOS and Linux:
```bash
git clone https://github.com/PrasannaBalaji29/ai-stack-diagnostics.git
cd ai-stack-diagnostics
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
Python 3.10 to 3.13 are supported. Please do not use syntax or modules that only exist in newer versions (for example `tomllib` needs 3.11).

## Before you open a pull request
Run the same checks CI runs:
```
python -m pytest -q
ruff check .
mypy diagnostics server.py dashboard
bandit -c pyproject.toml -r diagnostics server.py dashboard -ll -ii
```
For changes to the dashboard page also run the browser-engine test (needs Node.js):
```
npm ci --prefix tests/frontend
python tests/frontend/run_e2e.py
```
A new feature needs tests. A bug fix needs a test that fails without the fix.

## Add a known error to `explain_error`
1. Add a `Rule(...)` to `RULES` in `diagnostics/checks/explain_error.py`: an id, one or more bounded regex patterns, a title, the cause and the fix in plain words.
2. Add a sample line for it to `SAMPLES` in `tests/test_explain_error.py`. A test fails if a rule has no sample.
3. Keep patterns short and bounded (no nested quantifiers), and never put pasted text into the answer: only values that pass the strict name check may be reused.

## Style
Plain words in messages, no jargon. Findings say what is wrong, why, and the exact fix. Be honest about limits: if the tool cannot tell, it says so.
