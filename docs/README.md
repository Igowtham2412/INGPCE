# INGPCE Documentation

This directory is the maintainer-facing documentation set for the INGPCE
industrial safety monitoring application.

## Start here

| Document | Use it for |
|---|---|
| [Architecture](./ARCHITECTURE.md) | Understanding the end-to-end system and module boundaries |
| [Development](./DEVELOPMENT.md) | Setting up a workstation, changing code, and running tests |
| [Operations](./OPERATIONS.md) | Starting, monitoring, recovering, and maintaining a deployment |
| [Data and models](../DATASET_GUIDE.md) | PPE feedback, face enrollment, calibration, and model evaluation |
| [Face dataset guide](../FACE_DATASET_GUIDE.md) | Consent, validation, import, and face-specific data handling |

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
