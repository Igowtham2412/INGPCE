# QA and Pre-deployment Report

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
- `pip-audit -r requirements.txt -r requirements-dev.txt --progress-spinner
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
