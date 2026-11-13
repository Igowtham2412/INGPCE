# INGPCE PPE Monitoring Platform

INGPCE is a local-first industrial safety monitoring application. It reads
configured CCTV/NVR streams, detects PPE and phone-related visual signals,
records incidents and evidence, and provides a browser dashboard for
human-supervised operations.

The application is a decision-support tool. A detected phone does not prove
phone use, PPE signals can be wrong, face matches are suggestions, and vehicle
speed values are estimates. Do not use any of these results alone for
disciplinary, identity, or enforcement decisions.

## What it includes

- Live camera wall, stream health, reconnect controls, and recorder playback.
- PPE and phone detection, person tracking, temporal confirmation, and
  configurable camera-specific PPE policies.
- Incident review, snapshots, buffered evidence clips, analytics, and CSV
  exports.
- Camera and area-group administration, historical camera availability, and
  optional calibrated speed tracking.
- Configurable alert rules and a PostgreSQL-backed delivery outbox for
  Microsoft Power Automate.
- Optional face enrollment, asynchronous matching, human review, and
  calibration tools. Face matching is disabled by default.
- Role-based authentication, CSRF protection, and audit records when
  authentication is enabled.
- An administrator-only **Settings** tab for supported application settings,
  plus guarded Git status, commit, and push controls.

## Architecture

```text
RTSP/NVR cameras
      |
      v
Camera streams and workers
      |
      v
PPE/phone inference -> filtering -> person association and tracking
      |
      v
Temporal confirmation and cooldown
      |
      +--> PostgreSQL incidents, rules, audit, and delivery outbox
      +--> Local snapshots and evidence clips
      +--> WebSocket dashboard updates
      +--> Optional asynchronous face matching
      +--> Power Automate alert delivery

Browser dashboard <-> FastAPI <-> PostgreSQL and local evidence
```

`app/main.py` composes the FastAPI application and its lifespan. `app/config.py`
loads deployment configuration, `app/db.py` contains PostgreSQL operations,
and modules in `app/cameras/`, `app/detection/`, `app/alerts/`, and `app/faces/`
implement their respective services. The dashboard is static HTML, CSS, and
JavaScript under `dashboard/`.

## Requirements and installation

The primary deployment path is Windows with Python 3.10–3.12 and PostgreSQL.
NVIDIA CUDA is recommended for inference; CPU inference is supported. A
Power Automate flow is required for email delivery.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set PostgreSQL connection details and other deployment secrets in `.env`.
Generate a session secret locally when enabling authentication:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Download the model weights required by the configured models:

```powershell
.\.venv\Scripts\python.exe scripts\download_model.py
.\.venv\Scripts\python.exe scripts\download_phone_model.py
```

Run database migrations and prepare an administrator account according to the
deployment workflow, then start the application:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\create_admin.py
.\run_server.bat
```

The dashboard defaults to `http://127.0.0.1:8080`. Check service readiness at
`http://127.0.0.1:8080/api/health`.

### Authentication for administration

The Settings tab and all Git controls require an authenticated `admin` account.
Enable authentication in `.env` before exposing administrative features:

```dotenv
PPE_AUTH_ENABLED=true
PPE_SESSION_SECRET=<unique random value of at least 32 bytes>
```

For a non-loopback bind, authentication and secure cookies are required by the
application configuration. Use HTTPS at the deployment boundary and set
`PPE_COOKIE_SECURE=true` when serving the dashboard over HTTPS. Do not expose
the application or its Git controls to an untrusted network.

## Dashboard

| Tab | Purpose |
|---|---|
| Live Wall | Live camera tiles, area/camera filters, reconnect, and camera details |
| Playback | Recorder playback where the NVR supports the configured stream |
| Incidents | Search and review incidents and their evidence |
| Face Matches | Restricted face-match review, analytics, enrollment, and status |
| Analytics | Incident and camera summaries |
| Cameras & Health | Camera inventory, speed calibration, availability history, and health |
| Alert Delivery | Review evidence, queue and dispatch status, and delivery history |
| Alert Rules | Configure camera, violation, cooldown, and recipient policies |
| Settings | Admin-only application and credential settings plus Git operations |

### Settings and restart behavior

Settings are grouped by inference, detection policy, alert delivery, face
matching, database pool, storage/retention, and camera streaming. The editor
shows each setting's supported type, range or choices, and description.
Cross-field combinations such as tracking thresholds and database pool bounds
are validated before saving.

Click **Save Settings** to validate and write the settings. The dashboard then
asks whether to restart. If confirmed, it shows the restart steps; the
application process is not terminated or relaunched automatically. Stop the
server in its terminal with Ctrl+C and run `.\run_server.bat` again. Saved
values are loaded at startup.

Dashboard values are stored in the repository-root `.app_settings.json` file.
It is ignored by Git and takes precedence over corresponding `.env` values
after restart. This file includes credentials configured in Settings and is
stored as local plaintext, not encrypted. Restrict access to the application
host, do not commit or share it, and back it up separately only if the
configuration must survive host replacement. Remove that local file to return
to environment/default values.

