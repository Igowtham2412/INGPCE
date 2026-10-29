"""
track_identity.py — track-based multi-frame face identification core.

Pure-logic module (numpy only, no OpenCV / InsightFace imports) so it can be
unit-tested and executed anywhere, and reused by the live FaceRecognitionWorker
and the demo/simulation pipeline.

Design (see the identification spec):
  * A violating *track* accumulates face observations across frames. Only the
    best N faces by quality are kept; a single lucky/bad frame never decides.
  * Embeddings are aggregated (mean of unit vectors / quality-weighted mean /
    per-observation vote) and matched once against the employee gallery.
  * Decisions are cached per (camera, track) so the same person is not
    re-identified for every frame, and so later incidents from the same track
    reuse the settled identity.

Status vocabulary matches the existing DB/UI exactly:
  MATCHED == IDENTIFIED, NEEDS_REVIEW (borderline), UNKNOWN, FACE_NOT_VISIBLE.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

# Existing status vocabulary used across DB, API and UI (do not rename).
STATUS_MATCHED = "MATCHED"            # a.k.a. IDENTIFIED in the spec
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"  # borderline score -> human review queue
STATUS_UNKNOWN = "UNKNOWN"            # below suggest threshold, or no margin
STATUS_FACE_NOT_VISIBLE = "FACE_NOT_VISIBLE"  # no usable face in the track


@dataclass
class FaceObservation:
    """One usable face found in one crop of a track."""
    embedding: np.ndarray
    quality: float
    source: str = "track"
    measurements: dict[str, Any] | None = None
    absolute_bbox: list[int] | None = None
    face_crop: Any = None  # np.ndarray in live mode; anything in tests


def normalize_embedding(vector: Sequence[float]) -> np.ndarray:
    """Return the L2-normalized 1-D float32 copy of ``vector`` (or zeros)."""
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    if array.size == 0 or norm <= 1e-8 or not np.isfinite(array).all():
        return np.zeros_like(array)
    return array / norm


def collect_face_observations(
    analyzer: Any,
    crops: Sequence[Any],
    min_quality: float = 0.0,
    best_n: int = 5,
) -> tuple[list[FaceObservation], dict[str, int]]:
    """Analyze every crop, keep the best ``best_n`` faces by quality.

    ``analyzer`` follows the FaceAnalyzer protocol used by the live app:
    ``analyze(image) -> (embedding, measurements)`` raising ``ImageRejected``
    with a ``reason`` attribute for unusable crops. The demo's simulated
    analyzer implements the same protocol.
    """
    observations: list[FaceObservation] = []
    rejected: dict[str, int] = {}
    for image, source, origin in crops:
        try:
            embedding, measurements = analyzer.analyze(image)
        except Exception as exc:  # ImageRejected in live mode
            reason = getattr(exc, "reason", type(exc).__name__)
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        quality = float(measurements.get("quality_score", 0.0))
        if quality < min_quality:
            rejected["below_quality_minimum"] = (
                rejected.get("below_quality_minimum", 0) + 1
            )
            continue
        face_box = measurements.get("bbox")
        absolute_bbox = None
        if (
            origin is not None
            and isinstance(face_box, (list, tuple))
            and len(face_box) == 4
        ):
            absolute_bbox = [
                int(face_box[0] + origin[0]), int(face_box[1] + origin[1]),
                int(face_box[2] + origin[0]), int(face_box[3] + origin[1]),
            ]
        observations.append(FaceObservation(
            embedding=normalize_embedding(embedding),
            quality=quality,
            source=str(source),
            measurements=dict(measurements),
            absolute_bbox=absolute_bbox,
        ))
    observations.sort(key=lambda item: item.quality, reverse=True)
    if best_n > 0:
        observations = observations[:best_n]
    return observations, rejected


def best_observation(observations: Sequence[FaceObservation]) -> FaceObservation | None:
    """Highest-quality observation (used for evidence face crop + metadata)."""
    if not observations:
        return None
    return max(observations, key=lambda item: item.quality)


def aggregate_embeddings(
    observations: Sequence[FaceObservation],
    mode: str = "mean",
) -> np.ndarray | None:
    """Aggregate track face embeddings into one unit vector.

    ``mean``            — mean of the unit embeddings, re-normalized.
    ``weighted_mean``   — quality-weighted mean, re-normalized.
    ``first``          — the single best (highest quality) embedding.
    """
    if mode not in {"mean", "weighted_mean", "first"}:
        raise ValueError(f"Unknown face aggregation mode: {mode!r}")
    usable = [item for item in observations if item.embedding.size]
    if not usable:
        return None
    if mode == "first" or len(usable) == 1:
        return usable[0].embedding
    matrix = np.stack([item.embedding for item in usable])
    if mode == "weighted_mean":
        weights = np.asarray(
            [max(item.quality, 1e-6) for item in usable], dtype=np.float32,
        )
        matrix = matrix * (weights / weights.sum()).reshape(-1, 1)
    return normalize_embedding(matrix.mean(axis=0))


def rank_gallery(
    query: np.ndarray,
    gallery: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank gallery persons by cosine similarity to ``query``.

    ``gallery`` maps person_id -> {employee_code, full_name, department,
    embeddings: [vector, ...]}. A person's score is the max similarity over
    their embeddings (same semantics as the live worker's max strategy).
    """
    normalized_query = normalize_embedding(query)
    if not normalized_query.size or not gallery:
        return []
    vectors: list[np.ndarray] = []
    owners: list[int] = []
    for person_id, person in gallery.items():
        for vector in person.get("embeddings", ()):
            normalized = normalize_embedding(vector)
            if normalized.size == normalized_query.size:
                vectors.append(normalized)
                owners.append(int(person_id))
    if not vectors:
        return []
    scores = np.stack(vectors) @ normalized_query
    best: dict[int, float] = {}
    for person_id, score in zip(owners, scores):
        pid = int(person_id)
        best[pid] = max(best.get(pid, -1.0), float(score))
    ranked = []
    for person_id, score in best.items():
        person = gallery[person_id]
        ranked.append({
            "person_id": person_id,
            "employee_code": person.get("employee_code"),
            "full_name": person.get("full_name"),
            "department": person.get("department"),
            "similarity": score,
        })
    return sorted(ranked, key=lambda item: (-item["similarity"], item["person_id"]))


