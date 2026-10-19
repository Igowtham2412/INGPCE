"""
ws_hub.py — zero-backlog WebSocket broadcast hub for live video + events.
Each client has a queue of size 1: new data replaces stale data instead of
queuing, guaranteeing the dashboard always shows the most recent state.
"""
import asyncio
import logging
from typing import Dict, Set

logger = logging.getLogger("ppe.ws_hub")


class _LatestOnlyQueue:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1)

    async def put(self, item):
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(item)

    async def get(self):
        return await self._queue.get()


class WebSocketHub:
    def __init__(self):
        self._video_subscribers: Dict[str, Set[_LatestOnlyQueue]] = {}
        self._event_subscribers: Set[_LatestOnlyQueue] = set()
        self._lock = asyncio.Lock()

    async def subscribe_video(self, camera_id: str) -> _LatestOnlyQueue:
        q = _LatestOnlyQueue()
        async with self._lock:
            self._video_subscribers.setdefault(camera_id, set()).add(q)
        return q

    async def unsubscribe_video(self, camera_id: str, q: _LatestOnlyQueue):
        async with self._lock:
            subs = self._video_subscribers.get(camera_id)
            if subs and q in subs:
                subs.discard(q)
                if not subs:
                    self._video_subscribers.pop(camera_id, None)

    async def broadcast_frame(self, camera_id: str, jpeg_bytes: bytes):
        subs = self._video_subscribers.get(camera_id)
        if not subs:
            return
        for q in list(subs):
            await q.put(jpeg_bytes)

    async def subscribe_events(self) -> _LatestOnlyQueue:
        q = _LatestOnlyQueue()
        async with self._lock:
            self._event_subscribers.add(q)
        return q

    async def unsubscribe_events(self, q: _LatestOnlyQueue):
        async with self._lock:
            self._event_subscribers.discard(q)

    async def broadcast_event(self, event: dict):
        for q in list(self._event_subscribers):
            await q.put(event)

    def stats(self) -> dict:
        return {
            "video_subscriber_counts": {cid: len(s) for cid, s in self._video_subscribers.items()},
            "event_subscribers": len(self._event_subscribers),
        }


hub = WebSocketHub()
