# INGPCE Safety Monitoring â€” Executive Overview and Handover

## Maintainer documentation

Use the focused guides for ongoing development and operations:

- [Consolidated maintainer reference](#consolidated-maintainer-reference)
- Architecture and code map (in the consolidated reference)
- Development guide (in the consolidated reference)
- Operations runbook (in the consolidated reference)

## Executive overview

INGPCE is a camera-based safety-support system for industrial operations. It
turns configured CCTV/NVR video into a live operations dashboard, searchable
incident evidence, camera-health information, and email notifications through
Microsoft Power Automate.

### What the system does

- Monitors configured camera feeds and tracks people across incoming frames.
- Looks for missing hardhats and visible mobile phones near tracked people.
- Saves incident snapshots and short evidence clips, and keeps incident
  history for review and reporting.
- Shows live camera status, playback where the recorder supports it, and
  operational analytics in a browser dashboard.
- Sends configured helmet and phone-detection alertsâ€”with an annotated imageâ€”
  through the organizationâ€™s Power Automate email flow.

### How an alert reaches the team

**Camera video â†’ local AI detection â†’ tracked incident and evidence â†’ matching
alert rule â†’ Power Automate â†’ email recipient.**

The application runs the vision models on the application computer; it does
not send camera frames to a cloud AI service. Power Automate receives incident
details and the selected evidence image only for configured alerts. A detected
phone means a phone was seen near a tracked person; it does not prove the
person was using it. Automated detections are decision-support signals and
should be reviewed by a person before consequential action.

### Business value

- Gives safety and operations teams a common view of configured cameras,
  incidents, and camera availability.
- Preserves image/video evidence and incident history to support follow-up.
- Routes selected alerts to operational email without requiring an SMTP
  mailbox password in the application.
- Provides a foundation for measuring trends and improving models against
  reviewed examples from the actual site.

### Important limitations and decisions

- Detection quality depends on camera angle, lighting, distance, occlusion,
  model quality, and local validation. No â€œbest in the worldâ€ accuracy claim
  is made; test against labeled footage from the site before relying on results.
- The current development host has a CUDA-enabled PyTorch runtime and an
  RTX 4070. Higher-resolution detection and the additional phone model consume
  compute; production camera capacity must still be measured under the site's
  real load.
- The current helmet model is a general PPE model, not yet fine-tuned or
  independently measured against a site-specific held-out dataset.
- Phone-object detection identifies a visible phone near a person; confirming
  phone use would require a separately designed and validated behavior model.
- Camera use, evidence retention, access, employee notice, and any response
  based on an alert must follow company policy and applicable law.

### Current deployment at a glance

| Area | Current setup |
|---|---|
| Application | Windows-hosted local dashboard and API |
| Cameras | Configured CCTV/NVR streams, accessed over RTSP |
| AI processing | Local PPE model plus local YOLO26 phone-object model |
| Tracking | ByteTrack person tracking with temporal observation checks |
| Data | PostgreSQL incident history and local evidence files |
| Email | Microsoft Power Automate flow and Outlook connector |
| External access | Optional Cloudflare quick tunnel; its URL may change after restart |
| Operational owner | Assign a named application, database, camera, and flow owner during handover |

### CEO demonstration path

1. Open the dashboard and show the Live Wall and Camera Status.
2. Open Incidents to show how an event is retained with its image evidence.
3. Show an approved, supervised example of a missing-helmet incident and its
   email notification.
4. Explain that a phone alert reports a visible phone near a person, not
   confirmed use.
5. Close with the current validation status, operational owner, data
   retention/privacy controls, and the plan to measure site accuracy.

This README also contains the detailed technical setup, testing, operations,
recovery, and maintainer handover instructions.

## 1. System overview

### Main capabilities

- Live camera wall with filters, full-screen viewing, and reconnect controls.
- Recorded playback for supported Hikvision NVR streams.
- PPE incident detection, snapshots, short evidence clips, review, and model
  feedback export. Missing-helmet detection uses a larger inference image and
  a lower class-specific confidence threshold, with temporal confirmation.
- Missing-helmet and phone-detected incident snapshots and clips are stored in
  separate `helmet_violations` and `phone_detected` folders under `snapshots/`
  and `evidence/clips/`. Existing records can be organized with
  `python scripts/organize_violation_evidence.py` to preview, then rerun with
  `--apply` to update their database paths and move their files.
- Person tracking and phone-object detection. A visible phone is associated
  with a tracked person and reported as **Phone detected near person**; this
  does not establish that the person is using it.
- Safety vest detection is supported by the configured PPE model's `Safety
  Vest` and `NO-Safety Vest` classes. It is included by default for seeded
  cameras and can be selected in **Edit Camera â†’ Required PPE**.
- Camera and area-group management; seed camera synchronization.
- Analytics and camera status history.
- Configurable incident rules with matching by camera, area/zone, violation
  type, and cooldown.
- Power Automate email delivery for incident alerts. Rules specify the email
  recipients. There is a manager-only test-send panel.
- Role-based users, audit logging, and CSRF protection when authentication is
  enabled.

### Alert delivery flow

1. The detector confirms and stores a violation.
2. The rules engine evaluates active database rules.
3. Matching alerts are written to PostgreSQL's durable `alert_outbox`.
4. The background worker POSTs incident details and recipient addresses to
   the Power Automate signed HTTPS URL.
5. The flow's Outlook action sends the email and returns a successful HTTP
   response.
6. The worker records the attempt in `alert_dispatch_log`. Failed sends are
   retried with backoff and eventually moved to the outbox `dead` state.

The application does not send incident email itself. It sends the flow
recipient list, subject, text body, and annotated JPEG for missing-helmet and
phone-detected incidents. It does not upload video clips to the flow.

Incident alerts are sent immediately and are also collected into automatic
Power Automate shift digests. Digests use Shift A 06:00â€“14:00, Shift B
14:00â€“22:00, and Shift C 22:00â€“06:00 in the configured server timezone.
Each digest contains only the missing-helmet and phone evidence images. The
flow must handle `event_type` `ppe_shift_digest` and its `images` array.

### Main components

| Path | Responsibility |
|---|---|
| `app/main.py` | FastAPI API, authentication middleware, app startup |
| `app/config.py` | Environment variables and seed camera/area configuration |
| `app/cameras/` | RTSP capture, camera worker lifecycle, reconnect/watchdog behavior |
| `app/detection/` | PPE/phone inference, person tracking, and compliance decisions |
| `app/faces/` | Face enrollment, recognition, calibration, validation, and readiness |
| `app/alerts/` | Rule evaluation, cooldowns, durable outbox, and Power Automate delivery |
| `app/db.py` | PostgreSQL schema, queries, seed synchronization |
| `dashboard/` | Browser dashboard |
| `migrations/versions/` | Ordered Alembic database migrations |
| `scripts/` | Setup, model, account, and support utilities |

## 2. Requirements and network access

- Windows 10/11 for the supplied scheduled-task deployment; Python 3.10â€“3.12
  (Python 3.12 recommended).
- PostgreSQL 14+ reachable from the application host.
- Network route from the host to the NVRs/cameras and RTSP access (normally
  TCP 554).
- Initial model download access and enough disk for model weights, snapshots,
  clips, logs, and PostgreSQL backups.
- Outbound HTTPS access from the application host to the Power Automate
  endpoint; the flow owner needs a working Microsoft 365 Outlook connection.
- A current Chromium installation for the browser end-to-end test.

Confirm camera/NVR access, storage capacity, retention policy, and Microsoft
Power Platform licensing/security policy with the site administrator before
production handover.

## 3. First-time Windows setup

Run commands from the project root (`C:\INGPCS` in the current deployment).

### 3.1 Configure PostgreSQL

Create a dedicated database role and database with a PostgreSQL administrator.
Use a unique password and store it only in `.env`:

```sql
CREATE ROLE ppe_user LOGIN PASSWORD 'replace-with-a-unique-secret';
CREATE DATABASE ppe_compliance OWNER ppe_user;
```

If the role/database already exist, do not recreate them. Verify the server
service is running and that the application host can reach the configured
database host and port.

### 3.2 Create Python environment and install dependencies

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

`requirements.txt` installs development/test tools as well as Playwright.
For runtime-only installation use `requirements.txt`.

### 3.3 Create `.env`

Copy the safe template and edit the local copy:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set at minimum:

- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`
- NVR usernames/passwords for each configured NVR host
- `PPE_MODEL_PATH` if the model is not at the default `models\best.pt`
- `PPE_PHONE_MODEL_PATH` if the phone model is not at
  `models\yolo26m.pt`
- `PPE_POWER_AUTOMATE_WEBHOOK_URL` after creating the flow (see section 5)
- `PPE_AUTH_ENABLED` and a unique `PPE_SESSION_SECRET` before any remote
  access

The `.env.example` file is a template, not production configuration. Never
commit `.env`, paste secrets into chat, or include webhook URLs, database
passwords, NVR credentials, session keys, or user data in screenshots.
The supplied `.env.example` intentionally contains no NVR credentials.

Generate a session key locally:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the generated value into `PPE_SESSION_SECRET`. Keep it private and
different in each environment. If a secret has ever been exposed, rotate it
and the related account/flow credential.

### 3.4 Download the model and start the app

```powershell
.\.venv\Scripts\python.exe scripts\download_model.py
.\.venv\Scripts\python.exe scripts\download_phone_model.py
.\.venv\Scripts\python.exe -m app.main
```

The existing PPE weights are a YOLOv8n model trained for PPE classes. The
additional YOLO26m model is pretrained on COCO and supplies its `cell phone`
class; the PPE model remains responsible for helmet decisions. The phone
weights are downloaded from Ultralytics the first time the download script is
run and saved under `models\`, which is excluded from source control. Check
the model and Ultralytics license terms before commercial redistribution.
Phone detections are associated with tracked people and confirmed over multiple
frames on all cameras.

Arc flash suit is included by default only for the MCC camera; other cameras
can still select it in the camera editor. Seed synchronization adds the MCC
requirement to that existing camera while preserving other PPE and
operator-managed stream settings. The active PPE weights must include an
`Arc Flash Suit` class; in `explicit_negative` mode they also need a
`NO-Arc Flash Suit` / `no_arc_flash_suit` class to report missing suits. The
current `best.pt` in this workspace does not include either suit class, so
affected status remains **unknown** (no false missing-suit alert) until
compatible weights are trained and installed. Use reviewed, validated model
weights before relying on suit alerts.

### Optional employee face dataset import

#### Architecture

The existing PPE detector and violation creation path remain unchanged; face
work is enqueued after a violation is stored and handled independently:

```mermaid
flowchart LR
  CAM[Camera PPE detection] -->|eligible violation + bbox| DB[(PostgreSQL)]
  DB -->|persistent async job| WORKER[Face worker]
  WORKER --> CROP[Person crop + same-track buffered frames]
  CROP --> YOLO[Local YOLOv8n-face: detect and crop]
  YOLO --> INSIGHT[InsightFace buffalo_l: align and ArcFace embed]
  INSIGHT --> GALLERY[Consent-filtered encrypted employee gallery]
  INSIGHT --> RESULT[(Encrypted match result + audit metadata)]
  UI[Authorized Face Match UI] --> API[FastAPI role-checked API]
  API --> DB
  API --> GALLERY
  HIST[Historian backfill jobs] --> DB
  UPLOAD[Manual historian/ad-hoc upload] --> API
```

Only `no_helmet`, `no_arc_flash_suit`, and `phone_detected` are eligible.
Results are suggestions for a human reviewer; a face-module failure cannot
block PPE detection or violation creation.

#### Face Match API

FastAPI publishes the complete request/response schemas at `/openapi.json`.
The face routes require the existing Manager or Admin role; mutating requests
also use the existing CSRF protection.

- Enrollment: `POST /api/face/enrollments/upload` (multipart employee fields,
  consent attestation, and photo); `POST /api/face/import-upload` for ZIP
  imports; `GET /api/face/enrollments` to list active enrollments.
- Results: `GET /api/face/matches` (also `/api/face-matches`) with filters and
  pagination; `GET /api/face-matches/{violation_id}` for details.
- Review: `POST /api/face-matches/{violation_id}/review` accepts
  `decision` (`Confirmed`, `Rejected`, `Reassigned`, or `Unknown`), optional
  `person_id`, `comment`, and `add_crop_embedding`. Explicit recomputation is
  `POST /api/face-matches/{violation_id}/rerun`.
- Historian: `POST /api/face/historian-jobs` accepts `date_from`, `date_to`,
  optional `camera_ids`, and eligible `violation_types`; job status and
  pause/resume/cancel actions use `/api/face/historian-jobs/{job_id}`.
- Ad-hoc search: `POST /api/face/search` accepts a multipart `image` and
  returns top candidates without creating a violation or match record.

Face recognition is disabled by default. The configured
InsightFace `buffalo_l` backend uses its local SCRFD detector, landmark
alignment, and 512-dimensional ArcFace embeddings with ONNX Runtime. The
legacy OpenCV YuNet/SFace backend uses the local Ultralytics YOLO face
detector. Live violations,
historian backfills, manual image searches, and employee-photo enrollment all
use this analyzer. Review the detector, InsightFace, and model-pack terms and
confirm organizational authorization before enabling model provisioning.
Images and embeddings are processed locally; do not send real employee images
to external services.

When using the legacy OpenCV backend, place the authorized `yolov8n-face.pt` weights at
`PPE_FACE_DETECTOR_MODEL_PATH` (default
`models\face\yolov8n-face.pt`). The detector uses `imgsz=1280` and `conf=0.15`
by default; configure `PPE_FACE_DETECTOR_IMAGE_SIZE`,
`PPE_FACE_DETECTOR_CONFIDENCE`, and `PPE_FACE_DETECTOR_CROP_PADDING` for the
local installation. The app deliberately does not download this detector
weights file automatically. If it is missing or cannot be loaded, matching
reports a model error rather than silently switching detection methods.

After reviewing and accepting the terms, set
`PPE_FACE_LICENSE_ACCEPTED=true` in the private `.env` and provision the
InsightFace model pack:

```powershell
.\.venv\Scripts\python.exe scripts\download_insightface_model.py
```

Provisioning requires the InsightFace and ONNX Runtime packages listed in
`requirements.txt`; the script checks the explicit local acceptance flag and
downloads the model pack under
`<PPE_FACE_MODEL_DIR>\models\buffalo_l` (default:
`models\face\models\buffalo_l`). Provisioning alone does not enable matching.
GPU execution is optional; CPU inference is supported.

The configured dataset path (`PPE_FACE_DATASET_PATH`, default `dataset`)
supports the employee roster workbook and employee photos in the local
dataset tree. A non-example `employees*.xlsx` or `roster*.xlsx` workbook is
preferred over sample/template manifests. See [Dataset Guide](#dataset-guide)
for the supported roster and photo-folder layout and the
validate/import/calibrate/pilot command sequence.

Employee folders should be named `<EMPLOYEE_CODE>_<Full Name>` and contain
JPG, JPEG, or PNG photos. Use one manifest row per photo, with
`employee_code`, `full_name`, `department`, `image_path`, `consent_recorded`,
and `consent_reference` columns. Image paths may be relative to the manifest,
absolute paths that resolve inside the dataset root, or a unique image
basename (including extensionless references) within the dataset tree.
Ambiguous, outside-root, sample, and test/incident image copies are rejected
rather than silently enrolled. Optional `consent_basis` and
`consent_date` columns retain the recorded consent source and date. The Excel
manifest reader is included in the optional face install.

Folder-only imports require a documented bulk-consent confirmation. CSV or
Excel imports require `consent_recorded=true` for each employee; alternatively
pass the confirming person's name, consent date, and internal reference to
record a bulk consent basis. The importer
blocks unconsented rows, hashes images for idempotency, encrypts accepted
embeddings, and writes a private per-person report to
`PPE_FACE_REPORT_DIR`. Original photos are not copied unless
`PPE_FACE_KEEP_PHOTOS=true`.

Generate a separate 32-byte AES key locally and put its URL-safe base64 value
in `PPE_FACE_ENCRYPTION_KEY`:

```powershell
.\.venv\Scripts\python.exe -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Keep that key in the approved local secret store; losing it makes stored
embeddings unreadable. With the authorized model provisioned, run a folder
import with explicit bulk consent:

```powershell
.\.venv\Scripts\python.exe scripts\import_face_dataset.py `
  --consent-name "Safety Manager" --consent-date 2026-10-26 `
  --consent-reference "approved-consent-record"
```

For a CSV that has `consent_recorded=true` for every row, the consent flags
can provide the recorded basis without bulk-consent arguments. The script
prints counts and a private report location, never image paths. It also flags
cross-employee embedding similarities and within-employee outliers for review;
it does not remove flagged records.

### Offline face threshold calibration

Calibration uses the same authorized local model and consented primary
dataset. Leave-one-out results are estimates from enrolled photos, not camera
accuracy claims. For incident evaluation, provide real, consented crops grouped by camera,
violation type, and identity, for example
`CAM01\no_helmet\E001\*.jpg` and
`CAM01\no_helmet\not_in_dataset\*.jpg`. Runtime face matching is limited to
`no_helmet`, `no_arc_flash_suit`, and `phone_detected`; `no_vest` is excluded.
The evaluation tool also accepts legacy label aliases and reports normalized
types. Each known label must match an enrolled employee code.
`not_in_dataset` crops are evaluation-only and must never be added to the
enrollment dataset.

```powershell
.\.venv\Scripts\python.exe scripts\calibrate_face_thresholds.py `
  --incident-crops dataset\face_incident_calibration `
  --target-far 0.001
```

Pass the same bulk-consent arguments as the import command when using folder
references without per-row CSV consent records. The report includes per-camera
genuine/impostor score counts, empirical true/false accept and not-identified
rates, recommended strict and suggestion thresholds, and a score histogram
CSV. Incident reports provide true-accept, false-accept, and not-identified
rates per violation type and per camera, as well as overall rates per type.
They also report the observed quality-score distribution to support optional
additional per-type floors configured with
`PPE_FACE_QUALITY_MIN_BY_VIOLATION` (a JSON map from type to 0â€“1 minimum).
These floors only reject lower-quality faces; do not lower match thresholds
for difficult images. At the default 0.1% target, at least 1,000 impostor score samples are
needed even to satisfy the tool's minimum sample-count check; this is not a
guarantee of statistical confidence. Thresholds are recommendations only and
do not modify application configuration. Do not claim performance or enable
identification until representative, consented footage from the actual
cameras has been evaluated and reviewed.

For a flat image folder, create a blank manifest, fill the camera, violation
type, and identity for every image, then pass the CSV as `--incident-crops`:

```powershell
.\.venv\Scripts\python.exe scripts\create_face_calibration_manifest.py `
  --images dataset\feedback\confirmed\images `
  --output dataset\feedback\confirmed\images\face_calibration_labels.csv
```

Use the enrolled `employee_code` as `employee_id`, or `not_in_dataset` for
unknown people. The manifest parser rejects missing labels, unknown employee
IDs, unsupported image paths, and paths that escape the manifest directory.
Keep `no_vest` out of this identity evaluation: it is not an eligible face
matching violation.

When `PPE_FACE_ENABLED=true`, the application queues matching asynchronously
when each eligible violation is created, regardless of its review status.
Eligibility is limited to `no_helmet`, `no_arc_flash_suit`, and
`phone_detected`; `no_vest` is never queued. A status change does not trigger
reprocessing. Only an explicit rerun does. The pipeline uses the person's
violation bounding box plus buffered frames of the same camera/track, checks
face quality, and records `FACE_NOT_VISIBLE` when no usable face is found. For
missing-helmet incidents, it additionally searches an upper-person/head region
to improve face detection in full-body CCTV crops; any accepted face is aligned,
embedded, and compared with the consented employee gallery. It never guesses an
identity.
Face recognition is not run or displayed on the live wall. A bounded,
short-lived person-crop buffer is keyed by camera and ByteTrack ID; face
detection and matching are not performed on normal frames, and buffered crops
are not stored on disk. The incident gallery shows match availability to
operators; only Admin/Safety Manager users can view names, candidates, crops,
and review controls. The independent
worker uses a dedicated single-thread executor (lowered to below-normal
priority on Windows) and bounded capture/enqueue buffers, so alert creation
and delivery do not wait for recognition. The persisted pending/processing
queue is capped by `PPE_FACE_JOB_QUEUE_MAX` (default 500); at capacity, new
recognition work is deferred and recovered by the queue sweep. The example
thresholds (`PPE_FACE_MATCH_THRESHOLD=0.60`,
`PPE_FACE_SUGGEST_THRESHOLD=0.45`) are provisional, not calibrated; keep
matching disabled until representative consented site data has been evaluated
and reviewed. `PPE_FACE_MATCH_STRATEGY` may be `max` or `top_k_mean`,
and calibration uses the configured strategy. CPU provider and processing
time are recorded with the result; this workspace has not been measured on
production cameras.
Run `scripts\benchmark_face_pipeline.py` for model-free queue/crop/matching
microbenchmarks, reported with violation-type and camera labels. After model
authorization and provisioning, it can measure local inference on a synthetic
or explicitly consented sample; YOLO face detection, InsightFace alignment,
and ArcFace embedding are measured together, so the tool reports combined
latency.
`scripts\benchmark_live_impact.py`
compares the PPE detector and annotated-tile encoding on synthetic paced frames
or a local recorded video while a bounded, simulated face-job worker runs; it
reports camera-to-detection and camera-to-tile-ready latency plus simulated
job counts by violation type. It does not measure WebSocket delivery or a real
camera and is not a production performance claim.
Match and review records are automatically
removed after `PPE_FACE_MATCH_RETENTION_DAYS` (default 365; set `0` to disable
automatic expiry). Retained enrollment photos are encrypted; configure
`PPE_FACE_KEEP_PHOTOS` according to the approved retention policy.
For `MATCHED` and `NEEDS_REVIEW` results, the selected face crop is encrypted
in the match record and available only to Admin/Safety Manager reviewers.
Below-threshold crops are not retained separately. Suggested crops remain
encrypted while awaiting human review; rejecting a match, deleting/withdrawing
its enrollment, or match-record expiry removes the stored crop. Before enabling
this feature, obtain an approved written policy, provide employee notice,
document consent, and complete legal review under applicable law (including
India's DPDP Act 2023 where applicable). A match is decision support only;
require human review before any disciplinary action.
`PPE_FACE_INCLUDE_IN_EMAIL=false` keeps the existing Power Automate schema and
message unchanged; when enabled, an email receives optional match fields only
for a `MATCHED` result, with pending-review wording until a reviewer
confirms or reassigns it.

The application loads `.env`, applies pending Alembic migrations, synchronizes
seed cameras/area groups, starts camera workers, and starts the alert outbox
worker. A ready server prints `STARTUP COMPLETE - application is ready.`

Open <http://127.0.0.1:8080/>. Stop the foreground server with Ctrl+C.

Inference device selection defaults to `PPE_DEVICE=auto`: a CUDA-enabled
PyTorch installation uses the first NVIDIA GPU, otherwise inference falls back
to CPU. Set `PPE_DEVICE=cpu` to force CPU. If the installed PyTorch package is
CPU-only, install a CUDA-enabled wheel compatible with your Python and NVIDIA
driver. On supported Windows NVIDIA hosts, `requirements.txt` selects the
CUDA 13 PyTorch wheels. To repair an existing environment that already has the
CPU-only PyTorch wheel, run:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps --force-reinstall torch==2.14.1+cu130 torchvision==0.29.1+cu130 --index-url https://download.pytorch.org/whl/cu130
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU fallback')"
```

The CUDA PyTorch wheel is large. This accuracy-oriented configuration uses
`PPE_IMG_SIZE=960` and a lower `no_helmet` confidence threshold while
retaining multi-frame confirmation. A higher inference resolution and a
second, medium-sized object detector increase compute; on CPU, expect reduced
camera inference throughput. Measure per-camera latency and dropped-frame
behavior before enabling all cameras. For systems with a supported NVIDIA
GPU, `PPE_DEVICE=auto` uses it when the installed PyTorch build supports CUDA.
On CPU, the app sets PyTorch's inference thread count to four by default;
`PPE_CPU_THREADS` controls this value. Tune it against the host and camera
count to avoid excessive inference-thread oversubscription.
Do not treat a larger model or lower threshold as proven more accurate until
evaluated against representative, labeled footage from these cameras.

### Camera stream and inference settings

Each camera uses one shared configured sub-stream capture for the Live Wall
and detection; it does not silently promote a failed grid feed to the
high-bitrate main stream. RTSP capture uses FFmpeg over TCP. The browser
receives visible tiles over one multiplexed WebSocket, with JPEG frames encoded
once and reused for subscribers. Opening a camera's full-screen viewer opens
its configured main stream on demand; recorded evidence requests the NVR
playback URL built from that main stream. Existing camera rows remain
supported through their legacy `rtsp_url` value. The stream status remains
`CONNECTING` until the first frame arrives, and the watchdog allows the
configured frame-timeout interval for that initial frame before requesting a
reconnect.
For cameras/NVRs that provide a supported HTTP video-preview endpoint, you
can enter that exact, device-documented HTTP stream URL in the sub-stream URL
field; the capture backend uses FFmpeg for either RTSP or HTTP. Do not replace
an RTSP URL with the NVR's web-console URL or assume a universal HTTP path:
preview endpoints and digest-auth support vary by model and firmware. The
main-stream URL used for time-range evidence playback must still be a
Hikvision-compatible RTSP channel; if main-stream playback is unavailable,
the existing buffered sub-stream evidence fallback is used. Switching
transport cannot repair a wrong channel, failed credentials, or an NVR
session limit.

The relevant optional settings and defaults are:

| Setting | Default | Purpose |
| --- | ---: | --- |
| `PPE_DEVICE` | `auto` | Select `auto`, `cuda`, or `cpu`; unavailable CUDA falls back to CPU with a warning. |
| `PPE_DETECT_FPS` | `5` | Maximum scheduled detection rate per camera; overdue frames are skipped. |
| `PPE_CAMERA_FRAME_TIMEOUT_SECONDS` | `10` | Time without a captured frame before the watchdog requests reconnect. |
| `PPE_CAMERA_MAX_SIMULTANEOUS_RECONNECTS` | `4` | Limits concurrent RTSP reconnect attempts; the default helps camera feeds start promptly without opening every feed at once. |
| `PPE_CAMERA_RECONNECT_INITIAL_SECONDS` | `2` | Initial exponential reconnect delay. |
| `PPE_CAMERA_RECONNECT_MAX_SECONDS` | `60` | Maximum reconnect delay. |
| `PPE_TILE_FPS` | `8` | Live Wall frame-rate cap (maximum 10 FPS). |
| `PPE_TILE_WIDTH` | `640` | Maximum Live Wall JPEG width in pixels. |
| `PPE_JPEG_QUALITY` | `70` | JPEG quality for shared Live Wall frames. |
| `PPE_FULLSCREEN_FPS` | `12` | Full-screen main-stream delivery rate (maximum 15 FPS). |
| `PPE_FULLSCREEN_WIDTH` | `1920` | Maximum full-screen JPEG width in pixels. |

`GET /api/cameras` reports each feed as `connecting`, `live`, `failed` (retrying),
or `stopped`, with a password-free error hint when available. `GET /api/health`
includes these states plus per-camera capture/detection FPS, latest-frame age,
reconnect count, and inference duration. The Live Wall shows the current state
and the error hint on the tile.

The COCO phone class detects visible phone objects, not the behavior of using
one. A phone incident is created only after it is associated with the existing
person track and observed across multiple frames. The dashboard and email
therefore say â€œPhone detected near person,â€ not â€œphone use confirmed.â€

The shipped PPE model card reports 68.5% recall on its own validation set;
that score is not a measurement for this site's cameras. The local review
dataset must include confirmed helmet violations and a separate labeled
validation set before model fine-tuning or threshold tuning can be claimed to
improve recall. See the model card's [limitations and
metrics](https://huggingface.co/Hansung-Cho/yolov8-ppe-detection) and the
official [YOLO26 documentation](https://docs.ultralytics.com/models/yolo26/).

For site-specific PPE tuning, gather reviewed examples from the affected
cameras and different distances, lighting, and helmet appearances. Include
confirmed missing-helmet cases as well as false positives, correct the
model-assisted labels, and keep a separate manually labeled validation set:

In the Violations gallery, use **Correct false alert** to distinguish a
non-person detection from a real person whose PPE was recognized incorrectly.
For PPE corrections, select the visible PPE and draw a box around it; for a
non-person detection, the saved crop is labeled as background. These reviewed
corrections are training examples only: they do not update the active model.
Training remains a separate, deliberate step and requires a separate
manually labeled validation set before considering a candidate for deployment.

```powershell
.\.venv\Scripts\python.exe scripts\build_feedback_dataset.py `
  --dataset dataset\feedback --validation-dir dataset\validation
.\.venv\Scripts\python.exe scripts\train.py `
  --data dataset\feedback\data.yaml --base-model models\best.pt `
  --imgsz 960 --device cpu
.\.venv\Scripts\yolo.exe detect val `
  model=runs\ppe_train\ppe_model\weights\best.pt `
  data=dataset\feedback\data.yaml imgsz=960 device=cpu
```

Review per-class recall and precision on the held-out validation data before
promoting a candidate model. Do not use the same images for training and
validation, and do not use automated detection alone for disciplinary
decisions.

### 3.5 Create the first administrator

Enable authentication and use the secret configured above before sharing the
dashboard with other users:

```dotenv
PPE_AUTH_ENABLED=true
PPE_SESSION_SECRET=your-locally-generated-secret
```

Restart the app, then create an administrator in a separate PowerShell
terminal:

```powershell
.\.venv\Scripts\python.exe scripts\create_admin.py admin@your-company.example
```

The script prompts for the password; it is not a command-line argument. Sign
in at the dashboard. Admins can invite users; use a trusted channel to share
single-use invitation links. For remote HTTPS use, set `PPE_COOKIE_SECURE=true`
and have the network/platform administrator review the reverse proxy or
tunnel, access controls, and TLS configuration. Never expose an unauthenticated
app.

## 4. Database upgrades and camera synchronization

Migrations are normally applied automatically when the app starts. To inspect
the current Alembic revision:

```powershell
.\.venv\Scripts\alembic.exe current
```

To apply migrations explicitly (for example, during a maintenance window):

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

The current upgrade maps stored legacy alert channels to the Power Automate
channel so existing rules continue through the sole configured provider.
Review migrated rule recipients and test delivery before relying on each rule.
Historical shift-report database tables from older revisions are retained for
data preservation, but the application no longer reads or writes them.

Seed camera configuration lives in `app/config.py`; it supplies defaults for
cameras missing from the database. Existing camera stream URLs edited in the
Cameras tab are operator-managed and are not overwritten during startup or
**Sync Default Cameras**. If an existing seeded camera still has the same
RTSP host, port, channel path, and query as its configured seed, synchronization
refreshes its credentials from the local environment without changing the
operator-selected endpoint. Camera names, custom cameras, PPE options, other
stream URLs, and existing incident history remain database data; use a database
backup before bulk edits.

The default setup uses the configured NVR channel feeds only. Legacy `D1`â€“`D20`
direct-camera feeds are retired and disabled on synchronization; their records
and incident history are retained.

Check database connectivity without starting camera workers:

```powershell
.\.venv\Scripts\python.exe scripts\check_db_connection.py
```

## 5. Configure Power Automate email delivery

Power Automate is the only email delivery integration in this build. The app
needs a flow's signed HTTPS URL; Microsoft 365 mailbox authentication is held
by the flow's Outlook connector, not in the app.

### 5.1 Create the flow

1. Sign in at [Power Automate](https://make.powerautomate.com/) with the
   organization account that owns the flow.
2. Create an **Automated cloud flow** with **When an HTTP request is received**.
3. Use the signed URL mode accepted by the tenant. This app sends the URL as
   its authentication; it does not obtain Microsoft Entra OAuth tokens. If
   the tenant requires tenant-authenticated OAuth instead of signed-URL
   access, stop here and have the integration extended for that auth mode.
4. In **Request Body JSON Schema**, use this schema. Image properties are
   optional so the dashboard's image-free test message is accepted:

   ```json
   {
     "type": "object",
     "properties": {
       "event_type": {"type": "string"},
       "subject": {"type": "string"},
       "body": {"type": "string"},
       "camera_name": {"type": "string"},
       "zone": {"type": "string"},
       "violation": {"type": "string"},
       "occurred_at": {"type": "string"},
       "recipients": {"type": "array", "items": {"type": "string"}},
       "image_name": {"type": "string"},
       "image_content_type": {"type": "string"},
       "image_content_bytes": {"type": "string"},
       "image_count": {"type": "integer"},
       "images": {
         "type": "array",
         "items": {
           "type": "object",
           "properties": {
             "image_name": {"type": "string"},
             "image_content_type": {"type": "string"},
             "image_content_bytes": {"type": "string"},
             "camera_name": {"type": "string"},
             "zone": {"type": "string"},
             "violation": {"type": "string"},
             "occurred_at": {"type": "string"}
           }
         }
       }
     },
     "required": [
       "event_type", "subject", "body", "camera_name", "zone",
       "violation", "occurred_at", "recipients"
     ]
   }
   ```

   Real incident requests use `event_type: "ppe_violation"` and include either
   `violation: "Missing Helmet"` or `violation: "Phone Detected Near Person"`.
   Both include the annotated JPEG as base64 in `image_content_bytes`;
   `image_name` distinguishes the image type. Dashboard test messages use
   `event_type: "test"` and do not include an image.
   Shift digests use `event_type: "ppe_shift_digest"` and provide all
   missing-helmet and phone images in the `images` array.
   The dashboard's manual Power Automate action sends only the latest available
   helmet or phone evidence image as a single `ppe_violation` event.
5. Add **Office 365 Outlook â†’ Send an email (V2)** and create its connection
   using the approved sender mailbox:
   - **To**: expression `join(triggerBody()?['recipients'], ';')`
   - **Subject**: `subject` from the trigger
   - **Body**: `body` from the trigger
   - **Attachments**: add one attachment with **Name** set to
     `image_name` and **ContentBytes** set to `image_content_bytes`.
     In the `ppe_shift_digest` branch, add an **Apply to each** over
     `images` and add each item's `image_name` and `image_content_bytes` as
     an attachment to the single digest email.
     Put this incident email action in the `event_type` equals
     `ppe_violation` branch of a Condition. In the other branch, add a
     separate **Send an email (V2)** action for the test message without an
     attachment. Incident emails must include the attached JPEG. The app
     won't send an incident without a readable evidence image.
6. Add a **Response** action after the email action. Return status code `200`
   only after the email action succeeds. Return a non-2xx status on failure.
   Save the flow and turn it on.
7. Copy the full generated HTTPS URL into `.env`:

   ```dotenv
   PPE_POWER_AUTOMATE_WEBHOOK_URL=paste-the-private-flow-url-here
   ```

   Treat the URL, including its query string, like a password. Do not put it
   in source control, screenshots, chat, or ordinary tickets. Restrict flow
   ownership/edit access. Regenerate the URL if it is exposed.
8. Restart the app so it loads the new setting.

The HTTP-trigger availability, authentication options, connector policies,
and licensing depend on the Microsoft tenant. Have the Power Platform
administrator approve the trigger/authentication pattern and sending mailbox.
See Microsoft's [HTTP trigger authentication
guide](https://learn.microsoft.com/en-us/power-automate/oauth-authentication),
[Power Automate licensing
guide](https://learn.microsoft.com/en-us/power-platform/admin/power-automate-licensing/types),
and [Office 365 Outlook connector
reference](https://learn.microsoft.com/en-us/connectors/office365/).

### 5.2 Test the flow from the dashboard

1. Sign in as a manager or administrator.
2. Open **Alert Delivery**, enter a test recipient that you are authorized to
   use, and select **Send Test Email**.
3. The dashboard reports whether the app received a successful response from
   the flow. Confirm both the flow run history and the destination mailbox.
4. If the send fails, see section 9 before changing production rules.

The test request follows the app-to-flow path and calls the real flow if the
URL is configured; it is **not** a mocked delivery. It sends a clearly marked
test message and should only target an approved test mailbox.

### 5.3 Configure incident rules

In **Alert Rules**, create or edit a rule:

- Choose the camera/zone filters, violation type (**Missing Helmet** or
  **Phone Detected Near Person**), and cooldown.
- Enable **Power Automate Email**.
- Enter one or more destination addresses in **Email Recipients**, separated
  by commas.
- Enable the rule and save it.

For a controlled end-to-end incident test, use an authorized camera and
supervised test conditions. Confirm the incident is saved, the rule matches,
an outbox row is enqueued, the flow run succeeds, and the email arrives.
Avoid triggering real PPE violations or collecting identifiable footage
without site authorization.

The dispatcher marks an attempt delivered when the HTTP response is 2xx. A
successful flow response proves the flow's email action completed as
configured; it does not independently prove final mailbox delivery. Check
Power Automate run history and the recipient mailbox.

## 6. Run persistently on Windows

Once `.venv`, `.env`, PostgreSQL, and the model are ready, install the
per-user task from PowerShell in the project root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_background_task.ps1
```

The task is named **INGPCE Dashboard and Cloudflare Tunnel** in Task Scheduler
Library's root. It starts at sign-in and runs the local dashboard and
Cloudflare quick tunnel watchdog. The PC must stay powered on and the Windows
account must stay signed in. A quick-tunnel URL can change after restart; it
is not a permanent hostname.

Check status and the latest tunnel URL:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\background_status.ps1
```

Logs are under `%LOCALAPPDATA%\INGPCE\logs`. To reload `.env`, restart the
scheduled task:

```powershell
Stop-ScheduledTask -TaskName "INGPCE Dashboard and Cloudflare Tunnel"
Start-ScheduledTask -TaskName "INGPCE Dashboard and Cloudflare Tunnel"
```

Check health locally:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/health
```

Keep authentication enabled and validate remote access before sharing any
tunnel URL. For a foreground run instead, stop the scheduled task first and
use the command in section 3.4 to avoid a port conflict.

## 7. End-to-end test and acceptance checklist

### Automated checks

From the project root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\ruff.exe check --select E4,E7,E9,F app tests migrations
.\.venv\Scripts\python.exe -m compileall -q app migrations tests
```

The suite includes API, unit, and Playwright browser coverage. Tests mock
database and external HTTP calls unless a test explicitly says otherwise;
automated tests do not verify physical cameras, a real flow, or email
delivery. Run the manual Power Automate test above after configuring the real
tenant connection.

### Manual Live Wall test

Run the following checks first at `http://127.0.0.1:8080`, then repeat the
browser checks through the Cloudflare quick tunnel with authentication enabled:

1. Enable one camera. Confirm a tile appears within a few seconds, opens
   full-screen on the main stream, and recovers from an unplug/reconnect
   without a rapid reconnect loop.
2. Enable four cameras and leave the wall open for at least 30 minutes. Check
   that tiles continue updating, no tile is starved by inference, and
   filtering the wall does not leave stale WebSocket subscriptions.
3. Enable all configured cameras. Check detection and incident creation on
   approved sample footage, and confirm a known NVR interval plays back and
   evidence clips are written. Verify the RTSP connection/session count at
   the NVR: one shared sub-stream per managed camera, plus only on-demand
   full-screen and NVR playback connections.
4. In Task Manager (or `nvidia-smi` where installed), record CPU and GPU use.
   Use **STREAM DIAGNOSTICS** for capture FPS, detection FPS, frame age,
   reconnect count, and inference milliseconds. Measure first-frame time and
   end-to-end latency with a visible clock or time-coded scene.

Record actual before/after results on the host; do not infer physical-camera
performance from unit tests:

| Scenario | First frame | End-to-end latency | Capture FPS | Detection FPS | CPU | GPU |
| --- | --- | --- | --- | --- | --- | --- |
| Before, one/four/all cameras | Measure | Measure | Measure | Measure | Measure | Measure |
| After, one/four/all cameras | Measure | Measure | Measure | Measure | Measure | Measure |

### Pre-handover acceptance

- [ ] The documented Python version and dependencies are installed.
- [ ] `.env` exists locally, contains current credentials, and is not committed.
- [ ] Database connectivity succeeds and startup reaches ready state.
- [ ] Alembic is at `head`; seed cameras/area groups look correct.
- [ ] Camera streams are reviewed in the live wall; playback is checked for a
      known recorded interval.
- [ ] Incident detection is checked against approved sample footage.
- [ ] Authentication is enabled and admin login/invite process is known.
- [ ] The flow is on, the signed URL is private, and its owner/backup owner
      are recorded in the organization's secure secrets inventory.
- [ ] A real test email is confirmed in both flow run history and mailbox.
- [ ] Supervised missing-helmet and phone-detected incident emails arrive
      with their annotated JPEGs attached.
- [ ] Helmet and phone alert rule recipients, camera/zone filters, and
      cooldowns are reviewed.
- [ ] Outbox retries, dead-letter monitoring, application logs, and DB backups
      have an assigned operational owner.
- [ ] A second maintainer can restart the task, find logs, and restore a
      database backup using the steps below.
- [ ] Camera footage retention, access, and privacy controls are approved.

## 8. Backup, restore, and change management

Take a database backup before upgrades, configuration changes that affect
seed data, or handover. Use PostgreSQL client tools from PowerShell:

```powershell
pg_dump --host localhost --port 5432 --username ppe_user --format custom `
  --file .\backups\ppe_compliance.dump ppe_compliance
```

The `backups` folder must exist and backup files must be protected as
sensitive production data. Supply credentials using the site's approved
PostgreSQL credential mechanism; do not put passwords directly in command
history.

Restore to a separate recovery database first:

```powershell
createdb --host localhost --port 5432 --username ppe_user ppe_compliance_restore
pg_restore --host localhost --port 5432 --username ppe_user `
  --dbname ppe_compliance_restore .\backups\ppe_compliance.dump
```

Validate the recovered database and application before planning a production
cutover. Coordinate retention, RPO/RTO, encrypted off-host copies, and restore
drills with the database owner. Keep `.env`, model weights, evidence folders,
and database backups in approved protected storage; they are not interchangeable
backup sources.

For each release, document the version/commit, migration revision, changed
environment keys, test results, backup location, operator, and rollback
decision. Migrations are forward-only where data cannot be mapped safely
backward; restore the pre-upgrade database backup for a rollback.

## 9. Operations and troubleshooting

### App does not open

1. Check the scheduled task with `Get-ScheduledTask -TaskName
   "INGPCE Dashboard and Cloudflare Tunnel"`.
2. Run `background_status.ps1` and inspect `%LOCALAPPDATA%\INGPCE\logs`.
3. Check `Invoke-RestMethod http://127.0.0.1:8080/api/health`.
4. Verify PostgreSQL, `.env`, model path, Python environment, and port 8080.
5. If the new process fails, use its full startup traceback before restarting
   repeatedly.

### Camera is offline or playback is empty

- Confirm NVR reachability, RTSP port, credentials, camera channel, and that
  the requested interval was recorded.
- Use the Cameras tab to edit a stream or reconnect a camera.
- Playback is available only where the recorder supports the configured
  Hikvision-compatible playback request. The selected date/time uses the
  camera/NVR local timezone.
- Never put credentials into logs, screenshots, or support tickets.

### Power Automate test or incident alert fails

- Verify the environment key exists without printing its value:
  `Select-String -Path .env -Pattern '^PPE_POWER_AUTOMATE_WEBHOOK_URL='`
- Check that the full current HTTPS URL is in `.env`, the flow is saved/on,
  and the app was restarted after changing it.
- Review Power Automate run history. No run generally means the app cannot
  reach the URL or the URL is stale; a failed Outlook action points to the
  Outlook connection, recipient, tenant policy, or flow configuration.
- The test endpoint requires a valid recipient and manager/admin access.
- Confirm the alert rule is enabled, has Power Automate Email selected, has
  recipients, and its match/cooldown conditions are satisfied.
- The app retries non-2xx responses. Inspect PostgreSQL `alert_outbox` and
  `alert_dispatch_log` with an administrator; dead jobs need an operational
  decision before retrying or changing a rule.
- Never paste the signed URL or raw error output that might contain it into a
  ticket or chat. Rotate the URL if it may have been disclosed.

### Useful diagnostics

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/health
.\.venv\Scripts\python.exe scripts\check_db_connection.py
.\.venv\Scripts\alembic.exe current
```

For API documentation while the service is running, use
<http://127.0.0.1:8080/docs> only on a trusted local network.

## 10. Dashboard areas

- **Live Wall** â€” current camera feeds and live event rail.
- **Playback** â€” select a camera and recorded interval.
- **Incidents** â€” filter and review stored evidence; authorized administrators
  can use the retention cleanup action.
- **Analytics** â€” incident trends and violation breakdowns.
- **Cameras** â€” camera/area management and seed synchronization.
- **Camera Status** â€” uptime, timeline, and connection transitions.
- **Alert Delivery** â€” manager/admin-only real-flow test send.
- **Alert Rules** â€” rule-based Power Automate email configuration.

Camera/incident viewing, exports, and user access must comply with site
privacy, safety, and retention requirements. Automated detections are
decision support; human review is required before consequential action.

## 11. Handover record

Complete this section in the organization's approved internal handover
location. Do not record secrets or the signed URL here.

| Item | Owner / location |
|---|---|
| Application maintainer | |
| Database owner and backup schedule | |
| Power Automate flow name and environment | |
| Flow owner and backup owner | |
| Secure secrets-vault reference | |
| Camera/NVR network contact | |
| Alert rule reviewer | |
| Monitoring/log review owner | |
| Incident response and restore runbook | |
| Last successful test email and date | |

## 12. Scope notes

- This build routes incident email only through Power Automate.
- Power Automate sends immediate helmet/phone incident emails and one
  image-only helmet/phone digest at the end of each completed shift.
- The flow endpoint uses a secret signed URL and does not use app-managed
  Microsoft Entra OAuth.
- Power Automate owns actual mailbox delivery. App success is based on the
  configured flow's HTTP response.
- Review the detector model's license and accuracy against representative
  site footage before operational use.

## 13. Violation-to-employee identification & demo

When `PPE_FACE_ENABLED=true`, confirmed helmet/phone violations are linked to
a named employee end to end: the face-recognition worker collects the best
faces of the violating person's track across frames, averages their ArcFace
embeddings (`PPE_FACE_IDENTIFY_BEST_N`, `PPE_FACE_IDENTIFY_AGGREGATION`), and
matches them against enrolled employees with a threshold + margin decision
(`MATCHED` / `NEEDS_REVIEW` / `UNKNOWN` / `FACE_NOT_VISIBLE`). Borderline and
unknown incidents appear in the dashboard review queue, where a supervisor
confirms, reassigns, or enrolls the person â€” every action is audit-logged.

A dependency-free proof of concept (simulation mode, no cameras or models
needed), the employee seed script, an identification evaluation harness,
Docker setup, and the full configuration reference live in
[demo/README.md](demo/README.md). Tests:
`python -m unittest tests.unit.test_track_identity tests.unit.test_debounce
tests.unit.test_association tests.integration.test_demo_simulation`


---

# Consolidated Maintainer Reference


## Documentation and System Overview

This directory is the maintainer-facing documentation set for the INGPCE
industrial safety monitoring application.

## Start here

| Document | Use it for |
|---|---|
| [Architecture](./ARCHITECTURE.md) | Understanding the end-to-end system and module boundaries |
| [Development](./DEVELOPMENT.md) | Setting up a workstation, changing code, and running tests |
| [Operations](./OPERATIONS.md) | Starting, monitoring, recovering, and maintaining a deployment |
| [Data and models](#dataset-guide) | PPE feedback, face enrollment, calibration, and model evaluation |
| [Face dataset guide](#face-dataset-guide) | Consent, validation, import, and face-specific data handling |

The root [README](../README.md) remains the product overview and full setup
reference. These documents explain how the pieces fit together and how to
operate them safely.

## System at a glance

```text
RTSP/NVR cameras
      |
      v
CameraManager -> RTSP workers -> PPEDetector -> ComplianceEngine
      |                                      |
      |                                      +--> snapshots and evidence clips
      |                                      +--> PostgreSQL violations
      |                                      +--> WebSocket live wall
      |
      +--> AlertManager -> durable outbox -> Power Automate -> email
      |
      +--> FaceRecognitionWorker (optional, asynchronous)

Browser dashboard <-> FastAPI routes <-> PostgreSQL and local evidence
```

## Safety boundaries

- Detection is decision support, not an automatic disciplinary decision.
- Face matching is disabled by default and requires authorization, consent,
  encryption keys, and human review.
- Camera credentials, database credentials, webhook URLs, session secrets,
  face encryption keys, employee photos, and evidence are local secrets/data.
- Never commit `.env`, generated evidence, model weights, face data, or runtime
  logs.

## Architecture and Code Map

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

## Development Guide

## Workstation setup

From PowerShell at the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

Fill `.env` with local PostgreSQL, camera/NVR, model, authentication, and
Power Automate settings. Never commit `.env`.

## Run the application

```powershell
.\.venv\Scripts\python.exe -m app.main
```

The supplied `run_server.bat` is the Windows launcher.
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

## Operations Runbook

## Start and stop

Start:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

The process is ready when startup prints `STARTUP COMPLETE` and
`http://127.0.0.1:8080/api/health` returns HTTP 200.

Stop a known process by PID:

```powershell
Get-Process python
Stop-Process -Id <PID>
```

Do not kill processes by image name on a shared host.

## Health checks

```powershell
Invoke-WebRequest http://127.0.0.1:8080/api/health -UseBasicParsing
```

Review:

- overall API status;
- camera stream state, latest-frame age, reconnect count, and inference time;
- database connectivity and application startup logs;
- alert delivery status in the Alert Delivery tab;
- face readiness only when face matching is intentionally enabled.

## Common recovery actions

### A camera is offline

1. Open Camera Status and identify the camera and time range.
2. Check the camera/NVR network route and RTSP credentials.
3. Check the stream URL in Cameras.
4. Use reconnect from the Live Wall.
5. Avoid changing detection thresholds to compensate for a transport failure.

### Alerts are not arriving

1. Open Alert Delivery and refresh status.
2. Check flow configuration, queued/dead counts, and recent activity.
3. Review the Power Automate flow run history.
4. Verify the webhook URL in the private `.env`.
5. Do not paste the webhook URL into logs, tickets, or chat.

### The dashboard is stale

1. Check `/api/health`.
2. Check the event WebSocket connection indicator.
3. Refresh the browser.
4. Restart the application only after checking the logs for the root cause.

### A model was changed

1. Confirm the file path in `PPE_MODEL_PATH`.
2. Validate the model can load in the project virtual environment.
3. Restart the application.
4. Confirm startup reports the expected model path.
5. Run a supervised camera/evidence check before relying on alerts.

## Storage management

The largest directories are normally `snapshots/`, `evidence/`, `.venv/`, and
`models/`. Before deleting evidence:

1. Confirm the retention policy and incident/audit requirements.
2. Use the supported cleanup operation where available.
3. Check database references and backup requirements.
4. Delete only a resolved, explicit time-bounded scope.

Never delete the whole repository or evidence root recursively as a shortcut.

## Security and privacy

- Keep authentication enabled for any remote access.
- Use a unique session secret and rotate exposed secrets.
- Treat employee face data, evidence, NVR credentials, database credentials,
  and webhook URLs as sensitive.
- Face recognition requires documented consent, an encryption key, and
  authorized reviewer access.
- Automated PPE and face outputs require human review before consequential
  action.

## Change History

## Camera Status filters

The dashboard exposed area-group and camera selectors, but the history API
did not pass those selections to the database query. The corrected endpoint
and parameterized query apply the time range, group ID, and camera ID to the
relevant history data. The dashboard sends the selected IDs directly, resets
filters explicitly, cancels stale requests, debounces changes, and displays
loading, empty, and error states. A successful HTTP response with the wrong
JSON shape now reports a controlled format error instead of crashing while
rendering.

## Analytics dashboard

The previous analytics view consisted of three fixed-size canvas plots without
summary metrics or a selectable reporting window. It now presents four
period-aware summary cards, a responsive hourly activity chart, an incident
classification doughnut and legend, and ranked area/camera hotspot bars.
Reporting periods are 24 hours, 7 days, and 30 days. The charts fill missing
hour buckets with zeros, redraw crisply on resize, and show explicit empty
states. API errors are surfaced in the dashboard. Statistics are derived from
the existing `/api/stats` response and current camera status.

## Shift report architecture

The **Email Alerts** tab configures persisted report settings through
manager-authorized APIs. Defaults are Shift A 06:00â€“14:00, Shift B 14:00â€“22:00,
and Shift C 22:00â€“06:00 in the configured IANA time zone. Global and
per-shift recipient lists, enable switches, and start/end times can be
changed and take effect without a restart.

The app's async scheduler reloads settings while running, creates due reports
for completed shifts (including empty shifts), and catches up within the
configured lookback on restart. Database report identity prevents duplicate
shift sends; claims are coordinated across workers, and each SMTP delivery
attempt is logged with retry/backoff state. Reports include camera status,
shift violations and alerts, connectivity transitions, and a previous-shift
violation comparison. Email is multipart HTML with a plain-text alternative.
SMTP secrets are read only from environment configuration.

The additive Alembic migration adds settings, report idempotency/status, and
attempt-log storage. Its SQL can be generated offline; applying the migration
or attempting rollback against a production database is outside this change's
verification.

## Environment configuration

Required to send mail:

- `PPE_SMTP_HOST`
- `PPE_SMTP_PORT` (default `587`)
- `PPE_SMTP_USER`
- `PPE_SMTP_PASSWORD`
- `PPE_ALERT_EMAIL_FROM` (defaults to the SMTP user)

Scheduler controls:

- `PPE_SHIFT_REPORT_TIMEZONE` (empty means detect the server's local zone)
- `PPE_SHIFT_REPORT_POLL_SECONDS` (default `30`)
- `PPE_SHIFT_REPORT_CATCHUP_HOURS` (default `72`)
- `PPE_SHIFT_REPORT_MAX_ATTEMPTS` (default `5`)

Configure recipients, shift windows, and alert enablement in **Email Alerts**.
Use **Send Test Email** in a safe environment to verify SMTP. Do not commit
credentials or test against production recipients.

## Manual verification

1. Configure `.env` and apply database migrations in a disposable PostgreSQL
   database.
2. Sign in as a manager, open **Email Alerts**, edit recipients and shift
   windows, save, reload, and confirm the values persisted.
3. Send a test email to a controlled mailbox and inspect the delivery log.
4. Set up camera status samples/events in the disposable database; combine
   window, group, and camera filters, test an empty selection, then clear all
   filters.
5. Run the Python test suite and install Chromium for the mock-backed browser
   workflow:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m playwright install chromium
   .\.venv\Scripts\python.exe -m unittest discover -s tests -v
   ```

## Verification limits

The browser workflow uses mocked API responses and never reaches the
configured database or SMTP server. It verifies camera filtering and the
settings UI save/reload flow in Chromium; it does not verify browser flows for
authentication, live camera streams, Firefox/WebKit/mobile layouts, or live
SMTP delivery. No migration has been applied to a real database. See
the QA section in this README for test counts and the deployment verdict.

## Dataset Guide

Face images and biometric data are sensitive. Keep real images on the
authorized INGPCE host, use only consented in-house photos, and never commit
or send them to chat or external services. The directories
`dataset\faces\` and `dataset\face_test_crops\` are Git-ignored; only their
`.gitkeep` placeholders are tracked.

## Recommended folder and Excel layout

Keep the employee roster workbook and employee photo folders under the
configured dataset root (`dataset` by default). For the local roster layout:

```text
dataset\
  employees.xlsx.xlsx
  faces\
    Image.jpg
    Image2.jpg
  faces_manifest.example.xlsx
  face_test_crops\
    (test-only image copies; never enrolled)
```

The importer selects the non-example employee roster workbook ahead of
sample/template workbooks. Use one row per employee photo, with
`employee_code`, `full_name`, `department`, `image_path`, and
`consent_recorded` columns. An extensionless `image_path` is supported when
exactly one corresponding employee-gallery image exists. Test, feedback,
violation, and historian folders are not used to resolve such references.
If the same basename is present in multiple eligible employee folders, the
import is stopped and the manifest must be corrected to include an explicit
relative path. Absolute paths are accepted only when they resolve inside the
configured dataset root; paths outside it and traversal paths are rejected.

```text
employee_code | full_name | department | image_path | consent_recorded | consent_reference
E001          | Jane Doe  | Operations | faces\image01.jpg | true | CONSENT-REF-001
```

Replace the sample rows in the actual employee roster with valid
consented-employee records; the example workbook is never used as a roster.
`consent_recorded` must be true. `consent_reference` and `consent_date` retain
the internal consent record. Alternatively, use an `employees.csv` roster
with the same columns. Folder-only imports (no roster) are also supported
and use the explicit bulk-consent confirmation.
`openpyxl==3.1.5` is installed with the application to read Excel.

### Enroll from the dashboard

An authenticated Admin or Manager can open **Face Matches â†’ Enroll Employee**,
enter the employee ID, name, and department, select a JPG/PNG photo, and
confirm that the employee notice and documented consent are in place. If
`employees.xlsx` does not exist, the form creates it. The uploaded photo is
checked for one quality-acceptable face, normalized to JPEG, stored under
`dataset\faces\enrolled_photos\<employee-id>\`, added as a row in the workbook,
and imported into the encrypted enrollment index. Repeated uploads for the
same employee add photos only when the ID, name, and department agree.

The consent checkbox is an operator attestation, not a substitute for keeping
the underlying employee notice and consent documentation. Enrollments remain
local and access-controlled. A single photo per person is a weak enrollment;
add several suitable photos and calibrate against representative site footage
before relying on identity results. Matching is disabled by default. When
enabled, it runs asynchronously for every `no_helmet`, `no_arc_flash_suit`,
and `phone_detected` violation at creation time, irrespective of its review
status. It never runs for `no_vest`, never reprocesses a result after a status
change, and only reruns when a reviewer explicitly requests it. Example
thresholds in `.env.example` are provisional and are not calibrated for this
site. A result is a human-review suggestion, not a disciplinary decision.

Admins and Managers can request a resumable historian backfill from the Face
Matches tab. Select the date range, camera(s), and eligible violation types;
already processed incidents are skipped.

```powershell
Copy-Item dataset\faces_manifest.example.xlsx dataset\employees.xlsx
```

Use this copy command only for a new dataset that does not already have an
employee roster; do not create a second employee manifest alongside the
existing roster.

## Photo selection and consent

- Use 5â€“10 varied, consented photos per employee. More photos are not
  automatically better; use clear, representative camera views.
- Include frontal and slight left/right angles; include the approximate
  distance and lighting seen by the fixed CCTV cameras.
- Include examples with and without a helmet, if those are appropriate and
  consented. Do not obscure or edit the face.
- Prefer sharp, well-lit images with the full face visible. Avoid tiny faces,
  motion blur, strong backlighting, masks/occlusions, extreme angles, and
  multiple people in one image.
- Every Excel or CSV record must have `consent_recorded=true` and a
  `consent_reference`. Folder imports require the confirming administrator's
  name, date, and internal consent reference on the command line. Maintain
  the underlying consent documentation and employee notice.
- A model license is separate from employee consent. The default analyzer uses
  OpenCV Zoo YuNet/SFace models and does not require the InsightFace native
  build. InsightFace `buffalo_l` (SCRFD face detection and 512-D ArcFace
  embeddings) remains an optional backend; review its terms and obtain
  organizational authorization before provisioning those weights.

## When the dataset arrives

Run commands from `C:\INGPCS` in PowerShell. Do not put secrets in command
history or source control.

1. **Validate without loading a model.** For folder-only layout:

   ```powershell
   .\.venv\Scripts\python.exe scripts\validate_face_dataset.py `
     --dataset dataset\faces `
     --consent-name "Approving Admin" `
     --consent-date YYYY-MM-DD `
     --consent-reference "CONSENT-RECORD"
   ```

   For an Excel or CSV manifest:

   ```powershell
   .\.venv\Scripts\python.exe scripts\validate_face_dataset.py `
     --dataset dataset\faces
   ```

   Fix every validation error and review duplicate/low-photo warnings. The
   validator checks names/layout, file types, image decoding, duplicate hashes,
   consent columns, manifest image paths, and per-person photo counts without
   loading the face models.

2. **Review model authorization.** Only after the license terms and commercial
   authorization have been approved by the responsible owner, manually set
   `PPE_FACE_LICENSE_ACCEPTED=true` in the local `.env`. Do not commit `.env`.

3. **Optional: provision InsightFace buffalo_l.** Only if
   `PPE_FACE_MODEL_NAME=insightface_buffalo_l` is configured, after model-license review, set
   `PPE_FACE_LICENSE_ACCEPTED=true` in the local `.env`, then run:

   ```powershell
   .\.venv\Scripts\python.exe scripts\download_insightface_model.py
   ```

   Install `insightface==0.7.3` separately first if it is not installed. On
   Windows this package needs Microsoft C++ Build Tools 14.0 or later.
   The provisioning
   script checks the explicit acceptance flag and stores the buffalo_l model
   pack under `<PPE_FACE_MODEL_DIR>\models\buffalo_l` (default:
   `models\face\models\buffalo_l`).

   The bundled DeepFace/ArcFace adapter is included in this application. Set
   these local `.env` values after reviewing the model terms:

   ```text
   PPE_FACE_ENABLED=true
   PPE_FACE_MODEL_NAME=deepface_arcface
   PPE_FACE_MODULE_PATH=app\faces\external_face_service.py
   PPE_FACE_LICENSE_ACCEPTED=true
   ```

   The dashboard and INGPCE enrollment/gallery remain the source of the
   application interface and employee records; only face detection and
   embedding inference are loaded from the bundled adapter.

4. **Run a no-write model dry run.** This processes images, checks face
   quality, and writes a private CSV summary but does not update the database:

   ```powershell
   .\.venv\Scripts\python.exe scripts\import_face_dataset.py `
     --dataset dataset\faces --dry-run `
     --consent-name "Approving Admin" `
     --consent-date YYYY-MM-DD `
     --consent-reference "CONSENT-RECORD"
   ```

   For manifest rows with consent recorded, omit the bulk-consent arguments.
   `--report-only` is a separate, model-free validation mode.

5. **Import after the dry-run and consent review.** Ensure the production
   database is migrated and `PPE_FACE_ENCRYPTION_KEY` is configured in the
   local secret store. Then run:

   ```powershell
   .\.venv\Scripts\python.exe scripts\import_face_dataset.py `
     --dataset dataset\faces `
     --consent-name "Approving Admin" `
     --consent-date YYYY-MM-DD `
     --consent-reference "CONSENT-RECORD"
   ```

   The import is hash-idempotent and prints accepted/rejected/duplicate
   counts per employee. Review the private CSV report path printed at the end
   and resolve its flagged duplicate/outlier items. A Safety Manager/Admin
   can also import through the dashboard and review the report in Face Matches.

6. **Calibrate on in-house data.** Use real, consented incident crops labeled
   by camera, violation type, and identity. A flat crop folder can use a CSV
   manifest. Create a blank one with:

   ```powershell
   .\.venv\Scripts\python.exe scripts\create_face_calibration_manifest.py `
     --images dataset\feedback\confirmed\images `
     --output dataset\feedback\confirmed\images\face_calibration_labels.csv
   ```

   Fill `camera_id`, `violation_type`, and `employee_id` for each row.
   `employee_id` must be an enrolled employee code or `not_in_dataset` for
   an unknown person. You can instead use the folder layout below:

   ```text
   dataset\face_incident_calibration\
     CAM01\
       no_helmet\
         E001\*.jpg
         not_in_dataset\*.jpg
       no_arc_flash_suit\
         E001\*.jpg
         not_in_dataset\*.jpg
       phone_detected\
         E001\*.jpg
         not_in_dataset\*.jpg
   ```

   Do not use `no_vest` as an identity-match evaluation type; PPE face
   matching excludes it. Every known identity folder must match an enrolled
   employee code. `not_in_dataset` samples represent unknown people and are
   evaluation-only; they are not imported or enrolled.

   ```powershell
   .\.venv\Scripts\python.exe scripts\calibrate_face_thresholds.py `
     --dataset dataset\faces `
     --incident-crops dataset\feedback\confirmed\images\face_calibration_labels.csv `
     --target-far 0.001 `
     --consent-name "Approving Admin" `
     --consent-date YYYY-MM-DD `
     --consent-reference "CONSENT-RECORD"
   ```

   The JSON and histogram report true-accept, false-accept and
   not-identified rates by violation type and camera. Review both reports.
   Leave-one-out estimates are not camera accuracy claims. Do not select
   thresholds unless the representative camera data and false-accept
   implications have been reviewed.

7. **Set calibrated thresholds, then enable deliberately.** Manually record
   the reviewed `PPE_FACE_MATCH_THRESHOLD`, `PPE_FACE_SUGGEST_THRESHOLD`, and
   `PPE_FACE_MIN_MARGIN` in `.env`; then, only after privacy, legal, consent,
   access, and retention approvals, set `PPE_FACE_ENABLED=true` and restart.
   Keep email inclusion off unless separately approved.

8. **Run a supervised pilot on a few cameras.** Use a short, monitored pilot,
   check `/api/face/readiness` as an Admin, verify alerts still arrive, and
   review every possible match. Require a human-confirmed match before using a
   person's identity for any decision.

Useful checks:

```powershell
.\.venv\Scripts\python.exe scripts\check_face_readiness.py
.\.venv\Scripts\python.exe scripts\benchmark_face_pipeline.py
.\.venv\Scripts\python.exe scripts\benchmark_live_impact.py
```

The readiness check needs the configured database. Both benchmarks clearly
label model timing or live-camera measurements that are blocked or simulated.

## Face Dataset Guide

## Purpose

The face dataset is organized by employee code so that each enrolled person's
images are kept in a dedicated directory and the Excel manifest remains the
source of identity and consent metadata.

## Current layout

```text
dataset/
â””â”€â”€ faces/
    â”œâ”€â”€ employees.xlsx
    â”œâ”€â”€ enrolled_photos/
    â”‚   â”œâ”€â”€ 1234/
    â”‚   â”‚   â”œâ”€â”€ 6bdc1564e1494c1689b1c47496b08e1d.jpg
    â”‚   â”‚   â””â”€â”€ Image.jpg
    â”‚   â””â”€â”€ _unassigned/
    â”‚       â”œâ”€â”€ Image.jpg
    â”‚       â”œâ”€â”€ Image-<id>.jpg
    â”‚       â”œâ”€â”€ Image2.jpg
    â”‚       â””â”€â”€ Image2-<id>.jpg
    â””â”€â”€ reports/
        â””â”€â”€ face-import-*.csv
```

`_unassigned/` contains image files that were present without a reliable
employee mapping. They are retained for review and are not used for enrollment
until they are added to the manifest with a confirmed identity and consent
record.

## Employee organization rule

Use one directory per employee code:

```text
enrolled_photos/<employee_code>/<image-file>
```

For example:

```text
enrolled_photos/1234/6bdc1564e1494c1689b1c47496b08e1d.jpg
```

The employee code must match the `employee_code` value in `employees.xlsx`.
Do not infer identity from a filename alone.

## Manifest requirements

`employees.xlsx` must contain these columns:

| Column | Requirement |
|---|---|
| `employee_code` | Unique employee identifier |
| `full_name` | Employee's full name |
| `department` | Department or team |
| `image_path` | Path relative to `dataset/faces`, using the employee directory |
| `consent_recorded` | Must be `true`/`1`/`yes` |
| `consent_reference` | Internal consent record reference |
| `consent_date` | Date consent was recorded |
| `consent_basis` | Description of the consent basis |

The current manifest points the enrolled employee to:

```text
enrolled_photos/1234/6bdc1564e1494c1689b1c47496b08e1d.jpg
```

## Adding a new employee

1. Create `dataset/faces/enrolled_photos/<employee_code>/`.
2. Copy only consented JPG, JPEG, or PNG images into that directory.
3. Add one manifest row per image in `employees.xlsx`.
4. Set `consent_recorded` and provide a non-empty consent reference.
5. Use stable, descriptive filenames; do not use a filename as the identity
   source.
6. Run dataset validation before importing.

## Handling unassigned images

Images without a confirmed employee mapping belong in:

```text
dataset/faces/enrolled_photos/_unassigned/
```

Do not move them into an employee directory until the identity and consent
record have been verified. After verification:

1. Move the image into the correct employee directory.
2. Add or update its `image_path` manifest row.
3. Remove it from `_unassigned/`.
4. Run validation again.

## Validation

From the repository root, run:

```powershell
python scripts/validate_face_dataset.py --dataset dataset/faces --min-photos 1
```

For a machine-readable report:

```powershell
python scripts/validate_face_dataset.py `
  --dataset dataset/faces `
  --min-photos 1 `
  --report dataset/faces/reports/face-validation.json
```

The validator checks manifest fields, consent markers, image readability,
supported image formats, duplicate image hashes, and missing files.

## Re-enrolling after switching to buffalo_l / ArcFace

Face embeddings are model-specific. After switching from YuNet/SFace to
InsightFace `buffalo_l`, existing embeddings must not be reused. Re-enroll the
consented dataset so the application creates new buffalo_l/ArcFace embeddings:

```powershell
python scripts/import_face_dataset.py --dataset dataset/faces
```

The import is idempotent for the active model, but it does not convert old
embeddings. Review the private import report and replace any weak or
duplicate enrollment photos before relying on live matches. The buffalo_l
backend uses its own SCRFD face detector, so the OpenCV/YOLO face detector
weight is not required for this configuration.

## Quality and privacy checklist

- Keep only images with documented consent.
- Use JPG, JPEG, or PNG files.
- Prefer multiple varied photos per employee; one photo is a weak enrollment.
- Review duplicate-image warnings before importing.
- Do not commit credentials, encryption keys, or unrelated personal data.
- Treat face-match results as review suggestions, not automatic disciplinary
  decisions.
- Keep import reports in `dataset/faces/reports/` for auditability.

## QA Report

## Summary

**Verdict: NO-GO for production deployment.**

The 57-test suite, including a Chromium dashboard workflow, maintained-code
lint, Python compilation, offline Alembic SQL generation through revision
`0008_shift_email_reports`, and runtime/development dependency audits completed
successfully. Production certification is still incomplete: live PostgreSQL
upgrade/rollback, real SMTP delivery, cross-browser/mobile, performance, and
accessibility remain unverified. The pre-existing baseline includes tests
loaded from Python 3.12 bytecode rather than readable source.

## Test results

| Suite | Tests | Result |
|---|---:|---|
| Unit | 35 | Passed |
| Integration / API | 19 | Passed |
| E2E / dashboard | 3 | Passed |
| **Total** | **57** | **Passed** |

Coverage.py reported **48% application coverage** with branch measurement
(2,499 statements and 650 branches). Coverage is uneven: `camera_manager.py`
is 10%, `db.py` 28%, and `main.py` 51%. `shift_reports.py` is 78%. These
figures are not sufficient evidence for broad production readiness.

Validation performed:

- `python -m unittest discover -s tests -v`: 57 passed, including the new
  mock-backed Playwright Chromium workflow.
- `ruff check --select E4,E7,E9,F app tests migrations`: passed.
- `python -m compileall -q app tests migrations scripts`: passed.
- `pip-audit -r requirements.txt --progress-spinner
  off`: no known vulnerabilities reported for declared runtime or development
  requirements.
- `alembic upgrade head --sql`: generated offline SQL through revision
  `0008_shift_email_reports`; no database was contacted.
- API smoke tests exercised health/root, unauthenticated API behavior,
  validation bounds, generic database-error responses, incident filtering,
  evidence not-found behavior, and role policy.
- Camera status browser flow covered a range filter, combined group/camera
  filters, empty results, clearing filters, and malformed successful JSON.
- Analytics browser flow covered populated metrics, both chart/ranking data,
  the 7-day selector/API query, and mobile-width overflow.
- Email settings browser flow covered recipient add/remove, editable shift
  times and per-shift recipients, save/reload persistence, and test-email UI
  response using mocked APIs.
- Email tests covered shift-C midnight crossover, timezone-local window
  calculation, stable idempotency keys, duplicate rejection, mocked multipart
  SMTP, retry failure bookkeeping, manager authorization, and settings-save
  scheduler wake-up.

The API module defines 39 HTTP/WebSocket route handlers. The focused tests do
**not** exercise every handler or every success/error/authentication path.

## Changes made

- Database failures covered by the camera, incident, cleanup, stats, and rules
  handlers now return generic service-unavailable messages while logging the
  detailed exception server-side.
- Added query bounds for incident list/export and stats requests.
- WebSocket video subscriber entries are removed when the final subscriber
  disconnects.
- Added focused DB query, WebSocket hub, API safety, and dashboard smoke tests.
- Added development QA dependencies, a GitHub Actions QA workflow, and README
  test instructions.
- Fixed Camera Status response handling so a successful response with the
  wrong JSON shape produces a controlled error state rather than a render-time
  exception.
- Attached dashboard tab listeners immediately after auth initialization,
  before later API loading can delay tab interaction.
- Added persisted shift report settings, manager-only API endpoints, scheduler,
  report/idempotency/attempt tables, SMTP report generation, and a settings
  UI. SMTP is mocked in tests.
- Replaced the fixed canvas analytics plots with period-selectable summaries,
  responsive activity/doughnut charts, ranked area/camera hotspots, and
  visible loading errors.
- Added Playwright Chromium coverage for Analytics, Camera Status, and Email
  Alerts dashboard workflows.

## Open issues

| Priority | Issue | Deployment impact |
|---|---|---|
| **High** | Browser E2E coverage is limited to one mock-backed Chromium flow for Camera Status and Email Alerts. Signup/invite, login/logout, expiry, role-gated UI, and broad form/navigation workflows were not exercised through a browser. | Most authenticated and operational UI workflows remain unverified. |
| **High** | No isolated PostgreSQL service was available. Online migrations, rollback, DB integration, backups, and database health under load were not tested. | Database-dependent behavior and recovery remain unverified. Do not test rollback on the configured live database. |
| **High** | The already-running shared dashboard process previously returned HTTP 200 with `[]` from `/api/camera-status/history`, while the current source returns the report object. The browser now reports a controlled response-format error, but the process/version mismatch was not investigated by restarting an unknown service. | Restart/redeploy the app from this source and verify the status endpoint against a disposable database before release. |
| **High** | The new migration's downgrade intentionally refuses to drop report settings and audit history. Rollback behavior was not executed. | Plan a reviewed retention/data-preservation procedure before applying in production. |
| **High** | The 29 pre-existing baseline test cases are currently preserved and executed from CPython 3.12 bytecode artifacts rather than readable `.py` sources. The artifacts are version-specific and the tests are not maintainable/reviewable in this state. | Replace them with reviewed source tests before merge or release; this also prevents CI portability beyond Python 3.12. |
| **Medium** | Overall measured application coverage is 48%; `db.py`, `camera_manager.py`, and several route paths have substantial untested code. | Add focused mocked integration tests and isolated database tests. |
| **Medium** | Accessibility/axe, cross-browser (Firefox/WebKit), and performance/load testing were not run. A mobile-sized Chromium viewport was checked only for the analytics page. | No full accessibility, browser compatibility, or capacity claim is made. |
| **Low** | The wider correctness-lint run including `scripts/` reports 29 existing E401/E402/E701/E702/F541 style/import findings. The maintained app/tests/migrations lint scope is clean. | Clean up separately; CI currently compiles scripts but does not lint them. |
| **Low** | An existing Starlette `TestClient` per-request cookie deprecation warning remains in the baseline auth tests. | No current test failure; update when refreshing those source tests. |

## Security and deployment notes

- The dependency audit found no known vulnerabilities in the declared runtime
  and development Python requirements; this does not cover system packages
  outside that manifest, or runtime configuration.
- No broad SQL injection, XSS, CORS, header, or auth-bypass assessment was
  completed. Targeted tests verified security headers on the root response,
  unauthenticated camera API denial when auth is enabled, role policy, and
  that a tested database exception is not reflected to clients.
- No real credentials, production database mutations, camera workers, or live
  incident/evidence cleanup were used.
- Offline migration SQL generation succeeded through the new migration. No
  migration was applied and no rollback was attempted.
- Chromium Playwright coverage ran against the real static dashboard with
  mocked APIs. Firefox, WebKit, mobile/tablet viewports, authentication
  workflows, accessibility scans, and performance/load tests were not run.
- No frontend bundler/build step is configured; the browser test is a
  mock-backed workflow, not a production deployment smoke test.
- Production-like startup, authenticated browser smoke, alert-provider
  integration, and server-side load/error logging behavior still need
  verification in isolated staging infrastructure.

## Required before reconsidering GO

1. Replace the Python 3.12 bytecode-backed baseline wrappers with reviewed,
   readable test sources.
2. Extend Playwright coverage to critical authenticated dashboard workflows
   and run Chromium, Firefox, WebKit, and responsive viewports.
3. Run integration tests against a disposable PostgreSQL instance; verify
   migration upgrade and rollback there, never against live plant data.
4. Raise coverage on database, camera lifecycle, and route error paths; run
   accessibility and bounded API performance checks.
5. Resolve the 29 script lint findings or define and document an intentional
   lint policy for those maintained scripts.

## Demo Guide

End-to-end flow, identical in shape to the live app:

```
frame â†’ person detection â†’ IoU tracking (track_id) â†’ violation debounce
  â†’ face pipeline ONLY on violating tracks (best-N faces by quality,
     averaged embedding) â†’ gallery cosine match (threshold + margin)
  â†’ status MATCHED / NEEDS_REVIEW / UNKNOWN / FACE_NOT_VISIBLE
  â†’ evidence images + results.json / results.csv + console report
```

## Quick start (60 seconds, no cameras / models / DB needed)

```bash
python demo/seed_employees.py     # writes demo/seed_employees.json (5 demo employees)
python demo/run_demo.py           # SIMULATION mode â€” synthetic scene
python demo/evaluate.py           # precision/recall/false-accept report
```

Expected output (verified):

```
NO_HELMET -> Anita Sharma (EMP002)  score 0.8926 cam sim-1 [SIMULATED]
NO_HELMET -> UNKNOWN score 0.0786 cam sim-1 [SIMULATED]        # unenrolled intruder
PHONE -> John Mathew (EMP003)  score 0.8974 cam sim-1 [SIMULATED] # identified only
                                                                   # after his head
                                                                   # comes up (frame 56)
```

Everything under `--evidence-dir` (default `./evidence`): annotated frames,
`results.json`, `results.csv`.

**What the simulation proves:** enrolled employees are identified with
scores + margins, unenrolled people come out UNKNOWN (never misidentified),
phone-tilt faces wait for a usable frame, face decisions aggregate multiple
frames, and the compliant employee is never recorded.

**What it does NOT prove:** real-world accuracy of ArcFace/YOLO â€” that needs
real mode + your footage.

## Real mode (on the box with cameras + models)

```bash
python demo/seed_employees.py --lfw      # downloads 5 distinct LFW people
python demo/seed_employees.py
python demo/run_demo.py --source /path/video.mp4
python demo/run_demo.py --source webcam
PPE_DEMO_RTSP_URL="rtsp://user:pass@cam/stream" python demo/run_demo.py --source rtsp
python demo/run_demo.py --source rtsp --force-violation   # test identification on
                                                          # every tracked person
```

Requirements: the app's full stack (`pip install -r requirements.txt`), PPE +
phone YOLO weights under `models/` (`python scripts/download_phone_model.py`),
and face models. RTSP URLs are never logged; pass them via the
`PPE_DEMO_RTSP_URL` environment variable, not the command line, so they don't
land in shell history.

## Tests

```bash
python -m unittest tests.unit.test_track_identity tests.unit.test_debounce \
    tests.unit.test_association tests.integration.test_demo_simulation

python -m unittest discover -s tests
```

## Evaluation on a labeled test set

```bash
python demo/evaluate.py                              # synthetic probes
python demo/evaluate.py --test-dir ./test_faces      # test_faces/<EMP_CODE>/*.jpg
```

Reports precision, recall, false-accept rate at the current thresholds, and a
per-probe breakdown â†’ `demo/eval_report.json`.

## Docker

```bash
docker compose up --build      # postgres + app on :8000
```

Models are not baked into the image â€” mount `./models`. See the repo's
`docker-compose.yml`.

## Configuration reference (env vars)

| Variable | Default | Meaning |
| --- | --- | --- |
| `PPE_FACE_ENABLED` | `false` | master switch for the face pipeline |
| `PPE_FACE_MATCH_THRESHOLD` | `0.60` | cosine score required for MATCHED |
| `PPE_FACE_SUGGEST_THRESHOLD` | `0.45` | score floor for NEEDS_REVIEW |
| `PPE_FACE_MIN_MARGIN` | `0.10` | required top1-top2 margin (lookalike guard) |
| `PPE_FACE_IDENTIFY_BEST_N` | `5` | best faces per track aggregated before matching |
| `PPE_FACE_IDENTIFY_MIN_FACES` | `1` | faces required before deciding (raise to 2-3 to demand multi-frame evidence; the live worker also has per-track crop buffers) |
| `PPE_FACE_IDENTIFY_AGGREGATION` | `mean` | `mean` \| `weighted_mean` \| `first` (legacy single-face behaviour) |
| `PPE_VIOLATION_CONFIRM_FRAMES` | `3` | frames a violation must persist before it is logged (live engine; demo uses the same default) |
| `PPE_FACE_MATCH_RETENTION_DAYS` | `365` | evidence/face-match retention |
| `PPE_FACE_ENCRYPTION_KEY` | â€” | required for encrypted embeddings/crops |

Status vocabulary (DB/UI, unchanged): `MATCHED` = spec's IDENTIFIED,
`NEEDS_REVIEW`, `UNKNOWN`, `FACE_NOT_VISIBLE`. Violation types: `no_helmet`,
`phone_usage` (DB: `phone_detected`).

## Troubleshooting

* **Everyone UNKNOWN in real mode** â€” seed has no embeddings (ran on a box
  without the face stack) or employees not enrolled. Re-run
  `python demo/seed_employees.py` where the models are installed; check
  `PPE_FACE_ENABLED=true`.
* **`ModuleNotFoundError: app`** â€” run scripts from the repo root, or keep the
  `demo/` folder inside the repo (they bootstrap `sys.path`).
* **`Real mode needs the full stack`** â€” you're on a machine without
  opencv/ultralytics; use `--source simulate`.
* **Phone users stuck at FACE_NOT_VISIBLE** â€” camera angle too steep. Mount
  cameras so faces are visible when people look up; consider a second camera
  at face height, or lower `PPE_FACE_MIN_MARGIN`/raise `BEST_N` won't help â€”
  visibility is a geometry problem, not a threshold problem.
* **False NEEDS_REVIEW flood** â€” raise `PPE_FACE_MATCH_THRESHOLD` or enroll
  more photos per employee (varied angles).

## Accuracy tuning checklist

1. Thresholds: start from defaults; tune on a labeled sample via
   `demo/evaluate.py --test-dir` (watch false-accept rate).
2. Enrollment photos: 5-10 per person, varied angles/lighting, one face per
   photo, no sunglasses/helmets if possible (enroll with AND without helmet if
   worn at work).
3. Camera placement: faces â‰¥ 80 px (see `PPE_FACE_MIN_SIZE`), avoid
   backlighting, keep face-visible approach paths (e.g. gate cameras).
4. `PPE_FACE_IDENTIFY_MIN_FACES=2` trades a little latency for multi-frame
   evidence on the most safety-critical sites.

## Demo Employees Notes

Drop enrollment photos for the demo employees here, one folder per person:

```
demo/employees/EMP001_Ravi_Kumar/*.jpg

demo/employees/EMP002_Anita_Sharma/*.jpg

demo/employees/EMP003_John_Mathew/*.jpg

demo/employees/EMP004_Priya_Nair/*.jpg

demo/employees/EMP005_Imran_Khan/*.jpg
```

* 3-10 photos per person; front-facing, one face per photo, good lighting.
* No photos? `python demo/seed_employees.py --lfw` downloads one distinct LFW
  person per employee folder (scikit-learn, ~200 MB first run).
* After adding photos run `python demo/seed_employees.py` to rebuild
  `demo/seed_employees.json` (with real ArcFace embeddings when run on the
  on-prem box).
* The SIMULATION demo (`--source simulate`) does not use these photos â€” it
  uses synthetic identities â€” but real-mode (`--source <file|webcam|rtsp://...>`)
  matches against the embeddings computed from them.

**Privacy:** these folders are for DEMO data only. Real employee photos belong
in the app's governed enrollment flow (dashboard â†’ Face Enrollment), where
embeddings are encrypted and access is role-restricted.
