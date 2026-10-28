# INGPCE Safety Monitoring — Executive Overview and Handover

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
- Sends configured helmet and phone-detection alerts—with an annotated image—
  through the organization’s Power Automate email flow.

### How an alert reaches the team

**Camera video → local AI detection → tracked incident and evidence → matching
alert rule → Power Automate → email recipient.**

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
  model quality, and local validation. No “best in the world” accuracy claim
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
  cameras and can be selected in **Edit Camera → Required PPE**.
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

### Main components

| Path | Responsibility |
|---|---|
| `app/main.py` | FastAPI API, authentication middleware, app startup |
| `app/config.py` | Environment variables and seed camera/area configuration |
| `app/camera_manager.py` | Camera worker lifecycle and reconnect/watchdog behavior |
| `app/stream_handler.py` | Camera capture and frame streaming |
| `app/detector.py`, `app/compliance_engine.py` | PPE/phone inference, person tracking, and violation decisions |
| `app/rules_engine.py` | Rule evaluation and cooldowns |
| `app/alert_manager.py` | Durable alert outbox and Power Automate delivery |
| `app/db.py` | PostgreSQL schema, queries, seed synchronization |
| `dashboard/` | Browser dashboard |
| `migrations/versions/` | Ordered Alembic database migrations |
| `scripts/` | Setup, model, account, and support utilities |

## 2. Requirements and network access

- Windows 10/11 for the supplied scheduled-task deployment; Python 3.10–3.12
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
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

`requirements-dev.txt` installs development/test tools as well as Playwright.
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