Settings can configure the database password, Power Automate webhook URL,
session signing secret, face encryption key, and default or seeded-NVR camera
credentials. Secret fields are write-only: the API returns only whether a
value is configured, never the value itself. Leave a field blank to keep its
current value; use its clear option to remove a saved value when available.
Custom camera stream URLs and their credentials remain managed in Cameras.
Changing the session secret signs users out after restart. Changing the face
encryption key can make existing encrypted face data unreadable; preserve the
original key if that data must remain accessible. Face matching still
requires license acceptance in the deployment configuration.

### Git controls

The Settings tab includes Git status, commit, push, and commit-author controls.
They are available only to authenticated administrators. The author editor
updates repository-local `user.name` and `user.email` in `.git/config`; it
does not change the credential used to authenticate pushes. GitHub credentials
are managed separately by Git Credential Manager:

1. **Refresh Status** shows the current branch and every changed, deleted, or
   untracked path reported by Git, along with the configured commit author,
   origin repository, credential helper, and GitHub CLI account when available.
2. **Commit** requires a commit message, the exact phrase `COMMIT ALL CHANGES`,
   and a second confirmation. It stages and commits all non-ignored changes in
   the repository. Review the complete path list first; do not commit secrets,
   local datasets, evidence, model weights, or other generated files.
3. **Push** is enabled only with a clean working tree. It pushes the current
   branch's `HEAD` to the fixed `origin` remote and requires the exact phrase
   `PUSH TO ORIGIN` plus a confirmation.

These controls run fixed Git commands in the repository; the browser cannot
choose a shell command, remote, or branch. They do not replace normal code
review and release procedures. Ensure Git credentials and remote permissions
are configured for the account running the application. Git's `user.name` and
`user.email` are commit-author metadata, and the owner shown in the origin URL
is the repository owner; neither necessarily identifies the account used to
authenticate. Git Credential Manager does not reveal its account through Git
configuration, so the dashboard does not claim that identity unless a local
GitHub CLI login can be detected.

To switch the account used for pushes on Windows, review the GitHub entry in
Windows Credential Manager and remove only that entry if you intend to sign in
as another account. The next Git operation may open Git Credential Manager to
authenticate again. Never enter a password or personal access token in the
dashboard.

## Application configuration

`app/config.py` reads deployment variables from `.env`. Dashboard-saved
settings override those variables at startup. Common environment variables
include:

| Area | Variables |
|---|---|
| Database | `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` |
| Model files and device | `PPE_MODEL_PATH`, `PPE_PHONE_MODEL_PATH`, `PPE_DEVICE`, `PPE_CPU_THREADS` |
| Detection | `PPE_CONF_THRESHOLD`, `PPE_IOU_THRESHOLD`, `PPE_IMG_SIZE`, `PPE_PHONE_CONFIDENCE`, `PPE_FRAME_SKIP`, `PPE_DETECT_FPS` |
| Cameras and evidence | `PPE_SNAPSHOT_DIR`, `PPE_EVIDENCE_CLIP_DIR`, `PPE_TILE_WIDTH`, reconnect and timeout variables |
| Alerts | `PPE_POWER_AUTOMATE_WEBHOOK_URL`, `PPE_ALERTS_ENABLED`, outbox and shift-report variables |
| Authentication | `PPE_AUTH_ENABLED`, `PPE_SESSION_SECRET`, `PPE_COOKIE_SECURE`, `PPE_SESSION_HOURS` |
| Camera credentials | `PPE_CAMERA_USERNAME`, `PPE_CAMERA_PASSWORD`, optional host-specific `PPE_NVR_<HOST>_USERNAME` and `PPE_NVR_<HOST>_PASSWORD` |
| Face matching | `PPE_FACE_ENABLED`, `PPE_FACE_ENCRYPTION_KEY`, model, quality, and retention variables |

Use `.env.example` as the safe template. Never put secrets in source code,
Git, screenshots, logs, or issue reports. Dashboard secrets are stored as
plaintext in the local ignored settings file; keep that file on a trusted host.
New settings should be parsed and validated centrally in `app/config.py`; do
not read environment variables ad hoc in workers or routes.

## API and persistence

FastAPI publishes its OpenAPI document at `/openapi.json`. The API is the
source of truth for validation and authorization. Most operational data is
stored in PostgreSQL; snapshots, clips, model weights, logs, and optional face
data remain in local storage.

