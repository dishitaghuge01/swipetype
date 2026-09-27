"""
capture/touchpad.py

Capture layer for SwipeType (PRD section 7.2). Reads raw multitouch
events from the trackpad evdev device, normalizes coordinates to 0-1
range, and exposes a grab/ungrab mode switch so the OS cursor doesn't
move while SwipeType owns the trackpad.
"""

import evdev
from evdev import ecodes


class TouchpadCapture:
    def __init__(self, device_path: str):
        self.device_path = device_path
        self.device = evdev.InputDevice(device_path)

        x_info = self.device.absinfo(ecodes.ABS_MT_POSITION_X)
        y_info = self.device.absinfo(ecodes.ABS_MT_POSITION_Y)
        self.min_x = x_info.min
        self.max_x = x_info.max
        self.min_y = y_info.min
        self.max_y = y_info.max

        # slot -> list of (x_norm, y_norm, timestamp) points for the
        # in-progress or just-completed gesture on that slot
        self.active_fingers: dict[int, list[tuple[float, float, float]]] = {}

        # slot -> [raw_x, raw_y], last known raw position per slot.
        # Needed because a single evdev frame often updates only X or
        # only Y, never both, so we must remember the other coordinate.
        self._last_pos: dict[int, list[int]] = {}

        # Current slot pointer. Per evdev protocol B, slot 0 is implied
        # until an ABS_MT_SLOT event says otherwise.
        self._current_slot = 0

        # (slot, event_type) pairs queued within the current frame,
        # flushed on SYN_REPORT. event_type is "finger_down" or "finger_up".
        self._frame_events: list[tuple[int, str]] = []

        # Slots whose position or tracking id changed this frame.
        self._dirty_slots: set[int] = set()

    def _normalize(self, raw_x: float, raw_y: float) -> tuple[float, float]:
        x_range = self.max_x - self.min_x
        y_range = self.max_y - self.min_y
        x_norm = (raw_x - self.min_x) / x_range if x_range else 0.0
        y_norm = (raw_y - self.min_y) / y_range if y_range else 0.0
        return x_norm, y_norm

    def set_mode(self, swipe_mode: bool) -> None:
        """Grab (exclusive owner, cursor frozen) or ungrab (normal OS
        cursor behavior) the touchpad device. This is the load-bearing
        mechanism for clean mode switching — see PRD 7.2."""
        if swipe_mode:
            self.device.grab()
        else:
            self.device.ungrab()

    def clear_slot(self, slot: int) -> None:
        """Caller invokes this after reading a completed gesture's full
        path (post finger_up) to free the buffer for that slot."""
        self.active_fingers.pop(slot, None)
        self._last_pos.pop(slot, None)

    def read_events(self):
        """Generator yielding (slot, event_type) tuples, where
        event_type is 'finger_down', 'finger_up', or 'move'."""
        for event in self.device.read_loop():
            if event.type == ecodes.EV_ABS:
                self._handle_abs(event)
            elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                yield from self._flush_frame(event.timestamp())

    def _handle_abs(self, event) -> None:
        if event.code == ecodes.ABS_MT_SLOT:
            self._current_slot = event.value

        elif event.code == ecodes.ABS_MT_TRACKING_ID:
            slot = self._current_slot
            self._dirty_slots.add(slot)
            if event.value == -1:
                self._frame_events.append((slot, "finger_up"))
            else:
                self.active_fingers[slot] = []
                self._last_pos.setdefault(slot, [0, 0])
                self._frame_events.append((slot, "finger_down"))

        elif event.code == ecodes.ABS_MT_POSITION_X:
            slot = self._current_slot
            self._last_pos.setdefault(slot, [0, 0])[0] = event.value
            self._dirty_slots.add(slot)

        elif event.code == ecodes.ABS_MT_POSITION_Y:
            slot = self._current_slot
            self._last_pos.setdefault(slot, [0, 0])[1] = event.value
            self._dirty_slots.add(slot)

    def _flush_frame(self, timestamp: float):
        frame_event_slots = {slot for slot, _ in self._frame_events}

        # finger_down / finger_up first. A finger_down also gets its
        # first point appended immediately so the path isn't missing
        # its starting position.
        for slot, event_type in self._frame_events:
            if event_type == "finger_down":
                raw_x, raw_y = self._last_pos.get(slot, [0, 0])
                point = (*self._normalize(raw_x, raw_y), timestamp)
                self.active_fingers.setdefault(slot, []).append(point)
            yield (slot, event_type)

        # move events: slots with a position update this frame, an
        # active buffer, and not already reported above.
        for slot in self._dirty_slots - frame_event_slots:
            if slot in self.active_fingers:
                raw_x, raw_y = self._last_pos.get(slot, [0, 0])
                point = (*self._normalize(raw_x, raw_y), timestamp)
                self.active_fingers[slot].append(point)
                yield (slot, "move")

        self._frame_events.clear()
        self._dirty_slots.clear()