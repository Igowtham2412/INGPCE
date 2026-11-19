import unittest
from urllib.parse import urlsplit

from app import config


class SeedCameraConfigTests(unittest.TestCase):
    def test_seeded_camera_ids_are_unique_and_have_usable_stream_urls(self):
        camera_ids = [camera["camera_id"] for camera in config.SEED_CAMERAS]
        self.assertEqual(len(camera_ids), len(set(camera_ids)))

        for camera in config.SEED_CAMERAS:
            main_url = urlsplit(camera["main_stream_url"])
            sub_url = urlsplit(camera["sub_stream_url"])
            self.assertEqual(main_url.scheme, "rtsp")
            self.assertTrue(main_url.hostname)
            self.assertEqual(sub_url.scheme, "rtsp")
            self.assertTrue(sub_url.hostname)
            self.assertEqual(
                (camera["main_stream_url"], camera["sub_stream_url"]),
                config.camera_stream_urls_for(camera["rtsp_url"]),
            )

    def test_broadcast_resolution_is_full_hd(self):
        self.assertEqual(config.OUTPUT_WIDTH, 1920)
        self.assertEqual(config.OUTPUT_HEIGHT, 1080)
