"""
scratch/test_phase4.py

Wires TouchpadCapture + GestureSegmenter + the loaded dictionary
together to test live word decoding, per PRD Phase 4 test criteria.

Set DEVICE_PATH to your trackpad's path (from Phase 1).

Run: python scratch/test_phase4.py
(Dictionary load takes a few seconds — wait for "Dictionary loaded"
before toggling into swipe mode.)

Then in another terminal: python cli/swipetype_toggle.py to enter
swipe mode. Toggle again to leave it. Ctrl+C in this terminal to stop.

Test: swipe 10-15 words you chose in advance. For each, compare the
printed decoded word against what you meant to type. Log the misses.
If accuracy feels off, open decoder/scorer.py and hand-tune ALPHA,
BETA, GAMMA, PRUNE_RADIUS at the top of the file, then re-run this
script (dictionary reloads each run, ideal paths aren't affected by
scorer weights so this is just for the scoring stage).
"""

import sys
import threading
import time

sys.path.insert(0, ".")  # allow running from repo root

from capture.touchpad import TouchpadCapture
from capture.socket_listener import SocketListener
from gesture.segmenter import GestureSegmenter
from decoder.dictionary import load_dictionary

DEVICE_PATH = "/dev/input/event10"

swipe_mode = False
capture = TouchpadCapture(DEVICE_PATH)

print("Loading dictionary (top 30k English words via wordfreq)...")
dictionary = load_dictionary()
print(f"Dictionary loaded: {len(dictionary)} words.")

segmenter = GestureSegmenter(dictionary=dictionary)


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
            decoded = result.get("decoded_word")
            candidates = result.get("candidates", [])
            candidates_str = ", ".join(f"{w}({score:.2f})" for w, score in candidates)

            # Debug: where in key-space did this gesture actually start
            # and end? Helps diagnose whether swipes are physically
            # reaching edge keys (Q/A/Z on the left, P on the right)
            # or falling short, which shape/weight tuning can't fix.
            path = result.get("path", [])
            if len(path) >= 2:
                from decoder.geometry import to_key_space
                start_kx, start_ky = to_key_space(path[0][0], path[0][1])
                end_kx, end_ky = to_key_space(path[-1][0], path[-1][1])
                print(f"    key-space: start=({start_kx:.2f},{start_ky:.2f}) end=({end_kx:.2f},{end_ky:.2f})")

            print(
                f"  WORD: '{decoded}'  "
                f"(dt={result.get('dt_ms', 0):.0f}ms, path_len={result.get('path_len', 0):.2f})"
            )
            print(f"    top-{len(candidates)}: {candidates_str}")


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