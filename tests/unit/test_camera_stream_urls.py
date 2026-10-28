import unittest

from app.config import camera_stream_urls_for


class CameraStreamUrlTests(unittest.TestCase):
    def test_main_url_derives_sub_stream(self):
        main_url = "rtsp://camera.example/Streaming/Channels/101"

        self.assertEqual(
            camera_stream_urls_for(main_url),
            (
                main_url,
                "rtsp://camera.example/Streaming/Channels/102",
            ),
        )

    def test_legacy_sub_url_derives_main_stream(self):
        sub_url = "rtsp://camera.example/Streaming/Channels/302"

        self.assertEqual(
            camera_stream_urls_for(sub_url),
            (
                "rtsp://camera.example/Streaming/Channels/301",
                sub_url,
            ),
        )

    def test_non_hikvision_url_is_preserved_for_both_streams(self):
        url = "rtsp://camera.example/live"

        self.assertEqual(camera_stream_urls_for(url), (url, url))


if __name__ == "__main__":
    unittest.main()
