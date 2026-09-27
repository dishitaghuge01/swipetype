"""
scratch/test_phase3.py

Wires TouchpadCapture + GestureSegmenter together to test flick vs
word classification live, per PRD Phase 3 test criteria.

Set DEVICE_PATH to your trackpad's path (from Phase 1).

Run: python scratch/test_phase3.py
Then in another terminal: python cli/swipetype_toggle.py to enter
swipe mode. Toggle again to leave it. Ctrl+C in this terminal to stop.

Test:
- Quick 1-finger left flick -> expect "FLICK: char_delete"
- Quick 2-finger simultaneous left flick -> expect ONE "FLICK: word_delete"
  (not a word_delete followed by a char_delete)
- Slower multi-key swipe -> expect "WORD GESTURE" with path length/duration
"""

import sys
import threading
import time

sys.path.insert(0, ".")  # allow running from repo root

from capture.touchpad import TouchpadCapture
from capture.socket_listener import SocketListener
from gesture.segmenter import GestureSegmenter

DEVICE_PATH = "/dev/input/event10"

swipe_mode = False
capture = TouchpadCapture(DEVICE_PATH)
segmenter = GestureSegmenter()


def on_toggle():
    global swipe_mode
    swipe_mode = not swipe_mode
    capture.set_mode(swipe_mode)
    state = "SWIPE_MODE (grabbed)" if swipe_mode else "IDLE (ungrabbed)"
    print(f"[toggle] now: {state}")


def read_loop():
    for slot, event_type in capture.read_events():
        if not swipe_mode:
            continue

        result = segmenter.handle_event(capture, slot, event_type)

        # Clear this slot's buffer as soon as the segmenter has read
        # it, whether or not it classified yet (it may still be
        # waiting on a second finger in the same touch group).
        if event_type == "finger_up":
            capture.clear_slot(slot)

        if result is None:
            continue

        if result["type"] == "flick":
            print(
                f"  FLICK: {result['delete_type']} "
                f"(fingers={result['finger_count']}, dt={result['dt_ms']:.0f}ms, "
                f"dx={result['dx']:.2f}, dy={result['dy']:.2f})"
            )
        else:
            path_points = len(result.get("path", []))
            print(
                f"  WORD GESTURE: {path_points} points, "
                f"dt={result.get('dt_ms', 0):.0f}ms, path_len={result.get('path_len', 0):.2f} key-units"
            )


def main():
    listener = SocketListener(on_toggle=on_toggle)
    listener.start()
    print(f"Socket listening at: {listener.socket_path}")
    print("Daemon idle. Run cli/swipetype_toggle.py to switch modes. Ctrl+C to stop.")

    reader_thread = threading.Thread(target=read_loop, daemon=True)
    reader_thread.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping.")
        listener.stop()


if __name__ == "__main__":
    main()