The settings and Git endpoints are administrative and require authentication:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` / `PUT` | `/api/app-settings` | Read or persist supported settings; secret values are write-only |
| `GET` | `/api/git/status` | Read current branch and changed paths |
| `PUT` | `/api/git/identity` | Update repository-local commit author name/email |
| `POST` | `/api/git/commit` | Commit all non-ignored repository changes after confirmation |
| `POST` | `/api/git/push` | Push current `HEAD` to `origin` after confirmation |

Database migrations live in `migrations/versions/`. Camera seed
synchronization is idempotent and preserves operator-managed stream settings.
Alert delivery uses a durable outbox with bounded retries and records delivery
attempts for review.

## Detection, evidence, and face matching

The PPE detector and separate phone detector run locally. Detection signals
are filtered and associated with tracked people; temporal confirmation and
cooldown help reduce duplicate and transient incidents. Model quality and
camera performance must be evaluated using representative, independently
labeled site data before operational reliance.

Confirmed incidents may store an annotated snapshot and short evidence clip.
Evidence retention and access should be governed by site policy. Generated
evidence is not automatically training data.

Face matching is optional, disabled by default, and asynchronous. It requires
documented organizational authorization, appropriate consent, license
acceptance, encryption-key configuration, role restrictions, and a retention
procedure. A face match is a ranked suggestion for a human reviewer, never an
automatic identity assertion.

Vehicle speed tracking is an estimate based on calibrated gate placement and
detected vehicle motion. Validate it against known-speed vehicles; it is not a
certified speed instrument.

## Security and privacy

- Enable authentication before exposing the service beyond a trusted local
  machine. Use unique, strong passwords and limit administrator accounts.
- Protect the `.env` file and `.app_settings.json`; back them up only through
  approved secure processes.
- Keep camera streams, employee data, face samples, and evidence within
  authorized storage and access boundaries.
- Do not commit credentials, faces, evidence, local datasets, or model weights.
- Review retention and deletion requirements with safety, privacy, and IT
  owners.
- Treat detection and face matching outputs as fallible signals requiring
  human judgment.

## Testing and quality checks

Run focused tests during development and the full suite before release:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app scripts tests
node --check .\dashboard\app.js
git diff --check
```

Useful focused checks include:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\test_settings_store.py
.\.venv\Scripts\python.exe -m pytest -q tests\integration\test_api_safety_contracts.py
.\.venv\Scripts\python.exe -m pytest -q tests\e2e\test_dashboard_smoke.py
```

For a deployment, also verify database migration state, login and role
restrictions, live camera reconnect behavior, evidence access, alert delivery,
settings persistence after restart, and Git actions on a test branch.

## Operations and troubleshooting

### Startup or database failure

1. Check the application log and `/api/health`.
2. Verify PostgreSQL availability, credentials, migrations, and pool settings.
3. Confirm required model paths exist and package/runtime versions are
   compatible.
4. Check CPU/GPU availability and memory.

### Camera offline

1. Review camera health and status history in **Cameras & Health**.
2. Verify network access, NVR availability, and the configured stream.
3. Use the dashboard reconnect action and inspect the server log.
4. Do not adjust detection thresholds to compensate for a transport failure.

### Alerts not arriving

1. Review queue health and dispatch history in **Alert Delivery**.
2. Check the Power Automate flow run history and private webhook configuration.
3. Verify alert rules, recipients, and outbox retry/dead-letter status.

### Settings do not appear after save

Confirm the server was restarted, `.app_settings.json` remains in the
repository root, and the process can read it. Secrets and excluded deployment
controls must still be changed in `.env`. Invalid persisted settings stop
startup with an explicit error rather than being silently ignored.

### Git action fails

Refresh status, review the changed paths, and ensure the repository has a
current branch, a configured `origin`, and valid Git credentials. Push requires
a clean working tree. Resolve merge/rebase or hook errors using the normal Git
workflow rather than retrying blindly.

## Repository layout

```text
INGPCS/
├── app/
│   ├── alerts/          Alert rules and delivery workers
│   ├── cameras/         Stream capture and camera lifecycle
│   ├── detection/       Inference, tracking, compliance, and speed tracking
│   ├── faces/           Enrollment, matching, review, and calibration
│   ├── config.py        Environment and persisted runtime configuration
│   ├── settings_store.py Validated dashboard settings persistence
│   ├── git_operations.py Guarded repository status/commit/push
│   ├── db.py            PostgreSQL data layer
│   └── main.py          FastAPI application and routes
├── dashboard/           Static operations interface
├── migrations/          Versioned database migrations
├── scripts/             Setup, model, evaluation, and maintenance tools
├── tests/               Unit, integration, and end-to-end tests
├── models/              Local model weights; do not commit
├── snapshots/           Local incident evidence; do not commit
├── evidence/             Local video evidence; do not commit
├── .env.example          Safe configuration template
├── requirements.txt      Runtime and validation dependencies
├── run_server.bat        Windows launcher
└── README.md             Setup, operations, configuration, and security guide
```

## Deployment handover checklist

- [ ] Assign owners for the application host, database, cameras, alert flow,
  safety review, face-data governance, and evidence retention.
- [ ] Record the deployed branch/commit, Python/dependency versions, model
  checksums, and database migration state.
- [ ] Validate camera coverage and representative model performance.
- [ ] Test alert delivery, restart recovery, backups, and administrator access.
- [ ] Document known limitations, retention rules, and rollback procedures.
