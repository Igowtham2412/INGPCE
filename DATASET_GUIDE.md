# Employee Face Dataset Guide

Face images and biometric data are sensitive. Keep real images on the
authorized INGPCE host, use only consented in-house photos, and never commit
or send them to chat or external services. The directories
`dataset\faces\` and `dataset\face_test_crops\` are Git-ignored; only their
`.gitkeep` placeholders are tracked.

## Recommended folder and Excel layout

Put 5–10 photos per person in a folder named with the unique employee code,
an underscore, and the person's full name. Keep one Excel manifest at the
dataset root:

```text
dataset\faces\
  employees.xlsx
  E001_Jane Doe\
    image01.jpg
    image02.png
  E002_Ravi Kumar\
    image01.jpg
```

Copy the blank template and fill one row per photo. Set `image_path` relative
to `dataset\faces\`, including the employee folder:

```text
employee_code | full_name | department | image_path | consent_recorded | consent_reference
E001          | Jane Doe  | Operations | E001_Jane Doe\image01.jpg | true | CONSENT-REF-001
```

Use `dataset\faces_manifest.example.xlsx` and copy it to
`dataset\faces\employees.xlsx`. `image_path` must point to a JPG, JPEG, or PNG
inside the dataset. Alternatively, use `employees.csv` with the same columns.
Do not put multiple manifests in the dataset root. Folder-only imports (no
manifest) are also supported and use the explicit bulk-consent confirmation.
`openpyxl==3.1.5` is installed with the application to read Excel.

```powershell
Copy-Item dataset\faces_manifest.example.xlsx dataset\faces\employees.xlsx
```

## Photo selection and consent

- Use 5–10 varied, consented photos per employee. More photos are not
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
- A model license is separate from employee consent. Do not load or download
  model weights until the organization has confirmed the model license permits
  the intended commercial use. `buffalo_l` is restricted to non-commercial
  research by the InsightFace model terms.

## When the dataset arrives

Run commands from `C:\INGPCS` in PowerShell. Replace the example consent
details, URL, and checksum with the approved in-house values. Do not put
secrets in command history or source control.

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
   InsightFace.

2. **Review model authorization.** Only after the license terms and commercial
   authorization have been approved by the responsible owner, manually set
   `PPE_FACE_LICENSE_ACCEPTED=true` in the local `.env`. Do not commit `.env`.

3. **Download the licensed model.** Obtain an HTTPS ZIP URL and a trusted
   SHA-256 from the authorized model supplier, then run:

   ```powershell
   .\.venv\Scripts\python.exe scripts\download_face_model.py `
     --url "https://authorized-supplier.example/model.zip" `
     --sha256 "<64-character-trusted-sha256>"
   ```

   Model name/root are selected with `PPE_FACE_MODEL_NAME` and
   `PPE_FACE_MODEL_DIR`. The downloader checks the flag before network access,
   verifies the archive hash, rejects unsafe archive paths, and refuses to
   overwrite an existing model directory.

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

6. **Calibrate on in-house data.** Use real, consented incident crops under
   `dataset\face_test_crops\`, organized by camera, violation type, and identity:

   ```text
   dataset\face_test_crops\
     CAM01\
       missing_helmet\
         E001\*.jpg
         not_in_dataset\*.jpg
       missing_vest\
         E001\*.jpg
         not_in_dataset\*.jpg
       phone_detected\
         E001\*.jpg
         not_in_dataset\*.jpg
   ```

   The violation folders may also use `no_helmet` and `no_vest`. Every known
   identity folder must match an enrolled employee code. `not_in_dataset`
   samples represent unknown people and are used only for evaluation; they
   are not imported or enrolled.

   ```powershell
   .\.venv\Scripts\python.exe scripts\calibrate_face_thresholds.py `
     --dataset dataset\faces `
     --incident-crops dataset\face_test_crops `
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
