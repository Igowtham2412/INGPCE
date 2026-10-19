# INGPCE Industrial Safety Operations

Real-time PPE monitoring for configured CCTV/NVR streams. The application
combines camera video, PPE detection, incident evidence, PostgreSQL history,
camera health, and rule-based incident email alerts delivered **only through
Microsoft Power Automate**.

This README is the operational guide for a new maintainer: setup, configuration,
testing, production handover, support, and recovery.

## 1. System overview

### Main capabilities

- Live camera wall with filters, full-screen viewing, and reconnect controls.
- Recorded playback for supported Hikvision NVR streams.
- PPE incident detection, snapshots, short evidence clips, review, and model
  feedback export.
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
recipient list, subject, and text body; it does **not** upload camera images or
clips to the flow. Shift-summary email reports are not included in this build.

### Main components

| Path | Responsibility |
|---|---|
| `app/main.py` | FastAPI API, authentication middleware, app startup |
| `app/config.py` | Environment variables and seed camera/area configuration |
| `app/camera_manager.py` | Camera worker lifecycle and reconnect/watchdog behavior |
| `app/stream_handler.py` | Camera capture and frame streaming |
| `app/detector.py`, `app/compliance_engine.py` | PPE inference and violation decisions |
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
.\.venv\Scripts\python.exe -m app.main
```

The application loads `.env`, applies pending Alembic migrations, synchronizes
seed cameras/area groups, starts camera workers, and starts the alert outbox
worker. A ready server prints `STARTUP COMPLETE - application is ready.`

Open <http://127.0.0.1:8080/>. Stop the foreground server with Ctrl+C.

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

Seed camera configuration lives in `app/config.py`; database-backed camera
records are what the dashboard displays. On every startup, configured camera
seeds are synchronized. The Cameras tab also has **Sync Default Cameras**.
Camera names, custom cameras, PPE options, and existing incident history are
database data; use a database backup before bulk edits.

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
4. In **Request Body JSON Schema**, choose **Use sample payload to generate
   schema** and paste:

   ```json
   {
     "event_type": "test",
     "subject": "INGPCE Power Automate Test",
     "body": "INGPCE Power Automate test message\nRecipient: safety.team@example.com\nSent at: 2026-10-16T03:30:00Z",
     "camera_name": "TEST",
     "zone": "Power Automate setup",
     "violation": "Test message",
     "occurred_at": "2026-10-16T03:30:00Z",
     "recipients": ["safety.team@example.com"],
     "image_name": "helmet_violation.jpg",
     "image_content_type": "image/jpeg",
     "image_content_bytes": "base64-encoded-image-data"
   }
   ```

   Real incident requests use `event_type: "ppe_violation"` and include the
   camera, zone, violation, timestamp, and configured recipient list. They
   also contain the annotated helmet-violation JPEG as base64 in
   `image_content_bytes`. Test messages do not include an image.
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
     won't send a helmet incident without a readable evidence image.
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

- Choose the camera/zone filters and cooldown. Power Automate alerts are
  restricted to **Missing Helmet** violations; other violation types are
  never emailed.
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
- [ ] A supervised missing-helmet incident email arrives with its annotated
      JPEG attached; non-helmet violations do not trigger Power Automate email.
- [ ] Helmet alert rule recipients, camera/zone filters, and cooldowns are reviewed.
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
