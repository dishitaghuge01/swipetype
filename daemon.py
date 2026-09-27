"""
daemon.py

SwipeType daemon: wires TouchpadCapture, GestureSegmenter, and a
TextInjector together into the full Phase 5 pipeline (PRD sections 7,
8). This supersedes scratch/test_phase4.py as the real long-running
process — decoded words and delete flicks are now injected into
whatever application currently has focus, instead of only being
printed to the console.

Set DEVICE_PATH below to your trackpad's device path (see
scratch/list_devices.py from Phase 1 if you need to re-find it).

Run: python daemon.py
Toggle swipe mode from another terminal (or your bound shortcut):
    python cli/swipetype_toggle.py
Ctrl+C in this terminal to stop.
"""

import os
import sys
import threading
import time

sys.path.insert(0, ".")  # allow running from repo root

from capture.touchpad import TouchpadCapture
from capture.socket_listener import SocketListener
from gesture.segmenter import GestureSegmenter
from decoder.dictionary import load_dictionary
from inject.base import TextInjector
from inject.xdotool_injector import XdotoolInjector
from inject.ydotool_injector import YdotoolInjector

DEVICE_PATH = "/dev/input/event10"  # from Phase 1 — adjust to your trackpad


def select_injector() -> TextInjector:
    """PRD section 7.4: pick the injection backend from
    XDG_SESSION_TYPE. If your environment reports something
    unexpected, hardcode the return value here instead of relying on
    the env var."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()

    if session_type == "wayland":
        print("Session type: wayland -> using ydotool for injection.")
        return YdotoolInjector()
    elif session_type == "x11":
        print("Session type: x11 -> using xdotool for injection.")
        return XdotoolInjector()
    else:
        print(
            f"Warning: XDG_SESSION_TYPE='{session_type}' not recognized "
            f"as 'x11' or 'wayland'. Defaulting to xdotool (x11). "
            f"Edit select_injector() in daemon.py to override.",
            file=sys.stderr,
        )
        return XdotoolInjector()


class SwipeTypeDaemon:
    def __init__(self, device_path: str):
        self.swipe_mode = False
        self.capture = TouchpadCapture(device_path)

        print("Loading dictionary (top 30k English words via wordfreq)...")
        self.dictionary = load_dictionary()
        print(f"Dictionary loaded: {len(self.dictionary)} words.")

        self.segmenter = GestureSegmenter(dictionary=self.dictionary)
        self.injector = select_injector()

        # PRD section 8, Phase 5, revised after the char/word-delete
        # desync bug: each entry is the text of a committed unit
        # (word + its trailing space) that is STILL PRESENT in the
        # buffer right now — not the original decoded word. A
        # char_delete trims one character off the tail of the top
        # entry (popping it once fully consumed); a word_delete pops
        # the top entry outright and deletes exactly its current
        # length. This keeps history accurate even when char_delete
        # and word_delete flicks are interleaved.
        self._history: list[str] = []

        self.listener = SocketListener(on_toggle=self._on_toggle)

    def _on_toggle(self) -> None:
        self.swipe_mode = not self.swipe_mode
        self.capture.set_mode(self.swipe_mode)
        state = "SWIPE_MODE (grabbed)" if self.swipe_mode else "IDLE (ungrabbed)"
        print(f"[toggle] now: {state}")

    def _consume_from_history(self, n: int) -> None:
        """Trim n characters off the tail of the most recent history
        entry, cascading into the entry before it if the top one gets
        fully consumed. Called after every char_delete so word_delete
        always sees the buffer's true current state."""
        for _ in range(n):
            if not self._history:
                return
            top = self._history[-1]
            if len(top) <= 1:
                self._history.pop()
            else:
                self._history[-1] = top[:-1]

    def _handle_flick(self, result: dict) -> None:
        if result["delete_type"] == "char_delete":
            self.injector.delete_chars(1)
            self._consume_from_history(1)
        else:  # "word_delete"
            if not self._history:
                print("  (word_delete flick with empty history — ignored)")
                return
            remaining = self._history.pop()
            self.injector.delete_chars(len(remaining))
            print(f"  DELETE WORD: (removed '{remaining}', {len(remaining)} chars)")

    def _handle_word(self, result: dict) -> None:
        decoded = result.get("decoded_word")
        candidates = result.get("candidates", [])
        candidates_str = ", ".join(f"{w}({score:.2f})" for w, score in candidates)

        print(
            f"  WORD: '{decoded}'  "
            f"(dt={result.get('dt_ms', 0):.0f}ms, path_len={result.get('path_len', 0):.2f})"
        )
        print(f"    top-{len(candidates)}: {candidates_str}")

        if decoded is None:
            print("  (no candidate survived pruning — nothing injected)")
            return

        self.injector.commit_word(decoded)
        self._history.append(decoded + " ")  # include the trailing space commit_word() types

    def _read_loop(self) -> None:
        for slot, event_type in self.capture.read_events():
            if not self.swipe_mode:
                continue

            result = self.segmenter.handle_event(self.capture, slot, event_type)

            if event_type == "finger_up":
                self.capture.clear_slot(slot)

            if result is None:
                continue

            if result["type"] == "flick":
                self._handle_flick(result)
            else:
                self._handle_word(result)

    def run(self) -> None:
        self.listener.start()
        print(f"Socket listening at: {self.listener.socket_path}")
        print("Daemon idle. Run cli/swipetype_toggle.py to switch modes. Ctrl+C to stop.")

        reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        reader_thread.start()

        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nStopping.")
            self.listener.stop()


def main() -> None:
    daemon = SwipeTypeDaemon(DEVICE_PATH)
    daemon.run()


if __name__ == "__main__":
    main()