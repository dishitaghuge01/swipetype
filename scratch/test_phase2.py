"""
scratch/test_phase2.py

Minimal wiring to test Phase 2 end-to-end before daemon.py exists for
real. Wires TouchpadCapture + SocketListener together: TOGGLE flips
swipe_mode, which grabs/ungrabs the device. Runs read_events() in a
background thread so a grabbed device doesn't block the main thread
from also handling socket toggles.

Set DEVICE_PATH to your trackpad's path (from Phase 1).

Run: python scratch/test_phase2.py
Then in another terminal: python cli/swipetype_toggle.py
(or trigger your bound keyboard shortcut once you've set it up)

Test: toggle on -> confirm cursor stops moving when you touch the
trackpad. Toggle off -> confirm normal cursor movement returns.
Ctrl+C to stop.
"""

import sys
import threading
import time

sys.path.insert(0, ".")  # allow running from repo root

from capture.touchpad import TouchpadCapture
from capture.socket_listener import SocketListener

DEVICE_PATH = "/dev/input/event10"

swipe_mode = False
capture = TouchpadCapture(DEVICE_PATH)


def on_toggle():
    global swipe_mode
    swipe_mode = not swipe_mode
    capture.set_mode(swipe_mode)
    state = "SWIPE_MODE (grabbed)" if swipe_mode else "IDLE (ungrabbed)"
    print(f"[toggle] now: {state}")


def read_loop():
    for slot, event_type in capture.read_events():
        if not swipe_mode:
            continue  # ignore events while idle, just proving grab works
        print(f"  slot={slot} event={event_type}")
        if event_type == "finger_up":
            capture.clear_slot(slot)


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