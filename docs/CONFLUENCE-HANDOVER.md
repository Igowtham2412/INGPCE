# INGPCE Industrial Safety Operations — Confluence Handover

> **Document status:** Repository-based implementation handover; stakeholder and deployment details still require confirmation.  
> **Snapshot date:** 18 November 2026  
> **System:** INGPCE PPE Monitoring Platform  
> **Repository:** `Igowtham2412/INGPCE`

## How to use this handover

This is a Confluence-ready master page. Keep it as one page or split the major
sections into child pages:

1. Product and stakeholder discussion
2. Architecture and end-to-end workflows
3. Implementation and API reference
4. Deployment, operations, testing, and support
5. Decisions, risks, and rollout

For day-to-day use by safety professionals, publish the companion
[Safety Operator Guide](./SAFETY-OPERATOR-QUICK-GUIDE.md) as a separate user
guide. It intentionally excludes developer setup and assigns first-time
installation and configuration to IT/application support.

The implementation descriptions below are based on the repository source and
tests. They do not prove that a particular site, camera, model, webhook, or
production deployment has been configured or validated. Items labeled
**Confirm** require input from product, operations, safety, privacy, IT, or the
deployment owner. No meeting transcript or approved decision record was found
in the repository; the discussion section is a structured summary to validate,
not a claim that those discussions already occurred.

---

## 1. Executive summary

INGPCE is a local-first industrial safety monitoring application. It connects
to configured CCTV/NVR streams, runs local computer-vision inference for PPE
and phone-related signals, associates observations with tracked people,
applies configurable policy and temporal confirmation, and presents incidents
and camera health through a browser dashboard.

The application supports:

- Live camera wall, fullscreen viewing, recorder playback, stream reconnect,
  camera/area administration, and camera availability history.
- PPE and phone detection, per-camera required PPE, person tracking, ROI/zone
  filtering, repeated-observation confirmation, and duplicate cooldown.
- Incident review, annotated and clean snapshots, evidence clips, CSV exports,
  statistics, and reviewed feedback dataset generation.
- Configurable alert rules delivered through a PostgreSQL outbox to Microsoft
  Power Automate, plus scheduled shift digests.
- Optional face enrollment and asynchronous candidate matching with
  administrator/manager access controls, review workflows, encrypted face
  data, and audit records.
- Optional vehicle speed estimates based on calibrated camera gates.
- Role-based authentication, CSRF checks for state-changing HTTP requests,
  audit logging, administrator settings, and guarded Git operations.

**Safety and decision boundary:** This is a human-supervised decision-support
tool. A detection is not proof of unsafe conduct, a face candidate is not an
identity assertion, and a speed estimate is not a certified measurement. Do
not use these outputs alone for discipline, identity, enforcement, or other
high-impact decisions.

## 2. Discussion summary and requirements

### 2.1 Problem statement

Safety teams need a single operational view of camera streams, potential PPE
and phone-related incidents, evidence, alert delivery, and camera availability.
Manual camera review is time-consuming. The application is intended to help
teams find events sooner while keeping a human reviewer responsible for
interpretation and action.

### 2.2 Stakeholders and responsibilities

| Stakeholder | Expected responsibility | Confirm named owner |
|---|---|---|
| Safety / EHS owner | Define safety rules, review process, and response policy | Yes |
| Camera / OT owner | Provide camera/NVR access, stream URLs, network paths, and retention expectations | Yes |
| Application owner | Maintain code, configuration, releases, and application support | Yes |
| Database / IT owner | PostgreSQL, access controls, backup, restore, and monitoring | Yes |
| Privacy / legal owner | Approve employee data, face workflows, notice/consent, and retention | Yes |
| Alert-flow owner | Own Power Automate flow, recipient groups, and delivery monitoring | Yes |
| Operators / managers | Review incidents and camera health, correct false positives | Yes |

### 2.3 Product requirements represented in the implementation

