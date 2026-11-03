from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from deepface import DeepFace
from PIL import Image


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


MODEL_NAME = os.getenv("FACE_MODEL", "ArcFace")
DETECTOR_BACKENDS = tuple(
    backend.strip()
    for backend in os.getenv(
        "FACE_DETECTORS", "retinaface,mtcnn,opencv"
    ).split(",")
    if backend.strip()
)
DEFAULT_THRESHOLD = float(os.getenv("FACE_THRESHOLD", "0.68"))
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class FaceDataset:
    """File-backed face dataset with an in-memory embedding cache."""

    def __init__(self, dataset_dir: str | Path = "dataset") -> None:
        self.dataset_dir = Path(dataset_dir)
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        self._embeddings: dict[str, list[float]] = {}
        self._embedding_files: dict[str, str] = {}
        self._detector_backend = DETECTOR_BACKENDS[-1]
        self._load_cache()

    def _load_cache(self) -> None:
        for person_dir in self.dataset_dir.iterdir():
            if not person_dir.is_dir():
                continue
            cache_path = person_dir / "embeddings.json"
            if not cache_path.exists():
                continue
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if cached.get("_model") != MODEL_NAME:
                continue
            for image_path, embedding in cached.get("embeddings", {}).items():
                key = str(Path(image_path))
                self._embeddings[key] = [float(value) for value in embedding]
                self._embedding_files[key] = key

    def _person_details(self, person_dir: Path) -> dict[str, Any]:
        details_path = person_dir / "details.json"
        if not details_path.exists():
            return {"id": person_dir.name, "name": person_dir.name}
        try:
            details = json.loads(details_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {details_path}") from exc
        if not isinstance(details, dict):
            raise ValueError(f"{details_path} must contain a JSON object")
        return {"id": person_dir.name, **details}

    def people(self) -> list[dict[str, Any]]:
        return [
            self._person_details(person_dir)
            for person_dir in sorted(self.dataset_dir.iterdir())
            if person_dir.is_dir()
        ]

    def _crop_face(self, image_path: Path) -> tuple[Path, str]:
        faces: list[dict[str, Any]] = []
        errors: list[str] = []
        for backend in DETECTOR_BACKENDS:
            try:
                faces = DeepFace.extract_faces(
                    img_path=str(image_path),
                    detector_backend=backend,
                    enforce_detection=True,
                    align=True,
                )
                if faces:
                    self._detector_backend = backend
                    break
            except Exception as exc:
                errors.append(f"{backend}: {exc}")
        if not faces:
            raise ValueError(
                "No face could be detected. Use a clear, front-facing photo "
                "with good lighting and one visible face."
            ) from (ValueError("; ".join(errors)) if errors else None)
        face = max(
            faces,
            key=lambda item: item.get("facial_area", {}).get("w", 0)
            * item.get("facial_area", {}).get("h", 0),
        )
        face = np.asarray(face["face"])
        if face.max() <= 1:
            face = face * 255
        image = Image.fromarray(np.asarray(face, dtype=np.uint8))
        cropped = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        image.save(cropped.name, format="JPEG", quality=95)
        cropped.close()
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return Path(cropped.name), f"data:image/jpeg;base64,{encoded}"

    def _embedding(self, image_path: Path) -> list[float]:
        result = DeepFace.represent(
            img_path=str(image_path),
            model_name=MODEL_NAME,
            detector_backend=self._detector_backend,
            enforce_detection=True,
            align=True,
        )
        if not result:
            raise ValueError(f"No face found in {image_path.name}")
        return [float(value) for value in result[0]["embedding"]]

    def enroll(
        self, person_id: str, details: dict[str, Any], image_path: Path
    ) -> dict[str, Any]:
        if (
            not person_id
            or person_id in {".", ".."}
            or Path(person_id).name != person_id
        ):
            raise ValueError("person_id must be a non-empty folder-safe value")
        if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("Only JPG, JPEG, PNG, and WEBP images are supported")

        person_dir = self.dataset_dir / person_id
        person_dir.mkdir(parents=True, exist_ok=True)
        images_dir = person_dir / "images"
        images_dir.mkdir(exist_ok=True)
        destination = images_dir / image_path.name
        destination.write_bytes(image_path.read_bytes())

        profile = {"id": person_id, "name": details.get("name", person_id), **details}
        (person_dir / "details.json").write_text(
            json.dumps(profile, indent=2), encoding="utf-8"
        )
        cropped, _ = self._crop_face(destination)
        try:
            embedding = self._embedding(cropped)
        finally:
            cropped.unlink(missing_ok=True)
        cache_path = person_dir / "embeddings.json"
        cached: dict[str, Any] = {"_model": MODEL_NAME, "embeddings": {}}
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("_model") != MODEL_NAME:
                cached = {"_model": MODEL_NAME, "embeddings": {}}
            elif "embeddings" not in cached:
                cached = {"_model": MODEL_NAME, "embeddings": cached}
        relative_path = str(destination.relative_to(self.dataset_dir))
        cached["embeddings"][relative_path] = embedding
        cache_path.write_text(json.dumps(cached, indent=2), encoding="utf-8")
        self._embeddings[relative_path] = embedding
        self._embedding_files[relative_path] = relative_path
        return profile

    def _refresh_embeddings(self) -> None:
        for person_dir in self.dataset_dir.iterdir():
            if not person_dir.is_dir():
                continue
            for image_path in (person_dir / "images").glob("*"):
                key = str(image_path.relative_to(self.dataset_dir))
                if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                if key not in self._embeddings:
                    cropped, _ = self._crop_face(image_path)
                    try:
                        self._embeddings[key] = self._embedding(cropped)
                    finally:
                        cropped.unlink(missing_ok=True)
                    self._embedding_files[key] = key
            cache = person_dir / "embeddings.json"
            entries = {
                key: value
                for key, value in self._embeddings.items()
                if key.startswith(f"{person_dir.name}\\")
                or key.startswith(f"{person_dir.name}/")
            }
            if entries:
                cache.write_text(
                    json.dumps(
                        {"_model": MODEL_NAME, "embeddings": entries}, indent=2
                    ),
                    encoding="utf-8",
                )

    def recognize(
        self, image_path: Path, limit: int = 5, threshold: float = DEFAULT_THRESHOLD
    ) -> list[dict[str, Any]]:
        self._refresh_embeddings()
        if not self._embeddings:
            return []
        cropped_query, cropped_data_url = self._crop_face(image_path)
        try:
            query = np.asarray(self._embedding(cropped_query), dtype=np.float32)
        finally:
            cropped_query.unlink(missing_ok=True)
        matches: dict[str, dict[str, Any]] = {}
        for relative_path, embedding in self._embeddings.items():
            candidate = np.asarray(embedding, dtype=np.float32)
            distance = float(
                1
                - np.dot(query, candidate)
                / (np.linalg.norm(query) * np.linalg.norm(candidate))
            )
            person_id = Path(relative_path).parts[0]
            person_dir = self.dataset_dir / person_id
            similarity = max(0.0, min(100.0, (1 - distance / threshold) * 100))
            result = {
                **self._person_details(person_dir),
                "match_percentage": round(similarity, 2),
                "distance": round(distance, 6),
                "matched_image": relative_path.replace("\\", "/"),
                "is_match": distance <= threshold,
                "cropped_query": cropped_data_url,
            }
            if person_id not in matches or distance < matches[person_id]["distance"]:
                matches[person_id] = result
        return sorted(
            matches.values(),
            key=lambda item: item["distance"],
        )[:limit]


def save_upload(contents: bytes, filename: str | None) -> Path:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise ValueError("Only JPG, JPEG, PNG, and WEBP images are supported")
    temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temporary.write(contents)
    temporary.close()
    return Path(temporary.name)
