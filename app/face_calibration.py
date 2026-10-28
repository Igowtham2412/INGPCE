"""Offline threshold calibration from consented references and labeled crops."""
import csv
import hashlib
import json
import math
import secrets
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import config
from .face_dataset import (
    FaceAnalyzer,
    ImageRejected,
    _cosine_similarity,
    discover_dataset,
)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_UNKNOWN_LABEL = "not_in_dataset"


@dataclass(frozen=True)
class CalibrationSample:
    label: str
    camera: str
    embedding: np.ndarray
    violation_type: str = "unspecified_type"
    quality_score: float | None = None


def _load_sample(
        image_path: Path, label: str, camera: str,
        analyzer: FaceAnalyzer,
        violation_type: str = "primary_dataset") -> CalibrationSample:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ImageRejected("unreadable")
    embedding, measurements = analyzer.analyze(image)
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if vector.size == 0 or not np.isfinite(vector).all() or norm < 1e-8:
        raise ImageRejected("invalid_embedding")
    return CalibrationSample(
        label, camera, vector / norm, violation_type,
        float(measurements["quality_score"]),
    )


def load_reference_samples(
        dataset_path: Path, analyzer: FaceAnalyzer,
        consent_name: str | None = None,
        consent_date: str | None = None,
        consent_reference: str | None = None,
) -> tuple[dict[str, list[CalibrationSample]], dict[str, int]]:
    """Extract quality-approved embeddings without persisting photos or vectors."""
    people = discover_dataset(
        dataset_path, consent_name, consent_date, consent_reference,
    )
    accepted: dict[str, list[CalibrationSample]] = defaultdict(list)
    rejected: Counter[str] = Counter()
    seen_hashes: dict[str, tuple[str, CalibrationSample | None]] = {}
    for employee_code, person in people.items():
        for image_record in person.images:
            try:
                image_hash = hashlib.sha256(image_record.path.read_bytes()).hexdigest()
            except OSError:
                rejected["unreadable"] += 1
                continue
            previous = seen_hashes.get(image_hash)
            if previous is not None:
                previous_label, previous_sample = previous
                if previous_label != employee_code:
                    if previous_sample is not None:
                        accepted[previous_label] = [
                            sample for sample in accepted[previous_label]
                            if sample is not previous_sample
                        ]
                    seen_hashes[image_hash] = ("cross_identity_duplicate", None)
                    rejected["cross_identity_exact_duplicate"] += 1
                else:
                    rejected["exact_duplicate"] += 1
                continue
            try:
                sample = _load_sample(
                    image_record.path, employee_code, "primary_dataset", analyzer,
                )
                accepted[employee_code].append(sample)
                seen_hashes[image_hash] = (employee_code, sample)
            except ImageRejected as exc:
                rejected[exc.reason] += 1
    return dict(accepted), dict(rejected)