Face recognition is disabled by default. The implementation uses local
InsightFace/ArcFace processing; it does not send images to a cloud service and
does not download model files automatically. The InsightFace library code is
MIT-licensed, but InsightFace's [official model README](https://github.com/deepinsight/insightface/blob/master/python-package/README.md)
and [model zoo](https://github.com/deepinsight/insightface/blob/master/model_zoo/README.md)
state that pretrained model weights, including `buffalo_l`, are for
non-commercial research only. Those weights are not permitted for this
commercial industrial-safety use without separate written authorization.

The optional face runtime is separate from the normal application install.
With the existing Python 3.12 virtual environment, install the CUDA 13 /
cuDNN 9 GPU runtime using:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\install_face_stack.ps1
```

The script installs pinned InsightFace, ONNX, NumPy, OpenCV, and ONNX Runtime
packages, then checks that CUDA is an available ONNX Runtime provider. Use
`-Cpu` to install the CPU-only runtime instead:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\install_face_stack.ps1 -Cpu
```

The GPU installer deliberately restores the GPU ONNX Runtime files after
installing InsightFace because InsightFace also declares a CPU `onnxruntime`
dependency. This is necessary to avoid the CPU package overwriting the GPU
provider files. ONNX Runtime 1.30.0 is selected because its official
compatibility table lists CUDA 13.0/cuDNN 9.x and compatibility with PyTorch
CUDA 13.x. This does not install or fetch any pretrained face models. The
regular application requirements do not include these optional packages; the
application continues to start with face recognition disabled if they are
absent.

Model loading and downloading require the explicit local setting
`PPE_FACE_LICENSE_ACCEPTED=true`. Leave it false until the organization has
reviewed the applicable model terms and secured permission. Do not assume the
MIT license for package code also grants commercial rights to model weights.

After authorization, get a model ZIP and its independently trusted SHA-256
digest from the authorized supplier. The model name and root are configurable
using `PPE_FACE_MODEL_NAME` and `PPE_FACE_MODEL_DIR`. The downloader will not
access the URL while the license flag is false:

```powershell
$env:PPE_FACE_LICENSE_ACCEPTED = "true"
.\.venv\Scripts\python.exe scripts\download_face_model.py `
  --url "https://authorized-model-supplier.example/model.zip" `
  --sha256 "<64-character-trusted-sha256>"
```

The downloader requires HTTPS, verifies the archive digest before extraction,
rejects unsafe ZIP paths, and refuses to overwrite an existing pack. It does
not select a model or imply that the supplier's terms permit this use. Do not
set the flag until authorization is actually reviewed.

Alternatives are (1) obtain commercial authorization for the InsightFace
weights, (2) choose a face model with terms that explicitly permit this use,
or (3) purchase a vendor SDK licensed for industrial deployment. A vendor SDK
with written deployment and support terms is recommended for review, but the
organization must choose and approve its own option.

The configured dataset path (`PPE_FACE_DATASET_PATH`, default
`dataset\faces`) supports employee folders with a CSV or Excel manifest:

See [DATASET_GUIDE.md](./DATASET_GUIDE.md) for photo guidance and the
validate/import/calibrate/pilot command sequence.

Employee folders should be named `<EMPLOYEE_CODE>_<Full Name>` and contain
JPG, JPEG, or PNG photos. A single `employees.csv` or `employees.xlsx`
manifest may sit in the dataset root alongside these folders. Use one row per
photo, with `employee_code`, `full_name`, `department`, `image_path`,
`consent_recorded`, and `consent_reference` columns. Image paths are relative
to the manifest and must remain inside the dataset directory. Optional
`consent_basis` and `consent_date` columns retain the recorded consent source
and date. The Excel manifest reader is included in the optional face install.

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
`CAM01\missing_vest\E001\*.jpg` and
`CAM01\missing_vest\not_in_dataset\*.jpg`. Supported type folders are
`missing_helmet`/`no_helmet`, `missing_vest`/`no_vest`, and
`phone_detected`. The older employee-only and camera/employee layouts remain
supported but report an `unspecified_type` group. Each known label must match
an enrolled employee code. `not_in_dataset` crops are evaluation-only and
must never be added to the enrollment dataset.

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
`PPE_FACE_QUALITY_MIN_BY_VIOLATION` (a JSON map from type to 0–1 minimum).
These floors only reject lower-quality faces; do not lower match thresholds
for difficult images. At the default 0.1% target, at least 1,000 impostor score samples are
needed even to satisfy the tool's minimum sample-count check; this is not a
guarantee of statistical confidence. Thresholds are recommendations only and
do not modify application configuration. Do not claim performance or enable
identification until representative, consented footage from the actual
cameras has been evaluated and reviewed.

When `PPE_FACE_ENABLED=true`, the application queues recognition only after an
eligible, temporally confirmed violation has been stored. Eligibility is
configured by `PPE_FACE_VIOLATION_TYPES`, defaulting to
`missing_helmet,missing_vest,phone_detected`, which map to stored types
`no_helmet`, `no_vest`, and `phone_detected`. Unknown configuration values are
warned about and ignored. `PPE_FACE_TRACK_REUSE_SECONDS` defaults to 120:
within that window, incidents from the same camera and ByteTrack ID reuse one
completed result instead of running the model again. The original analysis
considers the buffered full-person crops and incident evidence; no usable face
is recorded as no face/not identified, never guessed.
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
recognition work is deferred rather than slowing detection. The default match and
suggestion thresholds are both `1.0` (fail-closed); set them only from a
reviewed calibration. `PPE_FACE_MATCH_STRATEGY` may be `max` or `top_k_mean`,
and calibration uses the configured strategy. Provider and processing time
are recorded with the result so actual GPU/CPU cost can be measured; this
workspace has not been measured on production cameras.
Run `scripts\benchmark_face_pipeline.py` for model-free queue/crop/matching
microbenchmarks, reported with violation-type and camera labels. After model
authorization and provisioning, it can measure local inference on a synthetic
or explicitly consented sample; InsightFace exposes detection and embedding in
one call, so the tool reports combined latency. `scripts\benchmark_live_impact.py`
compares the PPE detector and annotated-tile encoding on synthetic paced frames
or a local recorded video while a bounded, simulated face-job worker runs; it
reports camera-to-detection and camera-to-tile-ready latency plus simulated
job counts by violation type. It does not measure WebSocket delivery or a real
camera and is not a production performance claim.
Match and review records are automatically
removed after `PPE_FACE_MATCH_RETENTION_DAYS` (default 365; set `0` to disable
automatic expiry). Original enrollment photos remain off by default.
For `Identified` and `Suggested` results, the selected face crop is encrypted
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
for an `Identified` result, with pending-review wording until a reviewer
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

Each camera uses one shared sub-stream RTSP capture for the Live Wall and
detection, falling back to its configured main stream only when the sub-stream
cannot open or stops delivering frames. The browser receives visible tiles
over one multiplexed WebSocket, with JPEG frames encoded once and reused for
subscribers. Opening
a camera's full-screen viewer opens its configured main stream on demand;
recorded evidence requests the NVR playback URL built from that main stream.
Existing camera rows remain supported through their legacy `rtsp_url` value.
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

`GET /api/health` includes per-camera capture/detection FPS, latest-frame age,
reconnect count, and inference duration. The Live Wall's **STREAM DIAGNOSTICS**
panel displays these values while it is open.

The COCO phone class detects visible phone objects, not the behavior of using
one. A phone incident is created only after it is associated with the existing
person track and observed across multiple frames. The dashboard and email
therefore say “Phone detected near person,” not “phone use confirmed.”

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

The default setup uses the configured NVR channel feeds only. Legacy `D1`–`D20`
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
       "image_content_bytes": {"type": "string"}
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
5. Add **Office 365 Outlook → Send an email (V2)** and create its connection
   using the approved sender mailbox:
   - **To**: expression `join(triggerBody()?['recipients'], ';')`
   - **Subject**: `subject` from the trigger
   - **Body**: `body` from the trigger
   - **Attachments**: add one attachment with **Name** set to
     `image_name` and **ContentBytes** set to `image_content_bytes`.
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

- **Live Wall** — current camera feeds and live event rail.
- **Playback** — select a camera and recorded interval.
- **Incidents** — filter and review stored evidence; authorized administrators
  can use the retention cleanup action.
- **Analytics** — incident trends and violation breakdowns.
- **Cameras** — camera/area management and seed synchronization.
- **Camera Status** — uptime, timeline, and connection transitions.
- **Alert Delivery** — manager/admin-only real-flow test send.
- **Alert Rules** — rule-based Power Automate email configuration.

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
- Shift summary emails and other notification providers are not active
  features.
- The flow endpoint uses a secret signed URL and does not use app-managed
  Microsoft Entra OAuth.
- Power Automate owns actual mailbox delivery. App success is based on the
  configured flow's HTTP response.
- Review the detector model's license and accuracy against representative
  site footage before operational use.
