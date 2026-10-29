# Demo & Proof of Concept — Violation → Named Employee

End-to-end flow, identical in shape to the live app:

```
frame → person detection → IoU tracking (track_id) → violation debounce
  → face pipeline ONLY on violating tracks (best-N faces by quality,
     averaged embedding) → gallery cosine match (threshold + margin)
  → status MATCHED / NEEDS_REVIEW / UNKNOWN / FACE_NOT_VISIBLE
  → evidence images + results.json / results.csv + console report
```

## Quick start (60 seconds, no cameras / models / DB needed)

```bash
python demo/seed_employees.py     # writes demo/seed_employees.json (5 demo employees)
python demo/run_demo.py           # SIMULATION mode — synthetic scene
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

**What it does NOT prove:** real-world accuracy of ArcFace/YOLO — that needs
real mode + your footage.

## Real mode (on the box with cameras + models)

```bash
# 1. photos (any of): drop photos in demo/employees/, or LFW fallback:
python demo/seed_employees.py --lfw      # downloads 5 distinct LFW people
# 2. seed with REAL embeddings (InsightFace/ArcFace)
python demo/seed_employees.py
# 3. run against a source
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
# dependency-free suite (numpy only) — runs anywhere including CI:
python -m unittest tests.unit.test_track_identity tests.unit.test_debounce \
    tests.unit.test_association tests.integration.test_demo_simulation

# full suite incl. OpenCV/Ultralytics-dependent tests (on-prem box):
python -m unittest discover -s tests
```

## Evaluation on a labeled test set

```bash
python demo/evaluate.py                              # synthetic probes
python demo/evaluate.py --test-dir ./test_faces      # test_faces/<EMP_CODE>/*.jpg
```

Reports precision, recall, false-accept rate at the current thresholds, and a
per-probe breakdown → `demo/eval_report.json`.

## Docker

```bash
docker compose up --build      # postgres + app on :8000
```

Models are not baked into the image — mount `./models`. See the repo's
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
| `PPE_FACE_ENCRYPTION_KEY` | — | required for encrypted embeddings/crops |

Status vocabulary (DB/UI, unchanged): `MATCHED` = spec's IDENTIFIED,
`NEEDS_REVIEW`, `UNKNOWN`, `FACE_NOT_VISIBLE`. Violation types: `no_helmet`,
`phone_usage` (DB: `phone_detected`).

## Troubleshooting

* **Everyone UNKNOWN in real mode** — seed has no embeddings (ran on a box
  without the face stack) or employees not enrolled. Re-run
  `python demo/seed_employees.py` where the models are installed; check
  `PPE_FACE_ENABLED=true`.
* **`ModuleNotFoundError: app`** — run scripts from the repo root, or keep the
  `demo/` folder inside the repo (they bootstrap `sys.path`).
* **`Real mode needs the full stack`** — you're on a machine without
  opencv/ultralytics; use `--source simulate`.
* **Phone users stuck at FACE_NOT_VISIBLE** — camera angle too steep. Mount
  cameras so faces are visible when people look up; consider a second camera
  at face height, or lower `PPE_FACE_MIN_MARGIN`/raise `BEST_N` won't help —
  visibility is a geometry problem, not a threshold problem.
* **False NEEDS_REVIEW flood** — raise `PPE_FACE_MATCH_THRESHOLD` or enroll
  more photos per employee (varied angles).

## Accuracy tuning checklist

1. Thresholds: start from defaults; tune on a labeled sample via
   `demo/evaluate.py --test-dir` (watch false-accept rate).
2. Enrollment photos: 5-10 per person, varied angles/lighting, one face per
   photo, no sunglasses/helmets if possible (enroll with AND without helmet if
   worn at work).
3. Camera placement: faces ≥ 80 px (see `PPE_FACE_MIN_SIZE`), avoid
   backlighting, keep face-visible approach paths (e.g. gate cameras).
4. `PPE_FACE_IDENTIFY_MIN_FACES=2` trades a little latency for multi-frame
   evidence on the most safety-critical sites.
