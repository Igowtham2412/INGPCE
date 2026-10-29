"""Unit tests for the track-based identification core (matcher, aggregation,
decision statuses, resolver caching). Pure numpy — runs anywhere."""
import unittest

import numpy as np

from app.faces.track_identity import (
    FaceObservation,
    IdentityThresholds,
    TrackIdentityCache,
    TrackIdentityResolver,
    aggregate_embeddings,
    decide_identity,
    normalize_embedding,
    rank_gallery,
    STATUS_MATCHED,
    STATUS_NEEDS_REVIEW,
    STATUS_UNKNOWN,
)


class FakeClock:
    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_gallery(rng, person_count=3, dim=64, embeddings_per_person=1):
    gallery = {}
    for person_id in range(1, person_count + 1):
        vectors = [
            normalize_embedding(rng.normal(size=dim))
            for _ in range(embeddings_per_person)
        ]
        gallery[person_id] = {
            "employee_code": f"EMP{person_id:03d}",
            "full_name": f"Person {person_id}",
            "department": "Test",
            "embeddings": vectors,
        }
    return gallery


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(1234)
        self.gallery = make_gallery(self.rng)

    def test_known_face_matches_with_margin(self):
        probe = self.gallery[2]["embeddings"][0] + 0.03 * self.rng.normal(size=64)
        decision = decide_identity(
            rank_gallery(probe, self.gallery), 0.60, 0.45, 0.05,
        )
        self.assertEqual(decision.status, STATUS_MATCHED)
        self.assertEqual(decision.person_id, 2)
        self.assertGreater(decision.score, 0.60)
        self.assertGreater(decision.margin, 0.05)

    def test_unknown_face_below_threshold_is_unknown_with_score_kept(self):
        probe = normalize_embedding(self.rng.normal(size=64))
        decision = decide_identity(
            rank_gallery(probe, self.gallery), 0.60, 0.45, 0.05,
        )
        self.assertEqual(decision.status, STATUS_UNKNOWN)
        self.assertIsNone(decision.person_id)
        self.assertIsNotNone(decision.score, "match score must always be stored")

    def test_borderline_score_goes_to_needs_review(self):
        ranked = [
            {"person_id": 1, "similarity": 0.50},
            {"person_id": 2, "similarity": 0.20},
        ]
        decision = decide_identity(ranked, 0.60, 0.45, 0.05)
        self.assertEqual(decision.status, STATUS_NEEDS_REVIEW)
        self.assertEqual(decision.person_id, 1)

    def test_high_score_without_margin_goes_to_needs_review(self):
        """Two lookalikes: strong score, tiny margin -> never auto-identify."""
        ranked = [
            {"person_id": 1, "similarity": 0.70},
            {"person_id": 2, "similarity": 0.69},
        ]
        decision = decide_identity(ranked, 0.60, 0.45, 0.05)
        self.assertEqual(decision.status, STATUS_NEEDS_REVIEW)

    def test_single_gallery_entry_matches_without_margin_requirement(self):
        ranked = [{"person_id": 1, "similarity": 0.80}]
        decision = decide_identity(ranked, 0.60, 0.45, 0.05)
        self.assertEqual(decision.status, STATUS_MATCHED)

    def test_empty_gallery_is_unknown(self):
        decision = decide_identity(rank_gallery(np.ones(8), {}), 0.6, 0.45, 0.05)
        self.assertEqual(decision.status, STATUS_UNKNOWN)


