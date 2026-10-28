import unittest
from unittest.mock import AsyncMock, patch

from app import db


class CameraSeedSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_direct_camera_seeds_are_retired(self):
        direct_camera_ids = {
            f"D{camera_number}" for camera_number in range(1, 21)
        }

        self.assertFalse(
            any(camera["camera_id"] in direct_camera_ids
                for camera in db.config.SEED_CAMERAS)
        )
        self.assertTrue(
            direct_camera_ids.issubset(db.config.RETIRED_SEED_CAMERA_IDS)
        )

    async def test_retired_direct_camera_is_disabled_without_deleting_it(self):
        update_camera = AsyncMock()

        with (
            patch.object(db.config, "SEED_AREA_GROUPS", []),
            patch.object(db.config, "SEED_CAMERAS", []),
            patch.object(db.config, "RETIRED_SEED_CAMERA_IDS", ["D1"]),
            patch.object(db, "list_area_groups", new_callable=AsyncMock, return_value=[]),
            patch.object(
                db, "list_cameras", new_callable=AsyncMock,
                return_value=[{"camera_id": "D1", "enabled": True}],
            ),
            patch.object(db, "update_camera", update_camera),
            patch.object(db, "create_camera", new_callable=AsyncMock),
        ):
            result = await db.sync_seed_data()

        update_camera.assert_awaited_once_with("D1", enabled=False)
        self.assertEqual(result["cameras_disabled"], 1)
        self.assertEqual(result["disabled_camera_ids"], ["D1"])

    async def test_existing_seed_feed_refreshes_credentials_only_for_same_endpoints(self):
        seed = {
            "camera_id": "CAM11",
            "name": "CAM11",
            "rtsp_url": "rtsp://operator:current@camera-host/Streaming/Channels/501",
            "main_stream_url": "rtsp://operator:current@camera-host/Streaming/Channels/501",
            "sub_stream_url": "rtsp://operator:current@camera-host/Streaming/Channels/502",
            "required_ppe": ["helmet", "vest"],
        }
        existing = {
            "camera_id": "CAM11",
            "required_ppe": ["helmet", "vest"],
            "rtsp_url": "rtsp://camera-host/Streaming/Channels/501",
            "main_stream_url": "rtsp://camera-host/Streaming/Channels/501",
            "sub_stream_url": "rtsp://camera-host/Streaming/Channels/502",
            "enabled": True,
        }
        update_camera = AsyncMock()

        with (
            patch.object(db.config, "SEED_AREA_GROUPS", []),
            patch.object(db.config, "SEED_CAMERAS", [seed]),
            patch.object(db.config, "RETIRED_SEED_CAMERA_IDS", []),
            patch.object(db, "list_area_groups", new_callable=AsyncMock, return_value=[]),
            patch.object(db, "list_cameras", new_callable=AsyncMock, return_value=[existing]),
            patch.object(db, "update_camera", update_camera),
            patch.object(db, "create_camera", new_callable=AsyncMock),
        ):
            result = await db.sync_seed_data()

        update_camera.assert_awaited_once_with(
            "CAM11",
            rtsp_url=seed["main_stream_url"],
            main_stream_url=seed["main_stream_url"],
            sub_stream_url=seed["sub_stream_url"],
        )
        self.assertEqual(result["updated_camera_ids"], ["CAM11"])
        self.assertEqual(result["cameras_updated"], 1)

    async def test_seed_sync_does_not_replace_credentials_for_custom_endpoint(self):
        seed = {
            "camera_id": "CAM11",
            "name": "CAM11",
            "rtsp_url": "rtsp://operator:current@seed-host/Streaming/Channels/501",
            "main_stream_url": "rtsp://operator:current@seed-host/Streaming/Channels/501",
            "sub_stream_url": "rtsp://operator:current@seed-host/Streaming/Channels/502",
            "required_ppe": ["helmet", "vest"],
        }
        existing = {
            "camera_id": "CAM11",
            "required_ppe": ["helmet", "vest"],
            "rtsp_url": "rtsp://operator:custom@camera-host/Streaming/Channels/501",
            "main_stream_url": "rtsp://operator:custom@camera-host/Streaming/Channels/501",
            "sub_stream_url": "rtsp://operator:custom@camera-host/Streaming/Channels/502",
            "enabled": True,
        }
        update_camera = AsyncMock()

        with (
            patch.object(db.config, "SEED_AREA_GROUPS", []),
            patch.object(db.config, "SEED_CAMERAS", [seed]),
            patch.object(db.config, "RETIRED_SEED_CAMERA_IDS", []),
            patch.object(db, "list_area_groups", new_callable=AsyncMock, return_value=[]),
            patch.object(db, "list_cameras", new_callable=AsyncMock, return_value=[existing]),
            patch.object(db, "update_camera", update_camera),
            patch.object(db, "create_camera", new_callable=AsyncMock),
        ):
            result = await db.sync_seed_data()

        update_camera.assert_not_awaited()
        self.assertEqual(result["updated_camera_ids"], [])

    async def test_existing_mcc_camera_keeps_stream_urls_and_gets_arc_suit(self):
        seed = {
            "camera_id": "CAM07",
            "name": "CAM07",
            "rtsp_url": "rtsp://seed-host/Streaming/Channels/501",
            "main_stream_url": "rtsp://seed-host/Streaming/Channels/501",
            "sub_stream_url": "rtsp://seed-host/Streaming/Channels/502",
            "required_ppe": ["helmet", "vest", "arc_flash_suit"],
        }
        existing = {
            "camera_id": "CAM07",
            "required_ppe": ["helmet", "vest"],
            "rtsp_url": "rtsp://camera-host/Streaming/Channels/101",
            "main_stream_url": "rtsp://camera-host/Streaming/Channels/101",
            "sub_stream_url": "rtsp://camera-host/Streaming/Channels/102",
            "enabled": True,
        }

        with (
            patch.object(db.config, "SEED_AREA_GROUPS", []),
            patch.object(db.config, "SEED_CAMERAS", [seed]),
            patch.object(db.config, "RETIRED_SEED_CAMERA_IDS", []),
            patch.object(db, "list_area_groups", new_callable=AsyncMock, return_value=[]),
            patch.object(db, "list_cameras", new_callable=AsyncMock, return_value=[existing]),
            patch.object(db, "update_camera", new_callable=AsyncMock) as update_camera,
            patch.object(db, "create_camera", new_callable=AsyncMock) as create_camera,
        ):
            result = await db.sync_seed_data()

        update_camera.assert_awaited_once_with(
            "CAM07", required_ppe=["helmet", "vest", "arc_flash_suit"],
        )
        create_camera.assert_not_awaited()
        self.assertEqual(result["cameras_updated"], 1)
        self.assertEqual(result["cameras_already_existed"], 1)

    async def test_missing_camera_is_created_with_seed_stream_urls(self):
        seed = {
            "camera_id": "CAM07",
            "name": "CAM07",
            "rtsp_url": "rtsp://camera-host/Streaming/Channels/101",
            "main_stream_url": "rtsp://camera-host/Streaming/Channels/101",
            "sub_stream_url": "rtsp://camera-host/Streaming/Channels/102",
            "required_ppe": ["helmet", "vest", "arc_flash_suit"],
        }
        create_camera = AsyncMock()

        with (
            patch.object(db.config, "SEED_AREA_GROUPS", []),
            patch.object(db.config, "SEED_CAMERAS", [seed]),
            patch.object(db.config, "RETIRED_SEED_CAMERA_IDS", []),
            patch.object(db, "list_area_groups", new_callable=AsyncMock, return_value=[]),
            patch.object(db, "list_cameras", new_callable=AsyncMock, return_value=[]),
            patch.object(db, "update_camera", new_callable=AsyncMock),
            patch.object(db, "create_camera", create_camera),
        ):
            result = await db.sync_seed_data()

        create_camera.assert_awaited_once_with(
            "CAM07",
            "CAM07",
            "rtsp://camera-host/Streaming/Channels/101",
            None,
            ["helmet", "vest", "arc_flash_suit"],
            main_stream_url="rtsp://camera-host/Streaming/Channels/101",
            sub_stream_url="rtsp://camera-host/Streaming/Channels/102",
        )
        self.assertEqual(result["cameras_created"], 1)
        self.assertEqual(result["newly_created_camera_ids"], ["CAM07"])

    async def test_non_mcc_camera_seed_does_not_add_arc_suit(self):
        seed = {
            "camera_id": "CAM01",
            "name": "CAM01",
            "rtsp_url": "rtsp://seed-host/Streaming/Channels/101",
            "required_ppe": ["helmet", "vest"],
        }
        existing = {
            "camera_id": "CAM01",
            "required_ppe": ["helmet", "vest"],
            "rtsp_url": seed["rtsp_url"],
            "enabled": True,
        }
        update_camera = AsyncMock()

        with (
            patch.object(db.config, "SEED_AREA_GROUPS", []),
            patch.object(db.config, "SEED_CAMERAS", [seed]),
            patch.object(db.config, "RETIRED_SEED_CAMERA_IDS", []),
            patch.object(db, "list_area_groups", new_callable=AsyncMock, return_value=[]),
            patch.object(db, "list_cameras", new_callable=AsyncMock, return_value=[existing]),
            patch.object(db, "update_camera", update_camera),
            patch.object(db, "create_camera", new_callable=AsyncMock),
        ):
            result = await db.sync_seed_data()

        update_camera.assert_not_awaited()
        self.assertEqual(result["cameras_updated"], 0)


if __name__ == "__main__":
    unittest.main()