| Requirement | Implemented behavior | Acceptance evidence to collect at site |
|---|---|---|
| Observe cameras in one dashboard | Live wall and fullscreen WebSocket feeds; playback endpoint for supported NVR recordings | Validate every deployed stream and representative NVR playback |
| Detect safety signals | Local PPE and separate phone models; configurable confidence and filtering | Site-specific labeled validation and approved operating thresholds |
| Reduce transient detections | Person tracking, person/PPE association, temporal confirmation, and per-signal cooldown | Measure false positives/negatives on representative footage |
| Keep incident evidence | Incident rows plus optional snapshots and buffered video clips | Verify evidence paths, access, backup, and retention policy |
| Support human review | Incident gallery, review status/comment, optional correction labels, feedback export | Walk through operator review and correction process |
| Route actionable alerts | Rule matching and durable alert delivery queue; Power Automate integration | Test actual flow with approved recipients and failure/retry cases |
| Monitor camera availability | Camera health/status and historical uptime buckets | Confirm status reporting under real stream outages and service restart |
| Govern optional face workflows | Disabled by default; gated by auth/role and audited operations | Privacy/legal sign-off and documented consent/retention before enabling |
| Estimate vehicle speed where calibrated | Gate-based speed estimates and stored evidence | Validate against known-speed vehicles; not a certified instrument |

### 2.4 Scope boundaries

**Included:** camera ingestion and monitoring, PPE/phone signal detection,
incident/evidence review, operational analytics, configurable alert delivery,
administration, and optional controlled face/speed features.

**Not guaranteed by the application alone:** camera/network availability,
model accuracy at a particular facility, legal basis for processing personal
data, alert recipient response, evidence retention compliance, database
backups, production availability, or certified vehicle-speed measurement.

### 2.5 Discussion points to close before production

Record a decision owner, date, and resolution for each item:

1. Which cameras, areas, and PPE requirements are in scope for launch?
2. Who is the accountable incident reviewer, and what is the response SLA?
3. Which violation types may trigger alerts, to which approved groups, and at
   what hours?
4. What evidence and incident retention/deletion schedule applies?
5. Is authentication mandatory for all deployments? Who provisions and revokes
   accounts?
6. Is optional face matching prohibited, allowed under documented controls,
   or deferred? Who approves the legal basis, consent, encryption-key custody,
   and retention?
7. Is speed estimation required? Who calibrates and signs off each gate pair?
8. What are the required availability, recovery time, recovery point, backup,
   and support-hours targets?
9. Which release environment and rollback owner are used?
10. Which model versions and site dataset define acceptable accuracy?

---

## 3. User roles and access model

When authentication is enabled, the application uses the `admin`, `manager`,
`operator`, and `viewer` roles. HTTP state-changing requests require the
session's CSRF token. Browser sessions are signed, revocable, time-limited, and
stored in PostgreSQL. WebSocket connections require same-origin checks and an
authenticated session.

| Role | Intended access |
|---|---|
| Viewer | Read-only application functions |
| Operator | Viewer access plus incident review |
| Manager | Operational changes such as camera and alert-rule administration; manager-gated alert test |
| Admin | Manager access plus application settings, Git controls, user/invite administration, audit log, cleanup, and face administration |

Face and biometric endpoints require authentication independently of the
general role middleware. Face match and enrollment operations are restricted
to managers/admins; sensitive settings and permanent enrollment deletion are
admin-only. Review the current route-level guards before changing roles.

Authentication defaults to disabled in `app/config.py`. Administrative and
face functions explicitly deny access when authentication is disabled, but
other operational endpoints may be unauthenticated in that mode. Enable and
verify authentication before exposing the service to other machines. A
non-loopback API bind is rejected unless authentication is enabled and secure
cookies are configured. Use HTTPS at the deployment boundary.

---

## 4. Architecture