@dataclass
class IdentityDecision:
    status: str
    person_id: int | None = None
    score: float | None = None
    margin: float | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    aggregated: bool = False  # True when decided from >1 face observation
    observation_count: int = 0


def decide_identity(
    ranked: Sequence[dict[str, Any]],
    match_threshold: float,
    suggest_threshold: float,
    min_margin: float,
    aggregated: bool = False,
    observation_count: int = 0,
) -> IdentityDecision:
    """Threshold + margin decision with the existing status vocabulary.

    * MATCHED        — top score >= match_threshold AND (single gallery entry
      OR top1-top2 margin > min_margin)  -> never on a lucky single frame.
    * NEEDS_REVIEW   — score in [suggest_threshold, match_threshold) or a
      strong score without enough margin over the runner-up.
    * UNKNOWN        — below suggest_threshold (match score still recorded).
    """
    if not ranked:
        return IdentityDecision(STATUS_UNKNOWN, candidates=[
            dict(item) for item in []
        ], aggregated=aggregated, observation_count=observation_count)
    best = ranked[0]
    margin = (
        float(best["similarity"] - ranked[1]["similarity"])
        if len(ranked) > 1 else 0.0
    )
    score = float(best["similarity"])
    candidates = [dict(item) for item in list(ranked)[:5]]
    if score >= match_threshold and (len(ranked) == 1 or margin > min_margin):
        status = STATUS_MATCHED
        person_id = int(best["person_id"])
    elif score >= suggest_threshold:
        status = STATUS_NEEDS_REVIEW
        person_id = int(best["person_id"])
    else:
        return IdentityDecision(
            STATUS_UNKNOWN, None, score, margin, candidates,
            aggregated, observation_count,
        )
    return IdentityDecision(
        status, person_id, score, margin, candidates,
        aggregated, observation_count,
    )


