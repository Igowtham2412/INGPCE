# Development Guide

## Workstation setup

From PowerShell at the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

Fill `.env` with local PostgreSQL, camera/NVR, model, authentication, and
Power Automate settings. Never commit `.env`.

## Run the application

```powershell
.\.venv\Scripts\python.exe -m app.main
```

The supplied `run_server.bat` and `run_server.sh` are convenience wrappers.
The default API/dashboard URL is `http://127.0.0.1:8080`.

## Change workflow

1. Read the owning module and its tests before editing.
2. Preserve API response shapes and dashboard element IDs unless the change
   includes all callers.
3. Keep secrets and generated data out of source control.
4. Add or update focused tests for behavior changes.
5. Run syntax checks and the smallest relevant test selection.
6. Run `git diff --check` and inspect the final diff.
7. Document configuration, operational, or API changes.

## Validation commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app scripts tests
node --check .\dashboard\app.js
git --no-pager diff --check
```

For dashboard changes, start the app and verify:

```powershell
Invoke-WebRequest http://127.0.0.1:8080/api/health -UseBasicParsing
```

Use the dashboard smoke/e2e tests when browser behavior changes.

## Model workflow

PPE model training is intentionally separate from application startup:

1. Review/correct feedback labels.
2. Create a held-out validation set.
3. Build a dataset YAML with `scripts/build_feedback_dataset.py`.
4. Train a candidate with `scripts/train.py`.
5. Compare baseline and candidate metrics on the same held-out data.
6. Preserve a backup before promotion.
7. Restart the application to load promoted weights.

Do not train on generated snapshots or evidence without verified annotations.
Face recognition is enrolled/calibrated, not retrained like the PPE detector.

## Git workflow

```powershell
git status --short
git diff --check
git add <files>
git commit -m "Describe the change" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
git push origin <branch>
```

Do not commit `.env`, model weights, face photos, evidence, snapshots, caches,
or runtime logs.
