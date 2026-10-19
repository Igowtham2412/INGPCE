# Camera Status Filters and Shift Reports

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
manager-authorized APIs. Defaults are Shift A 06:00–14:00, Shift B 14:00–22:00,
and Shift C 22:00–06:00 in the configured IANA time zone. Global and
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
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
   .\.venv\Scripts\python.exe -m playwright install chromium
   .\.venv\Scripts\python.exe -m unittest discover -s tests -v
   ```

## Verification limits

The browser workflow uses mocked API responses and never reaches the
configured database or SMTP server. It verifies camera filtering and the
settings UI save/reload flow in Chromium; it does not verify browser flows for
authentication, live camera streams, Firefox/WebKit/mobile layouts, or live
SMTP delivery. No migration has been applied to a real database. See
`QA_REPORT.md` for the final test counts and deployment verdict.