class TrackIdentityCache:
    """TTL cache of settled (camera, track) -> IdentityDecision."""

    def __init__(self, ttl_seconds: float = 300.0):
        self.ttl_seconds = float(ttl_seconds)
        self._entries: dict[tuple[str, int], tuple[float, IdentityDecision]] = {}

    def get(self, camera_id: str, track_id: int) -> IdentityDecision | None:
        entry = self._entries.get((camera_id, int(track_id)))
        if entry is None:
            return None
        decided_at, decision = entry
        if time.monotonic() - decided_at > self.ttl_seconds:
            self._entries.pop((camera_id, int(track_id)), None)
            return None
        return decision

    def put(self, camera_id: str, track_id: int, decision: IdentityDecision) -> None:
        self._entries[(camera_id, int(track_id))] = (
            time.monotonic(), decision,
        )

    def prune(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        for key in [
            key for key, (at, _decision) in self._entries.items() if at < cutoff
        ]:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class IdentityThresholds:
    match_threshold: float = 0.60
    suggest_threshold: float = 0.45
    min_margin: float = 0.05


class TrackIdentityResolver:
    """Per-track best-N face collector + one-shot decision with caching.

    Feed observations as frames arrive (``add_observation``), then either:
      * ``try_decide``  — returns a decision once >= ``min_faces`` usable
        faces were seen (or a cached one), else None (wait for better frames);
      * ``finalize``    — force a decision when the track ends (faces seen so
        far; UNKNOWN if none usable — callers map "no face at all" to
        FACE_NOT_VISIBLE themselves, mirroring the live worker).
    """

    def __init__(
        self,
        thresholds: IdentityThresholds,
        gallery: dict[int, dict[str, Any]] | None = None,
        cache: TrackIdentityCache | None = None,
        best_n: int = 5,
        min_faces: int = 1,
        aggregation_mode: str = "mean",
        max_wait_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if best_n < 1:
            raise ValueError("best_n must be >= 1")
        if min_faces < 1:
            raise ValueError("min_faces must be >= 1")
        self.thresholds = thresholds
        self.gallery = gallery if gallery is not None else {}
        self.cache = cache if cache is not None else TrackIdentityCache()
        self.best_n = int(best_n)
        self.min_faces = int(min_faces)
        self.aggregation_mode = aggregation_mode
        self.max_wait_seconds = float(max_wait_seconds)
        self.clock = clock
        self._observations: dict[
            tuple[str, int], list[FaceObservation]
        ] = {}
        self._first_seen: dict[tuple[str, int], float] = {}

    def set_gallery(self, gallery: dict[int, dict[str, Any]]) -> None:
        self.gallery = gallery

    def add_observation(
        self, camera_id: str, track_id: int,
        observation: FaceObservation,
    ) -> None:
        key = (camera_id, int(track_id))
        bucket = self._observations.setdefault(key, [])
        if self._first_seen.get(key) is None:
            self._first_seen[key] = self.clock()
        bucket.append(observation)
        # Keep only the best N by quality — a late, better face replaces an
        # early, worse one instead of growing without bound.
        bucket.sort(key=lambda item: item.quality, reverse=True)
        del bucket[self.best_n:]

    def _match(self, key: tuple[str, int]) -> IdentityDecision:
        observations = self._observations.get(key, [])
        aggregated = aggregate_embeddings(observations, self.aggregation_mode)
        if aggregated is None or not aggregated.size:
            return IdentityDecision(
                STATUS_UNKNOWN, observation_count=len(observations),
            )
        ranked = rank_gallery(aggregated, self.gallery)
        decision = decide_identity(
            ranked,
            self.thresholds.match_threshold,
            self.thresholds.suggest_threshold,
            self.thresholds.min_margin,
            aggregated=len(observations) > 1,
            observation_count=len(observations),
        )
        return decision

    def try_decide(
        self, camera_id: str, track_id: int,
    ) -> IdentityDecision | None:
        """Cached decision, or a fresh one once enough faces were seen."""
        key = (camera_id, int(track_id))
        cached = self.cache.get(camera_id, track_id)
        if cached is not None:
            return cached
        observations = self._observations.get(key, [])
        waited = self.clock() - self._first_seen.get(key, self.clock())
        if len(observations) < self.min_faces and waited < self.max_wait_seconds:
            return None  # keep waiting for a better frame (e.g. phone tilt)
        decision = self._match(key)
        if decision.status != STATUS_UNKNOWN or len(observations) >= self.min_faces:
            self.cache.put(camera_id, track_id, decision)
            return decision
        return None

    def finalize(self, camera_id: str, track_id: int) -> IdentityDecision:
        """Force the decision when the violation is confirmed / track ends."""
        key = (camera_id, int(track_id))
        decision = self._match(key)
        self.cache.put(camera_id, track_id, decision)
        self._observations.pop(key, None)
        self._first_seen.pop(key, None)
        return decision

    def forget(self, camera_id: str, track_id: int) -> None:
        key = (camera_id, int(track_id))
        self._observations.pop(key, None)
        self._first_seen.pop(key, None)
