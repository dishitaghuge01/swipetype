Build Plan (phase-wise, sequential, each phase independently testable)

Phase 0 — Environment verification

Run echo $XDG_SESSION_TYPE and echo $XDG_CURRENT_DESKTOP; record both — they determine §7.4's injection backend and §7.3's shortcut app.
Confirm input group membership as in §5; fix if missing.
Install system packages: sudo pacman -S python evdev — actually evdev here refers to the Python package via pip, not pacman; correct command: sudo pacman -S python python-pip, then pip install evdev wordfreq (use a venv: python -m venv .venv && source .venv/bin/activate first).
Install injection tool per Step 0's result: X11 → sudo pacman -S xdotool; Wayland → sudo pacman -S ydotool (or AUR if not in extra), then enable/start ydotoold and verify /dev/uinput permissions.

Phase 1 — Raw capture sanity check (no gesture logic yet)
Goal: prove raw touchpad multitouch data is readable in Python before building anything else.

Write a standalone script scratch/list_devices.py that runs evdev.list_devices(), prints each device's name and capabilities, so the touchpad's device path can be identified by hand.
Write scratch/read_raw.py: open the identified device, loop for event in device.read_loop(), print every ABS_MT_POSITION_X/Y, ABS_MT_SLOT, ABS_MT_TRACKING_ID event raw. Run it, physically swipe a finger, confirm coordinates stream in as expected.
Deliverable: confirm you can see raw X/Y move as you swipe, and tracking ID goes to -1 on lift.

Phase 2 — Capture layer + grab/ungrab

Implement capture/touchpad.py per §7.2 exactly: normalization using stored absinfo min/max, active_fingers dict keyed by slot, .grab()/.ungrab() wrapped in a set_mode(swipe_mode: bool) method.
Implement capture/socket_listener.py per §7.3, and the CLI in cli/swipetype_toggle.py.
Bind the CLI to a shortcut manually per §7.3 (one-time desktop setup step, not code).
Test: toggle on, confirm cursor stops moving on trackpad touch (grab working); toggle off, confirm cursor moves normally again.

Phase 3 — Gesture segmenter (flick vs word classification only, no decoding yet)

Implement gesture/segmenter.py per §7.7: on finger-lift, compute dt, dx, dy, path_len, classify as flick or word, and for now just print() the classification + raw path instead of committing anything.
Test: in swipe mode, do a quick left flick → confirm console prints "char delete" (1 finger) or "word delete" (2 finger); do a slower multi-key swipe → confirm console prints "word gesture" with the captured path.

Phase 4 — Decoder engine

Implement decoder/geometry.py (KEY_CENTERS table + to_key_space()) exactly per §7.5.
Implement decoder/resample.py (resample_path()) exactly per §7.5, including the degenerate single-point edge case.
Implement decoder/dictionary.py: load top-30,000 words via wordfreq, collapse repeated letters, build ideal paths via geometry+resample, cache in memory as {word: (resampled_path, frequency)}.
Implement decoder/scorer.py: pruning by start/end proximity, then shape_score/location_score/frequency_score/combined_score exactly per §7.5, return top-1 (and optionally top-5 for later debug logging).
Wire into segmenter: word gestures now call the decoder instead of printing raw path; print the decoded word to console (still no injection yet).
Test: perform swipes for 10–15 common words you choose in advance, log actual vs. expected decoded word, hand-tune ALPHA/BETA/GAMMA and PRUNE_RADIUS in config.yaml until accuracy feels reasonable. This is the phase where real tuning happens — expect iteration here.

Phase 5 — Text injection + delete history

Implement inject/base.py, inject/xdotool_injector.py, inject/ydotool_injector.py per §7.4; select backend at startup from Step 0's recorded session type.
Maintain a simple commit-history stack in daemon.py (list of committed words in order) so word-delete knows exactly how many Backspaces to send (len(last_word) + 1 for the trailing space), then pop it off the stack.
Wire decoder output → injector's commit_word(); wire flick classifications → delete_char()/delete_word().
Test end-to-end: open a text editor, toggle swipe mode, swipe several words in sequence with deletes interspersed, confirm correct text appears in the editor.

Phase 6 — Daemonization + polish (optional/stretch)

Wrap daemon.py as a systemd --user service (unit file in install.sh) so it starts automatically.
(Optional, only if desired) Explore 4-finger flick as an alternate toggle: requires switching part of the capture layer to also consume libinput's higher-level gesture recognition (separate from the raw evdev per-finger reads used for word gestures) — this is genuinely more complex than the keyboard-shortcut toggle and should only be attempted after Phases 0–5 are solid, and only after manually disabling the DE's existing 4-finger gesture binding in system settings.
(Optional) Ghost keyboard overlay for future onboarding of other users — not needed for the primary user, build only if planning to share the tool.