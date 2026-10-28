import asyncio
import unittest

from app.ws_hub import WebSocketHub


class WebSocketHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_subscriber_presence_tracks_subscriptions(self):
        hub = WebSocketHub()
        self.assertFalse(hub.has_video_subscribers("CAM01"))
        subscriber = await hub.subscribe_video("CAM01")
        self.assertTrue(hub.has_video_subscribers("CAM01"))
        await hub.unsubscribe_video("CAM01", subscriber)
        self.assertFalse(hub.has_video_subscribers("CAM01"))

    async def test_video_subscribers_receive_frame_and_unsubscribe(self):
        hub = WebSocketHub()
        subscriber = await hub.subscribe_video("CAM01")
        await hub.broadcast_frame("CAM01", b"frame")
        self.assertEqual(await asyncio.wait_for(subscriber.get(), timeout=0.1), b"frame")
        await hub.unsubscribe_video("CAM01", subscriber)
        self.assertEqual(hub.stats()["video_subscriber_counts"], {})

    async def test_event_broadcast_is_json_ready_and_counts_subscriber(self):
        hub = WebSocketHub()
        subscriber = await hub.subscribe_events()
        event = {"type": "health", "camera_id": "CAM01"}
        await hub.broadcast_event(event)
        self.assertEqual(await asyncio.wait_for(subscriber.get(), timeout=0.1), event)
        self.assertEqual(hub.stats()["event_subscribers"], 1)
        await hub.unsubscribe_events(subscriber)
        self.assertEqual(hub.stats()["event_subscribers"], 0)


if __name__ == "__main__":
    unittest.main()