```text
IP cameras / NVR
       |
       | RTSP main/sub-streams; optional recorder playback
       v
Camera manager and per-camera workers
       |
       +--> Stream connection, reconnect/watchdog, frame buffers
       |
       +--> PPE YOLO model + phone YOLO model
       |       |
       |       +--> filtering / ROI / zones
       |       +--> person-to-PPE association and ByteTrack tracking
       |       +--> temporal confirmation and cooldown
       |
       +--> Incident persistence, snapshots, clips, optional speed estimates
       |       |
       |       +--> PostgreSQL (incidents, cameras, rules, audit, outbox, ...)
       |       +--> local evidence and model files
       |       +--> optional asynchronous face matching worker
       |
       +--> WebSocket live video and event broadcasts
       +--> Alert rules -> durable outbox -> Power Automate
       +--> Scheduled shift digest -> Power Automate

Browser dashboard <---- HTTP/REST + WebSocket ----> FastAPI application
```

### 4.1 Main components

| Component | Responsibility |
|---|---|
| `app/main.py` | FastAPI application lifespan, HTTP/WebSocket routes, auth middleware, dashboard serving |
| `app/config.py` | Environment configuration, camera seed definitions, detector/policy settings, validation |
| `app/db.py` | Async PostgreSQL access, migration startup, camera seed synchronization, persistence |
| `app/cameras/` | Camera lifecycle, stream processing, reconnect, health, evidence capture |
| `app/detection/` | Inference, filtering, PPE/person association, temporal confirmation, speed tracking |
| `app/alerts/` | Alert rule matching, dispatch/outbox processing, shift digest |
| `app/faces/` | Face data import/enrollment, matching, async jobs, calibration, validation |
| `app/auth.py` | Password hashing, signed session tokens, invite tokens |
| `app/settings_store.py` | Validated dashboard settings persistence |
| `app/git_operations.py` | Guarded, fixed repository status/commit/push operations |
| `app/ws_hub.py` | WebSocket video/event subscriber coordination |
| `dashboard/` | Static HTML, JavaScript, and CSS operations interface |
| `migrations/versions/` | Versioned PostgreSQL schema changes |
| `tests/` | Unit, integration, and dashboard end-to-end tests |

### 4.2 Startup and shutdown

On application startup, the FastAPI lifespan:

1. Verifies the configured PPE model file exists and loads the detector. The
   detector also requires a phone model containing a recognized cell-phone
   class.
2. Runs Alembic migrations to `head`, creates the PostgreSQL pool, performs
   session/invite cleanup, and synchronizes seed camera/area data.
3. Creates local snapshot storage.
4. Starts the optional face worker when enabled. Failure to start that
   optional worker is logged and PPE detection continues without it.
5. Reconciles camera workers and starts alert/outbox and shift-digest workers.

Shutdown stops camera workers, the optional face worker, alert workers, and
the database pool. Startup requires real database access and model weights;
successful process launch alone is not proof that cameras, alerts, or
inference are healthy.

---

## 5. End-to-end operating workflows

### 5.1 Camera to incident

1. A camera is configured in PostgreSQL (manually or from the default seed
   list) with a camera ID, stream URL(s), area group, enable flag, and required
   PPE.
2. The camera manager starts a per-camera worker. Stream errors are logged;
   reconnect and watchdog behavior attempt recovery.
3. Frames are sampled according to inference/frame-skip settings. The detector
   loads PPE and phone models locally, normalizes class names, and filters by
   confidence, box shape/size, and configured ROI where applicable.
4. Person detections are tracked. PPE/phone observations are associated with
   person boxes and PPE-specific person regions.
5. The compliance engine applies the configured compliance mode and temporal
   confirmation window. In `explicit_negative` mode, a missing PPE signal is
   distinguished from unknown/unobservable status when the model supports
   explicit negative classes. Phone observations use their own confirmation.
6. A confirmed violation is subjected to cooldown/deduplication behavior.
   The system records an incident, attempts to save annotated/clean image
   evidence and a video clip when configured, and broadcasts relevant events.
7. If eligible and face matching is enabled, an asynchronous face job may be
   queued; face matching is separate from the PPE incident decision.
8. Matching alert rules may enqueue delivery jobs. Delivery status and review
   outcomes are available to authorized users.

