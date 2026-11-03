# Face Dataset Organization Guide

## Purpose

The face dataset is organized by employee code so that each enrolled person's
images are kept in a dedicated directory and the Excel manifest remains the
source of identity and consent metadata.

## Current layout

```text
dataset/
└── faces/
    ├── employees.xlsx
    ├── enrolled_photos/
    │   ├── 1234/
    │   │   ├── 6bdc1564e1494c1689b1c47496b08e1d.jpg
    │   │   └── Image.jpg
    │   └── _unassigned/
    │       ├── Image.jpg
    │       ├── Image-<id>.jpg
    │       ├── Image2.jpg
    │       └── Image2-<id>.jpg
    └── reports/
        └── face-import-*.csv
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
