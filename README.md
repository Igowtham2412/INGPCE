# INGPCE PPE Monitoring Platform

> **Evaluation Breif**

INGPCE is a local-first industrial safety monitoring platform. It ingests
configured CCTV/NVR streams, applies computer-vision models to detect PPE
compliance signals, persists incidents and evidence, and routes selected
alerts to operations through Microsoft Power Automate.

The platform is designed for **human-supervised safety operations**. It is not
an autonomous disciplinary or identity-decision system.

---

## 1. Executive summary

### Problem

Safety teams need a consistent way to monitor multiple camera feeds, identify
potential PPE violations, preserve evidence, review incidents, and notify
responsible teams without relying on manual observation of every camera.

### Solution

INGPCE combines:

- local RTSP/NVR video ingestion;
- PPE and phone-object detection;
- person tracking and temporal confirmation;
- PostgreSQL incident, audit, rule, and delivery records;
- local image/video evidence storage;
- browser-based operational dashboards;
- durable, retryable Power Automate alert delivery;
- optional, consent-controlled face matching for eligible incidents.

### Current scope

Implemented production-oriented capabilities include:

- live camera wall and camera health monitoring;
- missing helmet, missing vest, and phone-near-person signals supported by
  the configured model and rules;
- incident snapshots and short evidence clips;
- searchable incidents, playback, analytics, and camera-status history;
- configurable alert rules, cooldowns, recipients, and audit history;
- durable alert outbox with retry and dead-letter behavior;
- role-based access, session authentication, CSRF protection, and audit logs;
- optional asynchronous face enrollment, matching, review, and calibration.

### Important interpretation

The system reports **visual signals**, not intent:

- “Phone detected” means a phone was detected near a tracked person. It does
  not prove phone use.
- “Missing PPE” is only emitted when the configured model, association logic,
  and temporal confirmation satisfy the active policy.
- Face matching is a ranked suggestion for an authorized human reviewer, not
  an automatic identity assertion.

---

## 2. Evaluation summary

### What is measured

The repository supports evaluation of:

- frame-level detector behavior;
- temporal confirmation and cooldown behavior;
- person/PPE association;
- incident persistence and evidence generation;
- alert outbox reliability;
- face-match ranking and threshold decisions;
- dashboard/API smoke and end-to-end workflows.

### Current model evidence

The currently restored `models\best.pt` is the previously preferred PPE model.
During an internal candidate experiment, a feedback-derived split contained:

| Split | Images |
|---|---:|
| Training | 104611 |
| Validation | 68543 |

On that internal split:

| Model | Precision | Recall | mAP50 | mAP50-95 |
|---|---:|---:|---:|---:|
| Existing baseline | 0.360 | 0.420 | 0.352 | 0.218 |
| Candidate fine-tune | 0.607 | 0.559 | 0.545 | 0.343 |

These results are **not a site-independent benchmark**. The validation set was
created from reviewed feedback and is too small to establish production
accuracy, generalization, or fairness. The candidate was not retained because
the existing model was preferred operationally. A representative, manually
labeled site validation set is required before model promotion.

### Evaluation conclusion

The software pipeline is operationally testable, but model quality is not yet
certified for a particular site. The next DS milestone is a governed
site-specific benchmark with camera-stratified data, clear labels, and
false-positive/false-negative analysis.

---

## 3. System architecture

```text
RTSP/NVR cameras
       |
       v
Stream handlers and camera workers
       |
       v
PPE detector + phone detector
       |
       v
Filtering -> person/PPE association -> tracking
       |
       v
Temporal confirmation and cooldown
       |
       +--> PostgreSQL incident and audit records
       +--> Local snapshots and evidence clips
       +--> WebSocket live updates
       +--> Face-match job (optional and asynchronous)
       +--> Alert rules -> durable outbox -> Power Automate -> email

Browser dashboard <-> FastAPI API <-> PostgreSQL and evidence storage
```

### Runtime sequence

