"""
debounce.py — violation confirmation (N-of-window) + logging cooldown.

The live app already performs equivalent temporal confirmation inside
ComplianceEngine (`VIOLATION_CONFIRM_FRAMES`, `TEMPORAL_CONFIRMATION`) and
per-track alert cooldowns in camera_manager. This module is the portable,
dependency-free implementation used by the demo / evaluation pipeline and the
unit tests, so debounce behaviour can be proven without cameras or models.

Semantics:
  * ``observe(...)`` feeds one frame's observation for a (camera, track,
    violation_type). It returns True ONLY on the frame where the violation
    transitions to confirmed (>= confirm_frames positive observations in the
    recent window) — a person must violate persistently, not for one frame.
  * Once confirmed it stays confirmed until a non-violating frame clears the
    confirmed flag (recent history is kept, mirroring the live engine: brief
    compliance followed by renewed violation re-confirms quickly).
  * ``should_log(...)`` applies a configurable cooldown so the same person +
    violation type is not recorded/alerted repeatedly; it stamps the log time
    when it returns True.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable

Key = tuple[str, int, str]


class ViolationDebouncer:
    def __init__(
        self,
        confirm_frames: int = 3,
        window: int = 8,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if confirm_frames < 1:
            raise ValueError("confirm_frames must be >= 1")
        if window < confirm_frames:
            raise ValueError("window must be >= confirm_frames")
        self.confirm_frames = int(confirm_frames)
        self.window = int(window)
        self.cooldown_seconds = float(cooldown_seconds)
        self.clock = clock
        self._history: dict[Key, deque[bool]] = {}
        self._confirmed: set[Key] = set()
        self._last_logged_at: dict[Key, float] = {}

    def observe(
        self,
        camera_id: str,
        track_id: int,
        violation_type: str,
        present: bool,
    ) -> bool:
        """Record one frame; True only on the confirming transition."""
        key = (camera_id, int(track_id), violation_type)
        history = self._history.get(key)
        if history is None or history.maxlen != self.window:
            history = deque(history or (), maxlen=self.window)
            self._history[key] = history
        history.append(bool(present))
        if not present:
            # A clean frame ends the confirmed episode.
            was_confirmed = key in self._confirmed
            self._confirmed.discard(key)
            return False
        confirmed_now = (
            key not in self._confirmed
            and sum(history) >= self.confirm_frames
        )
        if confirmed_now:
            self._confirmed.add(key)
            return True
        return False

    def is_confirmed(
        self, camera_id: str, track_id: int, violation_type: str,
    ) -> bool:
        return (camera_id, int(track_id), violation_type) in self._confirmed

    def should_log(
        self,
        camera_id: str,
        track_id: int,
        violation_type: str,
        now: float | None = None,
    ) -> bool:
        """Cooldown gate; stamps the log time when it returns True."""
        key = (camera_id, int(track_id), violation_type)
        now = self.clock() if now is None else float(now)
        last = self._last_logged_at.get(key)
        if last is not None and now - last < self.cooldown_seconds:
            return False
        self._last_logged_at[key] = now
        return True

    def prune_stale(self, active_keys: set[Key]) -> None:
        """Drop state for tracks that no longer exist."""
        for mapping in (self._history, self._last_logged_at):
            for key in [key for key in mapping if key not in active_keys]:
                mapping.pop(key, None)
        self._confirmed = {
            key for key in self._confirmed if key in active_keys
        }
