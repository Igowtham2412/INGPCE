import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import config
from app.main import app


class FaceApiAccessTests(unittest.TestCase):
    def _client_as(self, role):
        auth_patch = patch.object(config, "AUTH_ENABLED", True)
        auth_patch.start()
        self.addCleanup(auth_patch.stop)
        session = {
            "user_id": 17,
            "email": "reviewer@example.test",
            "role": role,
            "csrf_token": "csrf-test-token",
        }
        token_patch = patch(
            "app.main.auth.authenticate_token",
            new=AsyncMock(return_value=session),
        )
        token_patch.start()
        self.addCleanup(token_patch.stop)
        return TestClient(app, raise_server_exceptions=False)

    def test_operator_cannot_view_enrollment_identities(self):
        client = self._client_as("operator")
        with patch(
            "app.main.db.list_face_enrollments",
            new=AsyncMock(return_value=[]),
        ) as list_enrollments:
            response = client.get("/api/face/enrollments")

        self.assertEqual(response.status_code, 403)
        list_enrollments.assert_not_awaited()

    def test_operator_cannot_access_face_matches_or_status(self):
        client = self._client_as("operator")
        with patch(
            "app.main.db.list_face_matches",
            new=AsyncMock(),
        ) as list_matches:
            matches = client.get("/api/face/matches")
            status = client.get("/api/face/status")

        self.assertEqual(matches.status_code, 403)
        self.assertEqual(status.status_code, 403)
        list_matches.assert_not_awaited()

    def test_manager_can_filter_face_matches_and_view_enabled_types(self):
        client = self._client_as("manager")
        with patch(
            "app.main.db.list_face_matches",
            new=AsyncMock(return_value=[{
                "incident_id": 21, "violation_type": "no_vest",
                "status": "Suggested", "full_name": "Example Person",
            }]),
        ) as list_matches, patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ) as audit:
            response = client.get("/api/face/matches?violation_type=no_vest")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["violation_type"], "no_vest")
        list_matches.assert_awaited_once_with("no_vest", 100, 0)
        self.assertEqual(audit.await_args.args[0], "face_matches_filtered")

        with patch.object(config, "FACE_VIOLATION_TYPES", frozenset({
            "no_helmet", "no_vest", "phone_detected",
        })), patch.object(config, "FACE_TRACK_REUSE_SECONDS", 120), patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ) as status_audit:
            status = client.get("/api/face/status")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            status.json()["enabled_violation_types"],
            ["no_helmet", "no_vest", "phone_detected"],
        )
        self.assertEqual(status_audit.await_args.args[0], "face_recognition_status_viewed")

    def test_face_analytics_accepts_violation_type_filter(self):
        client = self._client_as("manager")
        with patch(
            "app.main.db.get_face_repeat_violations",
            new=AsyncMock(return_value=[{
                "person_id": 1, "violation_type": "no_vest",
                "confirmed_violations": 2,
            }]),
        ) as analytics, patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ):
            response = client.get(
                "/api/face/analytics?violation_type=no_vest&window_hours=24",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["violation_type"], "no_vest")
        self.assertEqual(analytics.await_args.args[1:], (None, None, "no_vest"))

    def test_only_admin_can_view_readiness_and_request_is_audited(self):
        manager = self._client_as("manager")
        with patch(
            "app.main.db.count_active_face_enrollments",
            new=AsyncMock(return_value=0),
        ) as manager_count:
            manager_response = manager.get("/api/face/readiness")

        self.assertEqual(manager_response.status_code, 403)
        manager_count.assert_not_awaited()

        admin = self._client_as("admin")
        with patch(
            "app.main.db.count_active_face_enrollments",
            new=AsyncMock(return_value=0),
        ), patch(
            "app.faces.face_readiness.get_face_readiness",
            return_value={
                "ready_for_recognition": False,
                "feature_enabled": False,
                "active_enrolled_people": 0,
                "missing": ["No consented dataset"],
            },
        ), patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ) as audit:
            response = admin.get("/api/face/readiness")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ready_for_recognition"])
        self.assertEqual(audit.await_args.args[0], "face_readiness_checked")

    def test_manager_can_view_enrollments_and_view_is_audited(self):
        client = self._client_as("manager")
        with patch(
            "app.main.db.list_face_enrollments",
            new=AsyncMock(return_value=[{
                "person_id": 1, "employee_code": "E001",
                "full_name": "Example Person", "active": True,
            }]),
        ), patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ) as audit:
            response = client.get("/api/face/enrollments")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["full_name"], "Example Person")
        self.assertEqual(audit.await_args.args[0], "face_enrollments_viewed")

    def test_operator_incident_list_only_reports_face_match_availability(self):
        client = self._client_as("operator")
        with patch.object(config, "FACE_ENABLED", True), patch(
            "app.main.db.get_recent_violations",
            new=AsyncMock(return_value=[{
                "id": 7, "snapshot_path": None, "clean_snapshot_path": None,
                "video_path": None,
            }]),
        ), patch(
            "app.main.db.get_face_match_availability",
            new=AsyncMock(return_value={7}),
        ):
            response = client.get("/api/violations")

        self.assertEqual(response.status_code, 200)
        item = response.json()[0]
        self.assertTrue(item["face_recognition_enabled"])
        self.assertTrue(item["face_match_available"])
        self.assertNotIn("matched_full_name", item)
        self.assertNotIn("top3_candidates", item)

    def test_manager_cannot_permanently_delete_enrollment(self):
        client = self._client_as("manager")
        with patch(
            "app.main.db.remove_face_enrollment",
            new=AsyncMock(return_value={
                "photo_paths": [], "permanently_deleted": True,
            }),
        ) as remove:
            response = client.delete(
                "/api/face/enrollments/1",
                headers={"x-csrf-token": "csrf-test-token"},
            )

        self.assertEqual(response.status_code, 403)
        remove.assert_not_awaited()

    def test_failed_photo_cleanup_is_reported_and_audited(self):
        client = self._client_as("admin")
        with patch(
            "app.main.db.remove_face_enrollment",
            new=AsyncMock(return_value={
                "photo_paths": ["private-photo"], "permanently_deleted": True,
            }),
        ), patch(
            "app.main._remove_private_face_photos", return_value=1,
        ), patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ) as audit:
            response = client.delete(
                "/api/face/enrollments/1",
                headers={"x-csrf-token": "csrf-test-token"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("administrator cleanup", response.json()["detail"])
        self.assertEqual(audit.await_args.kwargs["details"]["photo_removal_failures"], 1)

    def test_admin_can_permanently_delete_enrollment_and_audit(self):
        client = self._client_as("admin")
        with patch(
            "app.main.db.remove_face_enrollment",
            new=AsyncMock(return_value={
                "photo_paths": [], "permanently_deleted": True,
            }),
        ) as remove, patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ) as audit:
            response = client.delete(
                "/api/face/enrollments/1",
                headers={"x-csrf-token": "csrf-test-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["deleted"])
        remove.assert_awaited_once_with(1, permanently=True)
        self.assertEqual(audit.await_args.args[0], "face_enrollment_deleted")

    def test_face_match_response_never_returns_encrypted_crop(self):
        client = self._client_as("manager")
        with patch(
            "app.main.db.get_violation_by_id",
            new=AsyncMock(return_value={"id": 7}),
        ), patch(
            "app.main.db.get_face_match",
            new=AsyncMock(return_value={
                "incident_id": 7, "status": "Suggested",
                "face_crop": b"encrypted-private-crop",
                "face_bbox": [1, 2, 3, 4],
                "face_quality": {}, "top3_candidates": [],
            }),
        ), patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ):
            response = client.get("/api/violations/7/face-match")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["face_crop_available"])
        self.assertNotIn("face_crop", response.json())

    def test_manager_can_view_crop_and_view_is_audited(self):
        client = self._client_as("manager")
        with patch(
            "app.main.db.get_face_match",
            new=AsyncMock(return_value={
                "status": "Identified", "face_crop": b"encrypted-private-crop",
            }),
        ), patch(
            "app.faces.face_dataset._encryption_key", return_value=b"k" * 32,
        ), patch(
            "app.faces.face_dataset.decrypt_face_crop", return_value=b"face crop jpeg",
        ), patch(
            "app.main.db.write_audit_log", new=AsyncMock(),
        ) as audit:
            response = client.get("/api/violations/7/face-crop")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"face crop jpeg")
        self.assertEqual(audit.await_args.args[0], "face_identity_viewed")

    def test_rejected_match_cannot_add_incident_crop_embedding(self):
        client = self._client_as("admin")
        with patch(
            "app.main.db.review_face_match", new=AsyncMock(),
        ) as review:
            response = client.post(
                "/api/violations/7/face-review",
                headers={"x-csrf-token": "csrf-test-token"},
                json={
                    "decision": "Rejected",
                    "add_crop_embedding": True,
                },
            )

        self.assertEqual(response.status_code, 422)
        review.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