def load_labeled_incident_crops(
        crop_root: Path, enrolled_codes: Iterable[str],
        analyzer: FaceAnalyzer,
) -> tuple[list[CalibrationSample], dict[str, int], dict[str, int]]:
    """Read labeled crops, including camera/type/employee folders."""
    root = crop_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Incident crop path must be a directory")
    labels = set(enrolled_codes)
    top_level_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    direct_layout = any(
        any(path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
            for path in folder.iterdir())
        for folder in top_level_dirs
    )
    nested_layout = any(
        any(
            child.is_dir()
            and any(path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
                    for path in child.iterdir())
            for child in folder.iterdir()
        )
        for folder in top_level_dirs
    )
    typed_layout = any(
        grandchild.is_dir()
        and any(
            path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
            for path in grandchild.iterdir()
        )
        for folder in top_level_dirs
        for child in folder.iterdir()
        if child.is_dir()
        for grandchild in child.iterdir()
    )
    if sum((direct_layout, nested_layout, typed_layout)) > 1:
        raise ValueError("Incident crop folders cannot mix layouts")

    candidates: list[tuple[Path, str, str, str]] = []
    if direct_layout:
        for label_dir in top_level_dirs:
            label = _normalize_label(label_dir.name, labels)
            candidates.extend(
                (path, label, "unspecified_camera", "unspecified_type")
                for path in sorted(label_dir.iterdir())
                if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
            )
    elif nested_layout:
        for camera_dir in top_level_dirs:
            for label_dir in sorted(
                    path for path in camera_dir.iterdir() if path.is_dir()):
                label = _normalize_label(label_dir.name, labels)
                candidates.extend(
                    (path, label, camera_dir.name, "unspecified_type")
                    for path in sorted(label_dir.iterdir())
                    if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
                )
    elif typed_layout:
        for camera_dir in top_level_dirs:
            for type_dir in sorted(
                    path for path in camera_dir.iterdir() if path.is_dir()):
                violation_type = _normalize_violation_type(type_dir.name)
                for label_dir in sorted(
                        path for path in type_dir.iterdir() if path.is_dir()):
                    label = _normalize_label(label_dir.name, labels)
                    candidates.extend(
                        (path, label, camera_dir.name, violation_type)
                        for path in sorted(label_dir.iterdir())
                        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
                    )
    else:
        raise ValueError("No labeled incident-crop folders were found")

    samples: list[CalibrationSample] = []
    rejected: Counter[str] = Counter()
    per_camera_total: Counter[str] = Counter()
    seen_hashes: dict[str, tuple[str, CalibrationSample | None]] = {}
    for image_path, label, camera, violation_type in candidates:
        per_camera_total[camera] += 1
        try:
            image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
        except OSError:
            rejected["unreadable"] += 1
            continue
        previous = seen_hashes.get(image_hash)
        if previous is not None:
            previous_label, previous_sample = previous
            if previous_label == "cross_identity_duplicate":
                rejected["cross_identity_exact_duplicate"] += 1
            elif previous_label != label:
                if previous_sample is not None:
                    samples = [
                        sample for sample in samples if sample is not previous_sample
                    ]
                seen_hashes[image_hash] = ("cross_identity_duplicate", None)
                rejected["cross_identity_exact_duplicate"] += 1
            else:
                rejected["exact_duplicate"] += 1
            continue
        try:
            sample = _load_sample(
                image_path, label, camera, analyzer, violation_type,
            )
            minimum_quality = config.FACE_QUALITY_MIN_BY_VIOLATION.get(
                violation_type, 0.0,
            )
            if (
                sample.quality_score is not None
                and sample.quality_score < minimum_quality
            ):
                rejected["below_violation_quality_minimum"] += 1
                continue
            samples.append(sample)
            seen_hashes[image_hash] = (label, sample)
        except ImageRejected as exc:
            rejected[exc.reason] += 1
    return samples, dict(rejected), dict(per_camera_total)


def _normalize_label(folder_name: str, enrolled_codes: set[str]) -> str:
    if folder_name.casefold() == _UNKNOWN_LABEL:
        return _UNKNOWN_LABEL
    if folder_name not in enrolled_codes:
        raise ValueError(
            "Incident crop labels must match an enrolled employee code or "
            "'not_in_dataset'"
        )
    return folder_name

def _normalize_violation_type(folder_name: str) -> str:
    aliases = {
        "missing_helmet": "no_helmet", "no_helmet": "no_helmet",
        "missing_vest": "no_vest", "no_vest": "no_vest",
        "phone_detected": "phone_detected",
    }
    violation_type = aliases.get(folder_name.strip().lower())
    if violation_type is None:
        raise ValueError("Incident-crop type folders must be helmet, vest, or phone")
    return violation_type


def _best_scores(
        query: np.ndarray, gallery: dict[str, Sequence[np.ndarray]],
        strategy: str | None = None, top_k: int | None = None,
) -> dict[str, float]:
    match_strategy = strategy or config.FACE_MATCH_STRATEGY
    count = top_k or config.FACE_MATCH_TOP_K
    if match_strategy not in {"max", "top_k_mean"} or count < 1:
        raise ValueError("Invalid face match strategy for calibration")
    scores = {}
    for label, embeddings in gallery.items():
        person_scores = sorted(
            (_cosine_similarity(query, reference) for reference in embeddings),
            reverse=True,
        )
        if not person_scores:
            continue
        scores[label] = (
            max(person_scores) if match_strategy == "max"
            else float(np.mean(person_scores[:count]))
        )
    return scores


def leave_one_out_queries(
        references: dict[str, list[CalibrationSample]],
) -> list[dict[str, Any]]:
    """Score every reference against other photos, excluding itself."""
    queries = []
    for label, samples in references.items():
        if len(samples) < 2:
            continue
        for index, sample in enumerate(samples):
            gallery = {
                other_label: [item.embedding for item in other_samples]
                for other_label, other_samples in references.items()
                if other_label != label and other_samples
            }
            same_person = [
                item.embedding for item_index, item in enumerate(samples)
                if item_index != index
            ]
            if same_person:
                gallery[label] = same_person
            scores = _best_scores(sample.embedding, gallery)
            queries.append({
                "label": label,
                "camera": "primary_dataset",
                "violation_type": sample.violation_type,
                "quality_score": sample.quality_score,
                "scores": scores,
            })
    return queries