1. `app.config` loads environment configuration and validates required values.
2. FastAPI startup initializes the PostgreSQL pool and application services.
3. PPE and phone models are loaded locally.
4. Optional face services start only when explicitly enabled.
5. Camera configuration is synchronized idempotently with the database.
6. Camera workers capture frames and run inference on the configured schedule.
7. Detection results are filtered, associated, tracked, and temporally
   confirmed.
8. Confirmed violations create database records and evidence artifacts.
9. Matching alert rules enqueue durable outbox records.
10. Background workers deliver alerts and record every attempt.
11. The dashboard reads REST resources and receives live events over WebSocket.
12. Shutdown stops workers cleanly before closing the database pool.

### Backend modules

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI composition root, lifespan, middleware, routes, static dashboard |
| `app/config.py` | Environment parsing, validation, model paths, thresholds, seed configuration |
| `app/db.py` | Async PostgreSQL pool, schema-safe queries, persistence, audit, outbox |
| `app/cameras/` | RTSP capture, worker lifecycle, reconnects, camera status, evidence |
| `app/detection/` | Inference, filtering, association, tracking, compliance, debouncing |
| `app/alerts/` | Rules, cooldowns, durable delivery, retries, shift digests |
| `app/faces/` | Enrollment, encrypted embeddings, matching, review, calibration |
| `app/auth.py` | Password/session authentication and authorization helpers |
| `app/ws_hub.py` | WebSocket connection and event fan-out |
| `dashboard/` | Static HTML, CSS, and JavaScript operations client |
| `migrations/versions/` | Versioned database migrations |
| `scripts/` | Setup, imports, evaluation, model, maintenance, and diagnostics tools |

---

## 4. Data and ML pipeline

### 4.1 Input data

The platform consumes:

- RTSP/NVR video streams;
- camera configuration and required-PPE policy;
- optional reviewed feedback images and YOLO labels;
- optional consented employee enrollment photos;
- operator review decisions and alert-delivery outcomes.

Generated snapshots, clips, logs, model weights, and local face data are
runtime artifacts. They are not automatically training data.

### 4.2 Detection

The PPE detector wraps Ultralytics YOLO, Deepstream SDK and normalizes model class names through
configuration. The phone detector is a separate local YOLO model. Each
detection can be filtered by:

- class-specific confidence;
- bounding-box area;
- aspect ratio;
- camera region of interest;
- frame validity.

The current deployment uses local inference. Camera frames are not sent to a
cloud AI service by the application.

### 4.3 Association and compliance

PPE objects are associated with tracked people using configured spatial
regions and association rules. The compliance engine then evaluates required
PPE per camera/area.

The system supports explicit-negative logic where a “missing” decision requires
an explicit negative class or a configured policy. Unsupported classes should
remain unknown rather than being converted into false alerts.

### 4.4 Temporal confirmation

A single frame is not sufficient for a confirmed incident. The debounce and
tracking layers require a violation to persist across configured observations,
then apply a cooldown to avoid duplicate incidents.

This reduces noise but introduces latency and can miss short-lived events.
Evaluation must therefore measure both frame-level detection and event-level
precision/recall.

### 4.5 Evidence

For confirmed violations, the camera worker can persist:

- an annotated JPEG snapshot;
- a short evidence clip when configured;
- camera, time, zone, detection, and tracking metadata in PostgreSQL.

Evidence paths are referenced by database records and should be governed by
retention, access-control, and backup policy.

---

## 5. Face matching subsystem

Face matching is disabled by default and is independent of PPE incident
creation.

### Pipeline

```text
Eligible incident
  -> bounded asynchronous job
  -> same-track face observations
  -> local face detection and alignment
  -> embedding generation
  -> encrypted gallery comparison
  -> MATCHED / NEEDS_REVIEW / UNKNOWN / FACE_NOT_VISIBLE
  -> authorized human review
```

Eligible violation types are configured, currently including missing helmet,
missing arc-flash suit, and phone-detected events. A face-worker failure must
not block incident creation or alert delivery.

### Privacy and governance requirements

Before enabling face matching:

1. obtain organizational authorization and documented consent;
2. configure an encryption key and protected dataset paths;
3. review the selected detector, embedding model, and license terms;
4. restrict enrollment and review to authorized roles;
5. define retention and deletion procedures;
6. validate false-match and unknown-match behavior on representative data.

Employee photos and embeddings must remain local and must not be copied into
tickets, chat, source control, or external test services.

---

## 6. Alert delivery architecture

```text
Confirmed violation
  -> active alert rules
  -> PostgreSQL alert_outbox
  -> retry worker
  -> signed Power Automate HTTPS webhook
  -> Outlook/organizational email connector
  -> alert_dispatch_log
```

The application does not send SMTP email for incident delivery. It sends a
structured event and annotated image to the configured Power Automate flow.

The outbox provides:

- durable queueing;
- retry with bounded attempts;
- delivery status;
- dead-letter state for persistent failures;
- auditability of recipients and attempts;
- operator visibility in Alert Delivery.

Shift digests are separate scheduled events and use the configured local
timezone. The flow must support the event types documented by the application.

---

## 7. Data engineering and persistence

### PostgreSQL responsibilities

PostgreSQL stores operational and audit data, including:

- cameras, areas, and required-PPE policy;
- users, roles, sessions, and audit events;
- violations and evidence metadata;
- alert rules, outbox records, and dispatch logs;
- camera status history;
- face enrollment metadata, jobs, matches, and review decisions.

Large binary evidence remains in configured local storage rather than being
embedded in ordinary relational rows.

### Data quality controls

- Camera seed synchronization is idempotent and preserves operator-managed
  stream settings.
- Alert delivery is durable and retryable.
- Face imports reject ambiguous, outside-root, sample, and unconsented data.
- Enrollment images are hashed for idempotency.
- Review decisions are persisted with actor and audit context.
- Unsupported model classes produce unknown status instead of inferred alerts.

### Retention and storage

The largest runtime directories are normally `snapshots\`, `evidence\`,
`.venv\`, and `models\`. Retention must be agreed with safety, privacy, and
IT owners before cleanup. Never use broad recursive deletion as a storage
management strategy.

---

## 8. Dashboard and API

The browser client is a static application in `dashboard/`:

- **Live Wall**: live camera tiles, filters, reconnect, and detail views.
- **Playback**: recorder-backed playback where supported.
- **Incidents**: evidence review, filtering, and incident details.
- **Face Matches**: authorized face-review queue and in-page evidence gallery.
- **Analytics**: trends, counts, and operational summaries.
- **Cameras**: camera configuration and required-PPE policy.
- **Cameras & Health**: camera configuration, live fleet health, green/yellow/red
  status, availability timeline, and status history in one workspace.
- **Alert Delivery**: queue health, selection, evidence, and dispatch history.
- **Alert Rules**: rule matching, cooldown, recipient, and enablement controls.

### Camera-scoped alert rules

Alert Rules supports creating one rule for any available enabled camera through
the camera selector. A rule can target:

- one registered camera or all available cameras;
- one violation type;
- an optional sector/zone;
- a configurable cooldown;
- Power Automate email delivery;
- one or more comma-separated recipients;
- enabled or disabled state.

The selector is populated from the live camera registry rather than requiring
operators to type an identifier. Deleting or disabling a camera does not
silently rewrite existing rules; review affected rules before disabling a
source. Rule changes are persisted through `POST`, `PUT`, and `DELETE
/api/rules` endpoints and the alert engine refreshes its in-memory rules after
each successful change. The API also rejects a non-existent camera identifier,
so rules cannot silently target an unregistered source.

FastAPI publishes the machine-readable contract at `/openapi.json`. The API
is the source of truth for validation and authorization; the dashboard must not
duplicate backend business rules.

---

## 9. Security model

Security controls include:

- optional but recommended authentication;
- role-based authorization for administrative and face operations;
- signed session cookies;
- CSRF protection for state-changing browser requests;
- password hashing;
- audit logging;
- local processing of camera and face data;
- secret configuration through `.env`, not source code.

Protect these values as secrets:

- database credentials;
- NVR/camera credentials;
- Power Automate webhook URL;
- session secret;
- face encryption key;
- employee data and evidence.

The `.env` file is local and ignored. `.env.example` contains placeholders only.

---

## 10. Installation and reproducibility

### Prerequisites

- Windows host for the primary deployment path;
- Python 3.10, 3.11, or 3.12;
- PostgreSQL;
- access to RTSP/NVR streams;
- NVIDIA CUDA runtime is recommended but CPU inference is supported;
- Power Automate flow for email delivery.

### Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure PostgreSQL, model paths, camera credentials, authentication, and
alert settings in `.env`. Generate a session secret locally:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Download required model files using the repository scripts:

```powershell
.\.venv\Scripts\python.exe scripts\download_model.py
.\.venv\Scripts\python.exe scripts\download_phone_model.py
```

Start the application:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

The single Windows launcher is:

```powershell
.\run_server.bat
```

Default URL: `http://127.0.0.1:8080`