**Interpretation:** Camera angle, occlusion, lighting, stream quality, model
version, confidence thresholds, and site-specific PPE all affect results.
`unknown` is not equivalent to compliant.

### 5.2 Incident review and model feedback

1. Operator loads incidents and evidence in the **Incidents** area.
2. Reviewer records `confirmed` or `false_positive` and may add a comment.
3. Optional correction input can label a false-person detection or correct a
   PPE class/box.
4. The review endpoint creates an image/label pair in the configured feedback
   dataset and persists review metadata.
5. This workflow collects candidate training/evaluation material; it does not
   automatically retrain or deploy a model. Dataset governance, labeling
   review, training, evaluation, and model release remain explicit work.

### 5.3 Alert delivery

1. Managers configure rules by violation type, camera scope, zone, cooldown,
   recipients, and enabled state.
2. The rules engine refreshes the enabled rules cache periodically and matches
   confirmed events.
3. Matching deliveries are written to a durable PostgreSQL outbox.
4. The background worker claims jobs, sends supported eligible notifications
   to the configured Power Automate webhook, records attempts/status, and
   retries within the configured maximum.
5. The Alert Delivery view/API supports delivery health/history and a
   manager-authorized test send. Shift digests are produced for configured A/B/C
   shifts and timezone.

Current code explicitly limits Power Automate incident eligibility to
`no_helmet` and `phone_detected`. Confirm this rule set with safety and alert
owners before production. A configured webhook does not prove that the flow,
mail recipients, or downstream actions work.

### 5.4 Optional face matching

Face matching is disabled by default and is a separate optional feature.
Enabling it requires appropriate model/configuration, license acceptance,
encryption-key setup, documented organizational authorization, and an
operational consent/retention process.

The implemented paths include employee enrollment/import, quality checks,
encrypted face data, asynchronous matching/reprocessing, candidate ranking,
review/reassignment, history/analytics, and audited access. Candidate scores
are suggestions for a human reviewer only. Do not enable this feature until
the privacy/legal owner approves its use and safeguards.

### 5.5 Vehicle speed estimates

Speed tracking is camera-specific and requires enabled cameras, a physical
distance, speed limit, and two normalized gate lines. It estimates speed from
vehicle movement between gates and can retain snapshot/video evidence.
Calibration and validation against a known-speed vehicle are mandatory before
operational use. This feature is not a certified enforcement instrument.

---

## 6. Dashboard capabilities

Dashboard navigation and capabilities in `dashboard/index.html` / `app.js`:

| Area | Capability |
|---|---|
| Live Wall | Camera tiles, wall layout, filters, reconnect, fullscreen |
| Playback | Recorder playback where the configured NVR supports the playback URL |
| Incidents | Incident gallery/detail, evidence, filters, review, exports, cleanup controls |
| Face Matches | Optional match review, analytics, enrollment, employee records, settings/status |
| Analytics | Incident and location summaries |
| Camera Health | Camera inventory, area groups, sync defaults, speed calibration, availability history |
| Alert Delivery | Eligible evidence gallery, test send, delivery status/history |
| Alert Rules | Rule creation, update, and removal |
| Settings | Admin-only validated runtime settings, tunnel status, Git operations |

Saved dashboard settings are written to `.app_settings.json` in the repository
root. The file is local plaintext, ignored by Git, and takes precedence over
the corresponding environment values after restart. Secret setting values are
not returned by the settings API, but the file itself still requires host
access controls. Blank/clear behavior depends on the individual setting.
Settings changes require an application restart; the UI does not terminate and
relaunch the process automatically.

Admin Git controls operate on the current repository. Commit stages all
non-ignored changes after confirmation; push requires a clean working tree and
pushes the current branch to `origin`. Review all changed paths and secrets
before using these controls; normal review/release processes still apply.

---

## 7. API and integration overview

OpenAPI schema is exposed at `/openapi.json`. The following is a functional
index, not a complete request/response contract; consult the live OpenAPI
schema and Pydantic request models before building clients.

