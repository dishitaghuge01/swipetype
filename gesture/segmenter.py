"""
gesture/segmenter.py

Gesture segmenter (PRD section 7.7). Consumes (slot, event_type) events
from TouchpadCapture and, once ALL fingers in a touch group have
lifted, classifies the completed gesture as a flick (char/word delete)
or a word gesture.

Phase 4 update: word gestures are now decoded via decoder/scorer.py
into an actual word (plus top-k debug candidates), instead of just
returning the raw path. Injection (Phase 5) wires into the returned
"decoded_word" field later without changing this module's interface.
"""

from typing import Dict, List, Optional, Set, Tuple

from decoder.geometry import to_key_space
from decoder.resample import resample_path, N_RESAMPLE
from decoder.scorer import decode as decoder_decode

# PRD section 7.7 defaults, config-overridable in a later phase
FLICK_MAX_DURATION_MS = 150
FLICK_MIN_DX = -0.5   # key-units; net x-displacement must be more negative than this (leftward)
FLICK_MAX_DY = 0.3    # key-units; net y-displacement must stay under this (roughly horizontal)


class GestureSegmenter:
    def __init__(self, dictionary: Optional[Dict[str, dict]] = None,
                 n_resample: int = N_RESAMPLE, debug_top_k: int = 5):
        """
        dictionary: output of decoder.dictionary.load_dictionary(). If
        None, word gestures fall back to returning the raw path with
        decoded_word=None (useful for testing before Phase 4's
        dictionary is wired in, or if it failed to load).
        """
        self.dictionary = dictionary
        self.n_resample = n_resample
        self.debug_top_k = debug_top_k

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
                "decoded_word": None,
                "candidates": [],
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

        # Word gesture: resample into key-space and decode.
        gesture_resampled = resample_path(key_space_path, self.n_resample)

        candidates: List[Tuple[str, float]] = []
        decoded_word: Optional[str] = None

        if self.dictionary is not None:
            candidates = decoder_decode(gesture_resampled, self.dictionary, top_k=self.debug_top_k)
            if candidates:
                decoded_word = candidates[0][0]

        return {
            "type": "word",
            "path": path,
            "decoded_word": decoded_word,
            "candidates": candidates,
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