### Health check

```powershell
Invoke-WebRequest http://127.0.0.1:8080/api/health -UseBasicParsing
```

Expected result: HTTP 200 with a healthy application response.

---

## 11. Configuration principles

Configuration is centralized in `app\config.py` and supplied through `.env`.
Important groups include:

| Group | Examples |
|---|---|
| Database | `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, credentials |
| Models | `PPE_MODEL_PATH`, `PPE_PHONE_MODEL_PATH`, `PPE_DEVICE`, `PPE_IMG_SIZE` |
| Inference | confidence thresholds, detection FPS, ROIs, CPU threads |
| Tracking | track thresholds, buffer size, match threshold |
| Compliance | required PPE, confirmation frames, cooldown |
| Cameras | reconnect limits, frame timeout, status history |
| Alerts | Power Automate URL, outbox polling, retry limits |
| Authentication | `PPE_AUTH_ENABLED`, `PPE_SESSION_SECRET`, cookie settings |
| Face | enablement, model, encryption, thresholds, retention, queue limits |

Do not read environment variables directly in new workers or routes. Add
validated configuration to `app\config.py`.

---

## 12. Testing and quality gates

Run the smallest relevant checks during development, then the full suite before
handover:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app scripts tests
node --check .\dashboard\app.js
git diff --check
```

For dashboard changes, also verify:

1. `/api/health` returns HTTP 200;
2. login and role restrictions behave as expected;
3. live camera updates reconnect after a temporary network interruption;
4. evidence opens in the in-page gallery;
5. alert selection, dispatch, retry, and failure states are visible;
6. camera status colors reflect online, partial, offline, and no-history data.

### Acceptance checklist

- [ ] Database initializes and migrations complete.
- [ ] Every configured camera has a clear health state.
- [ ] A supervised detection creates one incident after confirmation.
- [ ] Snapshot and clip paths resolve to readable evidence.
- [ ] Cooldown prevents duplicate incident spam.
- [ ] Alert outbox records success and failure attempts.
- [ ] Dashboard receives live updates.
- [ ] Unauthorized face and administration routes are blocked.
- [ ] Retention, backup, and privacy owners are documented.

---

## 13. Model development and promotion

PPE retraining is a controlled experiment, not an automatic production action.

Recommended workflow:

1. collect representative site footage;
2. manually label a governed dataset;
3. split by camera/time/location to prevent leakage;
4. keep an untouched validation/test set;
5. report per-class precision, recall, AP, confusion matrix, and event-level
   false-positive rate;
6. evaluate lighting, distance, occlusion, camera, and shift strata;
7. compare candidate and baseline on identical data;
8. review operational latency and resource usage;
9. stage the candidate with rollback weights;
10. promote only after DS, safety, and operations approval.

Do not use generated evidence as labels without human verification. Do not
promote a model based only on aggregate mAP or a random image split.

The training entry point is:

```powershell
.\.venv\Scripts\python.exe scripts\train.py --help
```