| Area | Representative endpoints |
|---|---|
| Authentication | `GET /api/auth/me`; `POST /api/auth/login`, `/logout`, `/invites`, `/accept-invite`; `GET /api/auth/users`; `GET /api/audit-log` |
| Health / settings / Git | `GET /api/health`; `GET/PUT /api/app-settings`; `GET /api/tunnel/status`; `GET /api/git/status`; `PUT /api/git/identity`; `POST /api/git/commit`, `/push` |
| Cameras | `GET/POST /api/cameras`; `GET/PUT/DELETE /api/cameras/{camera_id}`; `POST /api/cameras/{camera_id}/refresh`; `POST /api/cameras/sync-seed` |
| Camera playback / zones / speed | `GET /api/cameras/{camera_id}/playback`; camera zone CRUD under `/zones`; speed config and measurements under `/speed-settings` and `/speed-measurements` |
| Areas and health | `/api/area-groups`; `GET /api/camera-status/history` |
| Incidents | `GET /api/violations`; `POST /api/violations/{id}/review`; `DELETE /api/violations/{id}`; `GET /api/stats`; CSV export under `/api/export/violations.csv` |
| Evidence | `/api/snapshot/{id}`; `/api/violations/{id}/evidence/{kind}` |
| Alerting | `/api/rules`; `/api/alerts/power-automate/status`, `/violations`, `/test` |
| Face workflows | `/api/face/*` and `/api/face-matches/*`; manager/admin authorization required |

WebSockets:

- `/ws/video/{camera_id}` — single camera video
- `/ws/video?camera_ids=...` — multiplexed live wall feed
- `/ws/fullscreen/{camera_id}` — fullscreen feed subscription
- `/ws/events` — JSON event stream

External/runtime dependencies:

| Dependency | Use | Deployment-owned configuration |
|---|---|---|
| PostgreSQL | Application persistence, sessions, outbox, audits, jobs | Host, port, DB/user/password, backup/restore |
| CCTV/NVR | RTSP live streams and optional recording playback | Network access, stream credentials, recording availability |
| Local model files | PPE and phone inference; optional face models | Source, version, checksum, approved license |
| Power Automate | Alert and shift-digest destination | Private webhook, flow, recipient/access policy |
| Windows host/background scripts | Standard Windows launcher and optional watchdog/tunnel processes | Task scheduling, service account, logs, restart policy |

Cloudflare quick-tunnel display is optional Windows background-service
integration, not a replacement for production ingress security or a stable
deployment URL.

---

## 8. Data and persistence

### 8.1 Storage locations

| Data | Primary storage |
|---|---|
| Cameras, area groups, incidents, alert rules/delivery, users/sessions, audit records, face jobs/matches, speed measurements | PostgreSQL |
| Snapshots, video clips, optional face photos/reports, reviewed feedback, model weights, logs | Local filesystem paths configurable by deployment |
| Dashboard-saved application settings | Repository-root `.app_settings.json` (local plaintext) |

The Alembic chain currently contains revisions `0001_core` through
`0020_vehicle_speed_evidence`. Application startup upgrades to `head`. Back up
the database and review migration impact as part of release preparation.
Database migrations do not by themselves back up local evidence files.

### 8.2 Data handling and retention

Define site-specific retention and access rules for incident records, images,
clips, feedback, camera-status history, logs, face enrollment photos, face
embeddings, and match records. Do not assume one retention setting covers all
storage classes. The dashboard's “clean older than 2 days” incident action is
destructive and should only be used under an approved policy.

---

## 9. Implementation map

