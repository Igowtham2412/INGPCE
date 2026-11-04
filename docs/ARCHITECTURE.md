# Architecture and Code Map

## 1. Runtime lifecycle

The application starts through `python -m app.main`:

1. `app.config` loads `.env`, validates settings, and builds seed camera data.
2. The FastAPI lifespan checks and loads the PPE and phone models.
3. `app.db.init_pool()` creates the PostgreSQL pool and applies the compatible
   schema/migration setup.
4. The optional `FaceRecognitionWorker` starts when `PPE_FACE_ENABLED=true`.
5. `CameraManager.reconcile_all()` loads enabled cameras and starts workers.
6. `AlertManager.start()` starts the durable alert outbox and shift-digest
   workers.
7. FastAPI serves `/`, static dashboard assets, REST APIs, and WebSockets.
8. Shutdown stops camera workers, face jobs, alert workers, and the DB pool in
   that order.

Startup must fail loudly for required model/database failures. Face matching is
the only intentionally optional subsystem; its failure is logged and PPE
detection continues.

## 2. Backend module boundaries

### `app/main.py`

The application composition root and HTTP boundary. It owns:

- FastAPI construction and lifespan startup/shutdown.
- Authentication and CSRF middleware.
- Role checks for API paths.
- Camera, incident, playback, analytics, status, alert, and face routes.
- Static dashboard mounting.

Keep route validation and HTTP response shaping here. Move reusable domain
logic into the owning package rather than adding more cross-cutting helpers to
this file.

### `app/config.py`

The single source of runtime configuration. Environment parsing, validation,
camera seed definitions, model paths, thresholds, retention, and feature flags
belong here. Do not read environment variables directly from workers or routes.

### `app/db.py`

The async PostgreSQL data layer. It owns the connection pool, schema-safe
queries, seed synchronization, incident/evidence records, face records,
outbox records, audit records, and status history. Callers should use DB
helpers rather than embedding SQL in route or worker code.

### `app/cameras/`

- `camera_manager.py`: camera worker lifecycle, capture loop, detection
  scheduling, annotations, evidence recording, and status publication.
- `stream_handler.py`: RTSP capture and reconnect behavior.

Camera workers must remain isolated: a single stream failure cannot terminate
the manager or other camera workers.

### `app/detection/`

- `detector.py`: Ultralytics model loading, inference, class normalization,
  confidence/ROI filtering, and phone detection.
- `association.py`: associates PPE and phone detections with people.
- `compliance_engine.py`: converts associated detections into PPE status and
  violation decisions.
- `debounce.py`: temporal confirmation and cooldown support.

Detection answers what is visible in a frame. Compliance decides whether the
observations meet the configured temporal and PPE rules.

### `app/alerts/`

- `rules_engine.py`: matches stored violations to enabled rules and cooldowns.
- `alert_manager.py`: writes/dispatches durable outbox jobs, retries failed
  sends, records dispatch outcomes, and builds shift digests.

The application never sends email directly. Power Automate receives the
configured event payload and evidence image.

### `app/faces/`

- `face_recognition.py`: asynchronous jobs, crop buffering, embeddings,
  ranking, threshold decisions, and review state.
- `face_dataset.py`: encrypted gallery and analyzer integration.
- `face_enrollment_upload.py` and `face_manifest.py`: consented imports.
- `face_calibration.py` and `face_validation.py`: offline evaluation.
- `face_readiness.py`: readiness checks and diagnostics.
- `track_identity.py`: track/crop observation aggregation.

Face work is deliberately downstream of incident creation. A face worker
failure must not block PPE detection, incident storage, or alert delivery.

### `dashboard/`

- `index.html`: semantic tab structure and forms.
- `style.css`: shared design tokens, responsive layout, and tab-specific
  components.
- `app.js`: authentication, API calls, WebSocket updates, rendering, tab
  state, dialogs, gallery/lightbox behavior, and form interactions.

The dashboard is a static client. It must not contain secrets or duplicate
business rules that belong in the backend.

## 3. End-to-end event flow

### Live camera flow

```text
RTSP frame
  -> stream handler
  -> camera worker
  -> PPE/phone inference
  -> detection filtering
  -> person/PPE association
  -> temporal confirmation and cooldown
  -> violation record + snapshot/clip
  -> WebSocket event + dashboard
```

### Alert flow

```text
confirmed violation
  -> RulesEngine
  -> alert_outbox row
  -> AlertManager retry worker
  -> Power Automate HTTPS webhook
  -> Outlook/flow email
  -> alert_dispatch_log row
```

### Face flow

```text
eligible violation
  -> persistent face job
  -> bounded same-track crop buffer
  -> local face detector/analyzer
  -> encrypted embedding comparison
  -> MATCHED / NEEDS_REVIEW / UNKNOWN result
  -> authorized dashboard review
```

## 4. Data ownership and retention

| Data | Owner | Location |
|---|---|---|
| Incidents, rules, users, audit, outbox, face metadata | PostgreSQL | Database |
| Snapshots and clips | Evidence configuration | `snapshots/`, `evidence/` |
| PPE feedback | Model workflow | `dataset/feedback/` |
| Employee face data | Face enrollment workflow | `dataset/faces/`, configured encrypted paths |
| Model weights | Deployment owner | `models/` |
| Runtime logs | Deployment owner | local log files |

Retention must be configured and monitored. Generated files are not source
code and must not be used as training labels without human verification.