Face recognition uses enrollment and calibration rather than PPE-style
retraining. Thresholds must be selected using false-accept and false-reject
analysis on consented validation data.

---

## 14. Operations and troubleshooting

### Camera offline

1. Check Camera Status for last frame and reconnect history.
2. Verify network route, NVR availability, and credentials.
3. Validate the configured stream URL.
4. Use the dashboard reconnect action.
5. Do not change ML thresholds to compensate for a transport failure.

### Alerts not arriving

1. Check Alert Delivery queue and recent dispatch history.
2. Inspect outbox state and retry/dead counts.
3. Check Power Automate flow run history.
4. Verify the private webhook configuration.
5. Re-test with a manager-authorized test event.

### Dashboard stale

1. Check `/api/health`.
2. Check the browser WebSocket indicator.
3. Review application logs.
4. Refresh the browser.
5. Restart only after identifying whether the issue is process, database,
   camera, or network related.

### Model startup failure

1. Verify the configured model path exists.
2. Confirm the virtual environment and package versions.
3. Load the model with the repository's Python environment.
4. Check GPU/CPU availability and memory.
5. Restore the last known-good model if the failure followed promotion.

---

## 15. Limitations and risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Camera angle, lighting, occlusion | Missed or false detections | Site-specific validation and camera placement review |
| Small/non-representative labels | Poor generalization | Governed, stratified, held-out test data |
| Temporal confirmation latency | Short events may be missed | Tune confirmation policy and measure event recall |
| Phone near person interpreted as use | Incorrect conclusion | Use explicit UI wording and human review |
| Face false match | Privacy and safety risk | Conservative thresholds, margin checks, review workflow |
| Stream outage | Blind monitoring interval | Camera health, reconnect, and status history |
| Webhook outage | Delayed notification | Durable outbox, retries, dead-letter visibility |
| Disk growth | Service degradation | Retention, monitoring, and approved cleanup |
| Secret exposure | Unauthorized access | `.env`, rotation, least privilege, no secret logging |

---

## 16. Ownership and handover

Before evaluation or production handover, assign named owners for:

- application host and Python environment;
- PostgreSQL database and backups;
- camera/NVR network and credentials;
- Power Automate flow and recipient governance;
- safety policy and incident review;
- face-data authorization and retention;
- model evaluation and promotion;
- evidence retention and deletion.

Record the following for each deployment:

- branch and commit;
- Python and dependency versions;
- model filenames and checksums;
- database migration state;
- camera inventory and stream status;
- alert-flow version;
- validation dataset version and metrics;
- known limitations and rollback procedure.

---

## 17. Repository layout

```text
INGPCS/
├── app/
│   ├── alerts/          Alert rules and delivery workers
│   ├── cameras/         Stream capture and camera lifecycle
│   ├── detection/       Inference, association, tracking, compliance
│   ├── faces/           Enrollment, matching, review, calibration
│   ├── config.py        Validated runtime configuration
│   ├── db.py            PostgreSQL data layer
│   └── main.py          FastAPI application and routes
├── dashboard/           Static operations interface
├── migrations/           Versioned database migrations
├── scripts/              Setup, model, evaluation, import, maintenance tools
├── tests/                Unit, integration, and end-to-end tests
├── models/               Local model weights; not source-controlled
├── snapshots/            Local image evidence; not source-controlled
├── evidence/             Local video evidence; not source-controlled
├── .env.example          Safe configuration template
├── requirements.txt       Runtime and validation dependencies
├── run_server.bat         Windows launcher
└── README.md              This evaluation and maintainer guide
```

---

## 18. Final assessment

INGPCE provides a coherent, locally processed safety-monitoring pipeline from
camera ingestion through reviewed incident evidence and durable notification.
Its strongest engineering properties are explicit service boundaries,
asynchronous failure isolation, durable alert delivery, auditability, and
human-review controls.

The primary remaining Data Science requirement is not another blind retraining
run. It is a representative, independently labeled, camera-stratified
evaluation program that quantifies class-level and event-level performance
before operational model promotion.