| Concern | Implementation location |
|---|---|
| App startup/shutdown, routes, role checks, evidence APIs | `app/main.py` |
| Environment variables, model names/classes, seeded cameras, thresholds | `app/config.py` |
| PostgreSQL operations, seed sync, migrations, data retrieval | `app/db.py`, `migrations/` |
| Camera connection and per-camera processing | `app/cameras/camera_manager.py`, `app/cameras/stream_handler.py` |
| Model loading/inference and detection filtering | `app/detection/detector.py` |
| Person tracking, PPE association, compliance, confirmation | `app/detection/compliance_engine.py`, `app/detection/association.py`, `app/detection/debounce.py` |
| Vehicle speed tracking | `app/detection/speed_tracking.py` |
| Rule cache, event matching, delivery/outbox, shift reports | `app/alerts/rules_engine.py`, `app/alerts/alert_manager.py` |
| Face enrollment, recognition, async processing, audit use | `app/faces/` |
| Session/password helpers | `app/auth.py` |
| Settings validation and local persistence | `app/settings_store.py` |
| Guarded Git functionality | `app/git_operations.py` |
| Browser UI | `dashboard/index.html`, `dashboard/app.js`, `dashboard/style.css` |
| Model/setup/validation scripts | `scripts/` |
| Demo simulation/evaluation | `demo/` |
| Power BI export dataset builder | `powerbi/build_powerbi_dataset.py` |

---

## 10. Deployment and first-run checklist

### 10.1 Prerequisites

- Windows host for the documented launcher; Python 3.10–3.12 is the range
  stated in the repository README. Reconfirm runtime compatibility against the
  currently pinned dependency set before deployment.
- PostgreSQL reachable from the application host.
- Network access and approved credentials for each CCTV/NVR stream.
- PPE and phone model files at the configured paths. NVIDIA/CUDA is
  recommended; CPU inference is supported with different performance.
- Approved secret-management, backup, and log-access arrangements.
- Optional Power Automate flow/webhook and recipients.
- Optional face model/runtime, license acceptance, and governance approval
  before enabling face functionality.

### 10.2 Install and configure (PowerShell)

Run from the repository root. Start from `.env.example`, then edit the local
`.env` using deployment-approved secret handling:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure PostgreSQL values (`POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`), model paths, NVR
credentials, camera configuration, and policy values. For a deployment
accessible beyond loopback, set `PPE_AUTH_ENABLED=true`, generate a unique
`PPE_SESSION_SECRET` with at least 32 bytes, configure `PPE_COOKIE_SECURE=true`
when served over HTTPS, and use an HTTPS ingress.

Generate a session secret locally:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Download model files using repository scripts, or use approved checksummed
artifacts from the model owner:

```powershell
.\.venv\Scripts\python.exe scripts\download_model.py
.\.venv\Scripts\python.exe scripts\download_phone_model.py
```

### 10.3 Start and verify

```powershell
.\run_server.bat
```

The application runs at `http://127.0.0.1:8080` by default. Check
`http://127.0.0.1:8080/api/health`, then separately verify database access,
camera worker state, live frames, inference, saved evidence, authentication,
alert delivery, and restart behavior. `/api/health` reports a lightweight
status and telemetry; it is not a complete dependency readiness probe.

During startup, the app applies Alembic migrations to `head` and performs
seed synchronization. In production, coordinate this migration behavior with
the release owner and ensure a database backup exists before a schema upgrade.

### 10.4 First administrator and user setup

Use the repository's administrator creation procedure (`scripts/create_admin.py`)
with authentication configured. Provision roles based on least privilege,
verify login/logout and CSRF behavior, and document who can create invites,
deactivate users, view audit events, change settings, or access face data.
Never put passwords or webhook signatures into Confluence.

### 10.5 Configuration notes

- Environment is loaded by `app/config.py`; use `.env.example` for supported
  environment variable names.
- Dashboard settings are a separately validated subset and use internal
  setting keys; save/restart is required.
- Persisted settings are local plaintext. Restrict access to `.app_settings.json`.
- Face encryption key rotation may make existing encrypted face data
  unreadable; preserve the key securely when existing face data must remain
  accessible.
- Review database pool bounds, CPU/GPU availability, frame rate, evidence
  storage capacity, and camera reconnect load for the target installation.

---

## 11. Test and release strategy

### 11.1 Repository test coverage areas

