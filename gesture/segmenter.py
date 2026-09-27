"""
gesture/segmenter.py

Gesture segmenter (PRD section 7.7). Consumes (slot, event_type) events
from TouchpadCapture and, once ALL fingers in a touch group have
lifted, classifies the completed gesture as a flick (char/word delete)
or a word gesture, based on duration and net displacement in key-space.

Fix note: classification happens once per touch GROUP, not once per
individual finger. Two fingers in a real two-finger flick rarely lift
at the exact same instant, so classifying on each finger's own lift
event double-fires (first lift sees both fingers still active ->
word_delete, second lift sees only itself -> char_delete). Waiting for
the active-slot set to fully empty before classifying, and using the
peak concurrent finger count observed during the touch, fixes this
without relying on a fragile time-window guess.

Phase 3: classification only. Decoding (Phase 4) and injection
(Phase 5) wire into handle_event()'s return value later without
changing this module's public interface.
"""

from typing import Dict, List, Optional, Set, Tuple

from decoder.geometry import to_key_space

# PRD section 7.7 defaults, config-overridable in a later phase
FLICK_MAX_DURATION_MS = 150
FLICK_MIN_DX = -0.5   # key-units; net x-displacement must be more negative than this (leftward)
FLICK_MAX_DY = 0.3    # key-units; net y-displacement must stay under this (roughly horizontal)


class GestureSegmenter:
    def __init__(self):
        # Slots currently held down together.
        self._active_slots: Set[int] = set()

        # Peak number of slots that were concurrently down during the
        # current touch group (reset once the group fully lifts).
        self._peak_concurrent: int = 0

        # Path of the first finger to lift in the current group, held
        # until every finger in the group has lifted, then used as the
        # representative path for classification.
        self._pending_path: Optional[List[Tuple[float, float, float]]] = None

    def handle_event(self, capture, slot: int, event_type: str) -> Optional[Dict]:
        """Call this for every (slot, event_type) yielded by
        TouchpadCapture.read_events(). Returns a classification dict
        once all fingers in a touch group have lifted, else None."""
        if event_type == "finger_down":
            self._active_slots.add(slot)
            self._peak_concurrent = max(self._peak_concurrent, len(self._active_slots))
            return None

        if event_type == "move":
            return None

        if event_type == "finger_up":
            path = capture.active_fingers.get(slot, [])
            self._active_slots.discard(slot)

            if self._pending_path is None:
                self._pending_path = path

            if self._active_slots:
                # Other finger(s) in this group are still down. Wait
                # for them before classifying.
                return None

            # Whole group has now lifted — classify once.
            finger_count = self._peak_concurrent
            path_to_use = self._pending_path

            self._peak_concurrent = 0
            self._pending_path = None

            return self._classify(path_to_use, finger_count)

        return None

    def _classify(self, path: List[Tuple[float, float, float]], finger_count: int) -> Dict:
        if len(path) < 2:
            return {
                "type": "word",
                "path": path,
                "reason": "path too short to classify, defaulting to word",
            }

        key_space_path = [to_key_space(x, y) for x, y, _ in path]
        timestamps = [t for _, _, t in path]

        dt_ms = (timestamps[-1] - timestamps[0]) * 1000.0

        first_kx, first_ky = key_space_path[0]
        last_kx, last_ky = key_space_path[-1]
        dx = last_kx - first_kx
        dy = last_ky - first_ky

        path_len = self._path_length(key_space_path)

        is_flick = (
            dt_ms < FLICK_MAX_DURATION_MS
            and dx < FLICK_MIN_DX
            and abs(dy) < FLICK_MAX_DY
        )

        if is_flick:
            delete_type = "char_delete" if finger_count == 1 else "word_delete"
            return {
                "type": "flick",
                "delete_type": delete_type,
                "finger_count": finger_count,
                "dt_ms": dt_ms,
                "dx": dx,
                "dy": dy,
                "path_len": path_len,
            }

        return {
            "type": "word",
            "path": path,
            "dt_ms": dt_ms,
            "dx": dx,
            "dy": dy,
            "path_len": path_len,
        }

    @staticmethod
    def _path_length(key_space_path: List[Tuple[float, float]]) -> float:
        total = 0.0
        for i in range(1, len(key_space_path)):
            x1, y1 = key_space_path[i - 1]
            x2, y2 = key_space_path[i]
            total += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        return total