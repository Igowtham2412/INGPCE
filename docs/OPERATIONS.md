# Operations Runbook

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
