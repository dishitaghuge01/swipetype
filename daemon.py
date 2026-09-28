"""
daemon.py

SwipeType daemon: wires TouchpadCapture, GestureSegmenter, and a
TextInjector together into the full Phase 5 pipeline (PRD sections 7,
8). This supersedes scratch/test_phase4.py as the real long-running
process — decoded words and delete flicks are now injected into
whatever application currently has focus, instead of only being
printed to the console.

v2 (PRD section 7, FR-DEV): the device path is no longer hardcoded.
main() loads config.yaml via config.load_config() and either uses its
device_path override or auto-detects the trackpad via
capture.touchpad.find_touchpad_device(). See config.yaml to set an
explicit override if auto-detection ever picks the wrong device.

Run: python daemon.py
Toggle swipe mode from another terminal (or your bound shortcut):
    python cli/swipetype_toggle.py
Ctrl+C in this terminal to stop.
"""

import os
import signal
import sys
import threading

sys.path.insert(0, ".")  # allow running from repo root

from capture.touchpad import TouchpadCapture, find_touchpad_device
from capture.socket_listener import SocketListener
from gesture.segmenter import GestureSegmenter
from decoder.dictionary import load_dictionary
from inject.base import TextInjector
from inject.xdotool_injector import XdotoolInjector
from inject.ydotool_injector import YdotoolInjector
from config import load_config
from overlay.broadcaster import OverlayBroadcaster


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

        # v2 section 8.4: OverlayBroadcaster is a separate optional
        # process's IPC channel. Wired as attributes (not constructor
        # args) so GestureSegmenter.__init__'s call site elsewhere
        # doesn't need to change — purely additive.
        self.overlay = OverlayBroadcaster()
        self.segmenter.on_gesture_start = self.overlay.broadcast_clear
        self.segmenter.on_point = self.overlay.broadcast_point

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

        self.overlay.broadcast_mode(self.swipe_mode)
        if not self.swipe_mode:
            self.overlay.broadcast_clear()

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
        self.overlay.broadcast_commit(decoded)

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

        self.overlay.start()
        print(f"Overlay socket listening at: {self.overlay.socket_path}")

        print("Daemon idle. Run cli/swipetype_toggle.py to switch modes.")

        reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        reader_thread.start()

        # Ctrl+C sends SIGINT when run in a terminal; `systemctl --user
        # stop` sends SIGTERM. The original KeyboardInterrupt-only
        # handling caught the former but not the latter, so a systemd
        # stop wouldn't run listener.stop() cleanly. One handler for
        # both, gating the main loop with an Event instead.
        stop_event = threading.Event()

        def _handle_signal(signum, frame) -> None:
            print(f"\nReceived signal {signum}, stopping.")
            stop_event.set()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        stop_event.wait()
        self.listener.stop()
        self.overlay.stop()


def main() -> None:
    cfg = load_config()
    device_path = cfg["device_path"] or find_touchpad_device()
    print(f"Using touchpad device: {device_path}")

    daemon = SwipeTypeDaemon(device_path)
    daemon.run()


if __name__ == "__main__":
    main()