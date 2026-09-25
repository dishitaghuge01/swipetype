"""
scratch/list_devices.py

Phase 1, step 1: enumerate all evdev input devices and print their
capabilities so we can visually identify which device path corresponds
to the trackpad.

The trackpad should show ABS_MT_POSITION_X and ABS_MT_POSITION_Y in its
capabilities (protocol type B multitouch). Keyboards and plain mice will
not have these.

Run: python scratch/list_devices.py
(May need sudo if /dev/input/event* isn't readable by your user yet.)
"""

import evdev


def main():
    device_paths = evdev.list_devices()

    if not device_paths:
        print("No input devices found. Are you in the 'input' group? "
              "Try running with sudo to check, then fix group membership.")
        return

    for path in device_paths:
        try:
            device = evdev.InputDevice(path)
        except (OSError, PermissionError) as e:
            print(f"{path}: could not open ({e})")
            continue

        print("=" * 70)
        print(f"Path: {device.path}")
        print(f"Name: {device.name}")
        print(f"Phys: {device.phys}")

        capabilities = device.capabilities(verbose=True)

        has_mt_x = False
        has_mt_y = False

        for cap_type, cap_codes in capabilities.items():
            print(f"  {cap_type}:")
            for entry in cap_codes:
                # entry is typically (code_name_tuple_or_str, AbsInfo_or_None)
                # for EV_ABS entries, or just code_name for others
                print(f"    {entry}")
                # Check for the multitouch position codes we care about
                code_repr = str(entry)
                if "ABS_MT_POSITION_X" in code_repr:
                    has_mt_x = True
                if "ABS_MT_POSITION_Y" in code_repr:
                    has_mt_y = True

        if has_mt_x and has_mt_y:
            print(f"  >>> LIKELY TRACKPAD (has ABS_MT_POSITION_X/Y): {device.path}")

        print()


if __name__ == "__main__":
    main()