def incident_queries(
        samples: Sequence[CalibrationSample],
        references: dict[str, list[CalibrationSample]],
        strategy: str | None = None, top_k: int | None = None,
) -> list[dict[str, Any]]:
    gallery = {
        label: [sample.embedding for sample in employee_samples]
        for label, employee_samples in references.items()
        if employee_samples
    }
    return [
        {
            "label": sample.label,
            "camera": sample.camera,
            "violation_type": sample.violation_type,
            "quality_score": sample.quality_score,
            "scores": _best_scores(sample.embedding, gallery, strategy, top_k),
        }
        for sample in samples
    ]


def _recommend_match_threshold(
        impostor_scores: Sequence[float], target_far: float,
) -> tuple[float | None, int]:
    if not impostor_scores:
        return None, 0
    allowed_false_accepts = math.floor(target_far * len(impostor_scores))
    ordered = sorted(float(score) for score in impostor_scores)
    index = max(0, len(ordered) - allowed_false_accepts - 1)
    return ordered[index], allowed_false_accepts


def _recommend_suggestion_threshold(
        genuine_scores: Sequence[float],
        match_threshold: float | None,
) -> float | None:
    if not genuine_scores:
        return None
    # A lower-recall point is only a review suggestion, never an identification.
    suggestion = float(np.quantile(genuine_scores, 0.05, method="lower"))
    if match_threshold is not None:
        suggestion = min(suggestion, match_threshold)
    return suggestion


