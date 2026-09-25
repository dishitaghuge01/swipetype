"""
scratch/read_raw.py

Phase 1, step 2: open the trackpad device (path found via list_devices.py)
and stream raw multitouch events, printing the ones we care about for
gesture capture: ABS_MT_SLOT, ABS_MT_TRACKING_ID, ABS_MT_POSITION_X,
ABS_MT_POSITION_Y, and SYN_REPORT (frame boundary).

Set DEVICE_PATH below to whatever list_devices.py flagged as the trackpad,
e.g. "/dev/input/event7".

Run: python scratch/read_raw.py
(Likely needs sudo unless your 'input' group permissions have fully
propagated - i.e. you logged out/in after `gpasswd -a $USER input`.)

Deliverable: confirm X/Y stream in as you swipe, and ABS_MT_TRACKING_ID
goes to -1 when you lift your finger.
"""

import evdev
from evdev import ecodes, categorize

# --- EDIT THIS after running list_devices.py ---
DEVICE_PATH = "/dev/input/event10"
# ------------------------------------------------

# The event codes we care about for gesture capture (per PRD section 7.2)
TRACKED_CODES = {
    ecodes.ABS_MT_SLOT,
    ecodes.ABS_MT_TRACKING_ID,
    ecodes.ABS_MT_POSITION_X,
    ecodes.ABS_MT_POSITION_Y,
}


def main():
    try:
        device = evdev.InputDevice(DEVICE_PATH)
    except (OSError, PermissionError, FileNotFoundError) as e:
        print(f"Could not open {DEVICE_PATH}: {e}")
        print("Did you set DEVICE_PATH correctly from list_devices.py output?")
        return

    print(f"Reading from: {device.name} ({device.path})")
    print("Swipe your finger on the trackpad. Ctrl+C to stop.\n")

    for event in device.read_loop():
        if event.type == ecodes.EV_ABS and event.code in TRACKED_CODES:
            code_name = ecodes.ABS[event.code]
            if event.code == ecodes.ABS_MT_TRACKING_ID and event.value == -1:
                print(f"  {code_name} = {event.value}   <-- finger lifted")
            else:
                print(f"  {code_name} = {event.value}")

        elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
            print("-- SYN_REPORT (frame end) --")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")