The test tree includes unit tests for detection, tracking/association,
compliance, camera lifecycle and reconnect, alert outbox/rules, settings,
authentication helpers, face flows, speed tracking, and WebSocket hub;
integration tests cover API/auth, camera playback/status, demo, face access,
WebSockets, and Power Automate; end-to-end tests cover dashboard smoke and
workflows.

### 11.2 Development checks

From the activated project environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app scripts tests
node --check .\dashboard\app.js
git diff --check
```

Useful focused checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\test_detection.py tests\unit\test_compliance_engine_phone.py
.\.venv\Scripts\python.exe -m pytest -q tests\integration\test_api_safety_contracts.py tests\integration\test_auth.py
.\.venv\Scripts\python.exe -m pytest -q tests\e2e\test_dashboard_smoke.py tests\e2e\test_dashboard_workflows.py
```

These are repository-level software checks, not site acceptance tests. This
documentation update did not run the application against production cameras,
database, or Power Automate. Record actual CI/test run links and results before
claiming a deployment is accepted.

### 11.3 Site acceptance / go-live checklist

- [ ] Confirm deployed commit, Python/dependency versions, model identities and
  checksums, and database migration revision.
- [ ] Confirm all camera IDs, area groups, stream URLs, and per-camera PPE
  policies with the camera and safety owners.
- [ ] Test live display, reconnect after a camera outage, and supported NVR
  playback.
- [ ] Run labeled footage evaluation and approve thresholds per camera/site.
- [ ] Verify unknown/occluded PPE cases are not misrepresented as compliant.
- [ ] Test evidence generation, access control, storage capacity, retention,
  and deletion.
- [ ] Test reviewer workflow and ensure feedback data is written to the
  approved location.
- [ ] Verify login, role boundaries, CSRF rejection, session expiry/revocation,
  and audit records.
- [ ] Verify Power Automate webhook, recipients, delivery history, and retry
  handling with a non-production test where possible.
- [ ] If face matching is enabled, obtain signed governance approval, validate
  consent, encryption/key recovery, role limits, audit records, and retention.
- [ ] If speed estimates are enabled, calibrate gates and compare against a
  known-speed vehicle; obtain owner sign-off.
- [ ] Test PostgreSQL backup/restore and local evidence backup/restore.
- [ ] Document rollback steps, support contacts, monitoring, and escalation.

---

## 12. Operations and troubleshooting

| Symptom | First checks | Escalation / safe response |
|---|---|---|
| App fails during startup | Read server stderr/log, verify model file, PostgreSQL, migrations, and config validation | Do not expose partial service; fix the reported dependency/configuration error |
| Camera offline or stale | Camera Health status/history, NVR/network reachability, stream credentials, worker telemetry | Reconnect and inspect server logs; do not tune detection thresholds to hide transport failure |
| No incidents visible | Verify camera worker is running, model class mapping, required PPE, policy mode, thresholds, ROI, and frame sampling | Compare with reviewed sample footage and detector logs; review site calibration |
| Evidence missing | Confirm configured directory, permissions, free disk space, and database evidence path | Preserve incident record; investigate storage/encoder error before cleanup |
| Alerts not arriving | Alert Delivery status, enabled rule scope, supported violation type, webhook configuration, Power Automate run history | Check worker/outbox retries and credentials privately; do not paste webhook URL into tickets |
| Settings do not take effect | Confirm save response, `.app_settings.json` access, and full app restart | Remove settings file only if intentionally reverting to environment/default values |
| Face feature unavailable | Check disabled-by-default setting, readiness, model/license, encryption key, consent/authorization, and manager/admin role | Keep face feature disabled until configuration and governance are approved |
| Speed reading implausible | Check distance, gate placement, line geometry, camera angle, vehicle detection and timestamps | Disable operational reliance and recalibrate against known speed |
| Git action fails | Refresh status, inspect every path, verify current branch, `origin`, and credential manager | Use normal review/rebase/release process; never enter credentials into the dashboard |

Logs and evidence can contain sensitive operational or personal data. Limit
their access and retention; redact before sharing.

---

## 13. Risks, limitations, and controls