def _candidate_order(scores: dict[str, float]) -> list[tuple[str, float]]:
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def evaluate_queries(
        queries: Sequence[dict[str, Any]], target_far: float,
        min_margin: float = 0.0,
) -> dict[str, Any]:
    """Report empirical rates and conservative per-group score thresholds."""
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for query in queries:
        by_group[(
            query.get("violation_type", "unspecified_type"),
            query["camera"],
        )].append(query)

    groups: dict[str, Any] = {}
    all_histogram_scores: dict[
        tuple[str, str, str], list[float]
    ] = defaultdict(list)
    type_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: Counter(),
    )
    for (violation_type, camera), group_queries in sorted(by_group.items()):
        genuine_scores: list[float] = []
        impostor_scores: list[float] = []
        candidates: list[tuple[dict[str, Any], tuple[str, float] | None, float]] = []

        for query in group_queries:
            scores = query["scores"]
            ordered = _candidate_order(scores)
            top = ordered[0] if ordered else None
            margin = (
                top[1] - ordered[1][1] if top is not None and len(ordered) > 1
                else 0.0
            )
            label = query["label"]
            if label == _UNKNOWN_LABEL:
                if top is not None:
                    impostor_scores.append(top[1])
                    all_histogram_scores[
                        (violation_type, camera, "impostor")
                    ].append(top[1])
            else:
                genuine = scores.get(label)
                if genuine is not None:
                    genuine_scores.append(genuine)
                    all_histogram_scores[
                        (violation_type, camera, "genuine")
                    ].append(genuine)
                wrong_scores = [
                    score for candidate, score in scores.items()
                    if candidate != label
                ]
                if wrong_scores:
                    impostor_scores.append(max(wrong_scores))
                    all_histogram_scores[
                        (violation_type, camera, "impostor")
                    ].append(max(wrong_scores))
            candidates.append((query, top, margin))

        match_threshold, allowed_false_accepts = _recommend_match_threshold(
            impostor_scores, target_far,
        )
        suggest_threshold = _recommend_suggestion_threshold(
            genuine_scores, match_threshold,
        )
        accepted = suggested = not_identified = false_accepts = true_accepts = 0
        known_count = sum(
            query["label"] != _UNKNOWN_LABEL for query in group_queries
        )
        unknown_count = len(group_queries) - known_count

        quality_scores = [
            float(query["quality_score"])
            for query in group_queries
            if query.get("quality_score") is not None
        ]
        for query, top, margin in candidates:
            identified = bool(
                top is not None
                and match_threshold is not None
                and top[1] > match_threshold
                and margin > min_margin
            )
            if identified:
                accepted += 1
                correct = query["label"] != _UNKNOWN_LABEL and top[0] == query["label"]
                if correct:
                    true_accepts += 1
                else:
                    false_accepts += 1
            elif top is not None and suggest_threshold is not None and top[1] > suggest_threshold:
                suggested += 1
            else:
                not_identified += 1

        adequate_samples = (
            len(impostor_scores) >= math.ceil(1.0 / target_far)
            if target_far > 0 else False
        )
        groups[f"{violation_type}|{camera}"] = {
            "queries": len(group_queries),
            "known_queries": known_count,
            "unknown_queries": unknown_count,
            "genuine_score_samples": len(genuine_scores),
            "impostor_score_samples": len(impostor_scores),
            "target_false_accept_rate": target_far,
            "max_empirical_false_accepts": allowed_false_accepts,
            "recommended_match_threshold": match_threshold,
            "recommended_suggest_threshold": suggest_threshold,
            "minimum_margin_used": min_margin,
            "true_accept_rate": (
                true_accepts / known_count if known_count else None
            ),
            "false_accept_rate": (
                false_accepts / len(impostor_scores)
                if impostor_scores else None
            ),
            "not_identified_rate": (
                not_identified / len(group_queries) if group_queries else None
            ),
            "suggested_rate": (
                suggested / len(group_queries) if group_queries else None
            ),
            "violation_type": violation_type,
            "camera": camera,
            "quality_score_min": min(quality_scores) if quality_scores else None,
            "quality_score_median": (
                float(np.median(quality_scores)) if quality_scores else None
            ),
            "identified_count": accepted,
            "suggested_count": suggested,
            "not_identified_count": not_identified,
            "false_accept_count": false_accepts,
            "threshold_sample_size_adequate": adequate_samples,
            "minimum_impostor_samples_for_target": math.ceil(1.0 / target_far),
        }
        totals = type_totals[violation_type]
        totals["queries"] += len(group_queries)
        totals["known_queries"] += known_count
        totals["unknown_queries"] += unknown_count
        totals["true_accept_count"] += true_accepts
        totals["false_accept_count"] += false_accepts
        totals["impostor_score_samples"] += len(impostor_scores)
        totals["not_identified_count"] += not_identified

    by_violation_type = {}
    for violation_type, totals in sorted(type_totals.items()):
        by_violation_type[violation_type] = {
            **totals,
            "true_accept_rate": (
                totals["true_accept_count"] / totals["known_queries"]
                if totals["known_queries"] else None
            ),
            "false_accept_rate": (
                totals["false_accept_count"] / totals["impostor_score_samples"]
                if totals["impostor_score_samples"] else None
            ),
            "not_identified_rate": (
                totals["not_identified_count"] / totals["queries"]
                if totals["queries"] else None
            ),
        }

    histogram = _score_histogram(all_histogram_scores)
    return {
        "groups": groups,
        "by_violation_type": by_violation_type,
        "histogram": histogram,
        "query_count": len(queries),
    }


def _score_histogram(
        scores_by_group: dict[tuple[str, str, str], Sequence[float]],
        bins: int = 40,
) -> list[dict[str, Any]]:
    edges = np.linspace(-1.0, 1.0, bins + 1)
    rows = []
    for (violation_type, camera, kind), scores in sorted(scores_by_group.items()):
        counts, _edges = np.histogram(scores, bins=edges)
        for index, count in enumerate(counts):
            rows.append({
                "violation_type": violation_type,
                "camera": camera,
                "score_type": kind,
                "bin_start": round(float(edges[index]), 4),
                "bin_end": round(float(edges[index + 1]), 4),
                "count": int(count),
            })
    return rows


def write_calibration_report(
        result: dict[str, Any], output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    json_path = output_dir / f"face-calibration-{timestamp}-{suffix}.json"
    histogram_path = output_dir / f"face-calibration-histogram-{timestamp}-{suffix}.csv"
    with json_path.open("w", encoding="utf-8") as report_file:
        json.dump(result, report_file, indent=2, sort_keys=True)
    with histogram_path.open("w", encoding="utf-8-sig", newline="") as histogram_file:
        writer = csv.DictWriter(histogram_file, fieldnames=[
            "violation_type", "camera", "score_type",
            "bin_start", "bin_end", "count",
        ])
        writer.writeheader()
        writer.writerows(result["histogram"])
    return json_path, histogram_path