class AggregationTests(unittest.TestCase):
    def test_mean_aggregation_renormalizes_to_unit_length(self):
        observations = [
            FaceObservation(embedding=normalize_embedding(v), quality=0.5)
            for v in [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        ]
        aggregated = aggregate_embeddings(observations, "mean")
        self.assertAlmostEqual(float(np.linalg.norm(aggregated)), 1.0, places=5)
        self.assertTrue((aggregated > 0).all())

    def test_weighted_mean_favors_high_quality_faces(self):
        observations = [
            FaceObservation(embedding=np.array([1.0, 0.0]), quality=1.0),
            FaceObservation(embedding=np.array([0.0, 1.0]), quality=0.01),
        ]
        aggregated = aggregate_embeddings(observations, "weighted_mean")
        self.assertGreater(aggregated[0], aggregated[1] * 10)

    def test_aggregation_improves_score_over_single_noisy_frame(self):
        """Averaging multiple noisy observations beats the worst single one."""
        rng = np.random.default_rng(7)
        base = normalize_embedding(rng.normal(size=128))
        gallery = {1: {"employee_code": "E", "embeddings": [base]}}
        noisy = [
            normalize_embedding(base + 0.5 * rng.normal(size=128) / np.sqrt(128))
            for _ in range(5)
        ]
        observations = [
            FaceObservation(embedding=vec, quality=0.8) for vec in noisy
        ]
        single_best = max(
            rank_gallery(vec, gallery)[0]["similarity"] for vec in noisy
        )
        aggregated = aggregate_embeddings(observations, "mean")
        aggregated_score = rank_gallery(aggregated, gallery)[0]["similarity"]
        self.assertGreaterEqual(aggregated_score, single_best - 1e-6)

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            aggregate_embeddings(
                [FaceObservation(embedding=np.array([1.0]), quality=1.0)],
                "bogus",
            )


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(99)
        self.gallery = make_gallery(self.rng)
        self.clock = FakeClock()

    def make_resolver(self, **kwargs):
        settings = dict(
            thresholds=IdentityThresholds(0.60, 0.45, 0.05),
            gallery=self.gallery,
            best_n=5,
            min_faces=3,
            aggregation_mode="mean",
            max_wait_seconds=10,
            clock=self.clock,
        )
        settings.update(kwargs)
        resolver = TrackIdentityResolver(**settings)
        return resolver

    def observe_for(self, resolver, person_id, track_id, count, camera="cam-1"):
        base = self.gallery[person_id]["embeddings"][0]
        for _ in range(count):
            noise = 0.3 * self.rng.normal(size=64) / np.sqrt(64)
            resolver.add_observation(
                camera, track_id,
                FaceObservation(
                    embedding=normalize_embedding(base + noise), quality=0.8,
                ),
            )

    def test_waits_for_min_faces_before_deciding(self):
        resolver = self.make_resolver()
        self.observe_for(resolver, 1, 7, count=2)
        self.assertIsNone(resolver.try_decide("cam-1", 7))
        self.observe_for(resolver, 1, 7, count=1)
        decision = resolver.try_decide("cam-1", 7)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.status, STATUS_MATCHED)
        self.assertEqual(decision.person_id, 1)
        self.assertTrue(decision.aggregated)
        self.assertEqual(decision.observation_count, 3)

    def test_best_n_cap_keeps_only_highest_quality(self):
        resolver = self.make_resolver(best_n=3)
        base = self.gallery[1]["embeddings"][0]
        for quality in (0.1, 0.2, 0.3, 0.4, 0.95, 0.5):
            resolver.add_observation(
                "cam-1", 1,
                FaceObservation(
                    embedding=normalize_embedding(base), quality=quality,
                ),
            )
        bucket = resolver._observations[("cam-1", 1)]
        self.assertEqual(len(bucket), 3)
        self.assertEqual([obs.quality for obs in bucket], [0.95, 0.5, 0.4])

    def test_decision_is_cached_per_track(self):
        resolver = self.make_resolver(min_faces=1)
        self.observe_for(resolver, 1, 3, count=1)
        first = resolver.try_decide("cam-1", 3)
        self.observe_for(resolver, 1, 3, count=4)  # more faces arrive later
        again = resolver.try_decide("cam-1", 3)
        self.assertIs(first, again, "cached decision must be reused")

    def test_cache_expires_after_ttl(self):
        cache = TrackIdentityCache(ttl_seconds=30)
        decision = decide_identity([], 0.6, 0.45, 0.05)
        cache.put("cam-1", 5, decision)
        self.assertIsNotNone(cache.get("cam-1", 5))
        # time passes: use a monotonic-style offset via direct entry patch
        entry = cache._entries[("cam-1", 5)]
        cache._entries[("cam-1", 5)] = (entry[0] - 60, entry[1])
        self.assertIsNone(cache.get("cam-1", 5))

    def test_no_face_at_all_finalizes_unknown(self):
        resolver = self.make_resolver()
        decision = resolver.finalize("cam-1", 42)
        self.assertEqual(decision.status, STATUS_UNKNOWN)
        self.assertEqual(decision.observation_count, 0)

    def test_waits_then_decides_after_max_wait(self):
        resolver = self.make_resolver(min_faces=3, max_wait_seconds=5)
        self.observe_for(resolver, 2, 9, count=1)
        self.assertIsNone(resolver.try_decide("cam-1", 9))
        self.clock.advance(6)
        decision = resolver.try_decide("cam-1", 9)
        self.assertIsNotNone(decision, "max wait must force a decision")


if __name__ == "__main__":
    unittest.main()