| Risk / limitation | Control / owner action |
|---|---|
| False positives/negatives caused by model and site conditions | Evaluate representative labeled footage; human review; per-camera configuration |
| Inference load grows with camera count/resolution/FPS | Benchmark target hardware and tune sampling/concurrency before launch |
| RTSP/network instability can be mistaken for detection gaps | Monitor worker telemetry and historical camera status separately |
| Evidence and settings are local files in addition to PostgreSQL | Define filesystem ACLs, capacity, backup, restore, and retention |
| `.app_settings.json` contains local plaintext secret settings | Restrict host access; never commit/share; rotate secrets if exposed |
| Face data is sensitive biometric information | Disabled by default; governance approval, consent, encryption, RBAC, audit, retention |
| Face candidate confidence is not identity proof | Human verification; prohibit automated high-impact action |
| Speed is an estimate based on visual calibration | Validate against reference; never represent as certified enforcement measurement |
| Startup performs database migrations | Backup and coordinate schema changes with release process |
| Git commit UI stages all non-ignored changes | Review all staged paths; avoid use as production release mechanism |
| Quick tunnel URL can change and is not stable production ingress | Use approved secure and stable ingress for production |
| README setup statements may not match deployment state | Verify actual environment, `.env`, models, camera network, and CI evidence |

---

## 14. Decisions and action log

Create/update a Confluence decision table and fill in owners and dates. The
starting status is **Open** because no approved decision records are stored in
this repository.

| ID | Decision / action | Owner | Status | Due date / evidence |
|---|---|---|---|---|
| D-01 | Confirm first-release camera inventory, area groups, and PPE requirements | Safety + Camera | Open |  |
| D-02 | Approve detection evaluation dataset and minimum site acceptance criteria | Safety + ML/App | Open |  |
| D-03 | Approve alert-eligible violation types, rules, recipient lists, and escalation | Safety + Alert-flow owner | Open |  |
| D-04 | Define incident/evidence/log retention and deletion authority | Privacy + IT + Safety | Open |  |
| D-05 | Confirm authentication, network exposure, TLS, account lifecycle, and admin owners | IT + App owner | Open |  |
| D-06 | Decide whether face matching is disabled, deferred, or approved under controls | Privacy/Legal + Safety | Open |  |
| D-07 | Confirm backup/restore objectives for PostgreSQL and evidence files | IT/DBA | Open |  |
| D-08 | Set supported availability, recovery, monitoring, and support targets | Product + IT | Open |  |
| D-09 | Confirm if vehicle speed estimate is in scope and who certifies calibration | Safety + Site owner | Open |  |
| D-10 | Approve release/rollback procedure and migration gate | App + DB owner | Open |  |

### Suggested workshop agenda

1. Review the problem statement, operators, and decision boundaries.
2. Confirm camera/PPE scope and what counts as an actionable incident.
3. Walk the live-camera-to-review-to-alert workflow using a test camera.
4. Agree on accuracy evidence, false-positive handling, and human review.
5. Decide security, data retention, face-data, and evidence controls.
6. Confirm ownership for monitoring, alert flow, backups, support, and release.
7. Assign each action above and schedule site acceptance.

---

## 15. Release and support handover

Before calling the service production-ready, attach or link:

- Approved requirements/decisions and stakeholder sign-off.
- Deployment topology, host/service account, ingress/TLS, firewall and camera
  network paths.
- Environment-variable inventory with secret values excluded.
- Database migration/backup/restore evidence.
- Model identifiers, artifact source/checksum/license, and site evaluation
  report.
- Camera inventory and stream validation results.
- Test/CI run summary and site acceptance checklist.
- Power Automate flow owner and delivery test evidence.
- Retention, privacy, face-data (if applicable), and speed-calibration approvals.
- Runbook, support contacts, incident escalation, and rollback instructions.

**Current documentation conclusion:** The repository contains a substantial
implemented application and automated tests across its main services. Actual
“working” status for a particular site remains contingent on deployment
configuration and the site acceptance checks above. Do not mark production
readiness based solely on code presence or an HTTP health response.
