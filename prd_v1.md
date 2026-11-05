PRD: SwipeType — System-Wide Gesture-to-Text Input for Linux Trackpads
1. One-line summary

A background daemon that lets a single user compose text by swiping on their laptop trackpad, decoded into words via shape-matching against a QWERTY layout, and injected as real keystrokes into whatever application currently has focus.

2. Problem & user

Single target user (v1): the builder, on Arch Linux, who touch-types fluently (so no visual keyboard reference is required), finds voice input cognitively mismatched to how she composes text, and wants a faster/lower-friction input method than typing for casual composition (notes, chat, search).

3. Goals (v1 / MVP)
G1: Recognize one-finger trackpad swipe gestures and convert them into English words.
G2: Inject the recognized word into the currently focused text field, system-wide (any app, browser, OS search).
G3: Support delete-last-character and delete-last-word via distinct gestures.
G4: Support an explicit, unambiguous on/off toggle for "swipe mode" so normal trackpad cursor use isn't disrupted.
G5: Run entirely locally, no network dependency, low latency (<100ms perceived from finger-lift to text appearing).
4. Non-goals (explicitly out of scope for v1)
NG1: Multi-user distribution polish, onboarding UI, ghost-keyboard overlay (build as a stub, not required for you).
NG2: Windows/macOS support.
NG3: Numbers, punctuation, symbols, capitalization gestures — letters and spaces only in v1.
NG4: Candidate-word disambiguation UI (alternate word picker). v1 always commits the top-scoring candidate.
NG5: Personal dictionary learning / adaptive frequency updates. v1 uses a static frequency dictionary.
NG6: True IBus/Fcitx5 engine registration. v1 uses direct synthetic input injection instead (see §7.4 for why).
5. Environment assumptions to resolve in Step 0

Before writing any code, the executing LLM/user must determine:

Display server: run echo $XDG_SESSION_TYPE → will return x11 or wayland. This determines the text-injection backend (§7.4).
Window manager / DE: run echo $XDG_CURRENT_DESKTOP. Needed to know where to bind the toggle shortcut (§7.3) and whether 4-finger gestures are already claimed (only relevant if Phase 6 is attempted).
Confirm the user is in the input group (required for raw evdev device access): groups $USER | grep input. If absent: sudo gpasswd -a $USER input, then log out/in.
6. Functional requirements
FR1 — Mode toggle

A user-invoked action switches the daemon between IDLE (trackpad behaves normally) and SWIPE_MODE (daemon exclusively owns trackpad input; OS cursor does not move). Mechanism: keyboard shortcut → CLI command → Unix domain socket message → daemon grabs/ungrabs the touchpad evdev device.

FR2 — Word gesture recognition

While in SWIPE_MODE, a single continuous one-finger touch-down → move → lift sequence that is NOT classified as a flick (FR3) is treated as a word gesture. On lift, the path is decoded to the highest-scoring dictionary word, which is committed with a trailing space.

FR3 — Delete gestures
One-finger short right-to-left flick → delete last character (send Backspace).
Two-finger short right-to-left flick (simultaneous) → delete last committed word (send Backspace × len(last_word) + 1 for the space, or re-derive from a commit history stack — see §8, Phase 4).
FR4 — Text injection

Committed words/deletions are sent to whatever window currently has focus, via the platform-appropriate synthetic input tool (§7.4).

FR5 — Config

A single config.yaml controls: toggle shortcut binding reference, flick thresholds, dictionary size, resample point count, decoder weight constants, injection backend override.

7. Architecture
7.1 Components
[Toggle Shortcut (DE-bound)] --CLI--> [Unix Socket Listener] --> [Daemon Core]
                                                                       |
[Touchpad evdev device] --raw multitouch--> [Capture Layer] --------> |
                                                                       v
                                                              [Gesture Segmenter]
                                                                       |
                                                         (word path) / (flick event)
                                                                 /            \
                                                    [Decoder Engine]      [Delete handler]
                                                                 \            /
                                                                  v          v
                                                            [Text Injector] --> focused app
7.2 Capture layer — exact mechanism

Use python-evdev (PyPI package name: evdev), NOT python-libinput bindings — evdev is more stable/maintained and gives raw per-finger data instead of pre-baked gesture semantics, which we need since we're building our own gesture classifier.

Locate the touchpad device: evdev.list_devices() → for each path, evdev.InputDevice(path), inspect .capabilities() for ABS_MT_POSITION_X/ABS_MT_POSITION_Y (protocol type B multitouch) to identify the trackpad specifically (as opposed to the keyboard or a mouse device node).
Read raw coordinate bounds once at startup: device.absinfo(evdev.ecodes.ABS_MT_POSITION_X) and ...ABS_MT_POSITION_Y → gives .min and .max. Store these for normalization.
Multitouch protocol type B event codes to track per event loop iteration:
ABS_MT_SLOT — which finger-slot is being updated
ABS_MT_TRACKING_ID — new finger-down when this appears with a non-negative value on a slot; finger-up when it's set to -1
ABS_MT_POSITION_X, ABS_MT_POSITION_Y — raw coordinates for the active slot
SYN_REPORT — marks the end of one full frame of updates; this is when you snapshot "current state of all active slots" for that timestep
Maintain a dict active_fingers: dict[slot_id -> list[(x_norm, y_norm, timestamp)]], appending a point per SYN_REPORT for each currently-tracked slot.
Normalize raw coords to 0–1 range: x_norm = (raw_x - min_x) / (max_x - min_x), same for y.
Exclusive grab mechanism: on entering SWIPE_MODE, call device.grab() (wraps the EVIOCGRAB ioctl) — this stops the OS/compositor from also receiving these events, so the cursor does not move during a swipe gesture. On leaving SWIPE_MODE, call device.ungrab() to restore normal trackpad behavior. This is the load-bearing mechanism that makes mode-switching work cleanly — do not skip it.
7.3 Toggle mechanism — exact mechanism
Daemon opens a Unix domain socket at f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/swipetype.sock", listens for a literal "TOGGLE" message.
Build a tiny CLI script swipetype-toggle (a few lines: connect to the socket, send "TOGGLE", exit).
Bind that CLI to a keyboard shortcut using the DE's own shortcut settings (GNOME: Settings → Keyboard → Custom Shortcuts; KDE: System Settings → Shortcuts → Custom Shortcuts; if using a tiling WM instead of a DE: bind it in sxhkd's config, e.g. super + space → swipetype-toggle). This is a manual one-time setup step, not something the daemon does programmatically.
Recommended default binding: Super+Space. Verify in Step 0 it isn't already claimed by an app launcher (common on GNOME) before committing to it — if claimed, fall back to Super+grave or similar.
7.4 Text injection — exact mechanism, and why not a formal IME

A formal IBus/Fcitx5 engine is built around reacting to key events forwarded through the input method framework — it's the wrong shape for "a daemon that decides on its own, from gesture data, to inject a word." Building one adds real complexity (component XML registration, engine lifecycle, D-Bus service implementation) for no v1 benefit. Instead:

If XDG_SESSION_TYPE == wayland: use ydotool (Arch package: ydotool, in extra/AUR depending on version). Requires ydotoold running as a background service (creates a virtual uinput device) and the user in the appropriate group for /dev/uinput access (commonly input or a dedicated uinput group depending on the udev rule set up during install — verify with ls -l /dev/uinput). Daemon calls it via subprocess: ydotool type -- "word " for word commit, ydotool key backspace (repeated) for deletes.
If XDG_SESSION_TYPE == x11: use xdotool (Arch package: xdotool). Call: xdotool type --clearmodifiers -- "word ", and xdotool key BackSpace for deletes.
Abstract both behind a single TextInjector interface (commit_word(word: str), delete_char(), delete_word(word: str)) so the daemon core doesn't care which backend is active — selected once at startup based on the Step 0 environment check, overridable in config.yaml.
7.5 Decoder engine — exact algorithm (SHARK2-style shape matching)

Keyboard geometry — normalized key-unit coordinates (top row y=0, home row y=1, bottom row y=2; standard QWERTY stagger):

Top row (y=0):    Q(0,0) W(1,0) E(2,0) R(3,0) T(4,0) Y(5,0) U(6,0) I(7,0) O(8,0) P(9,0)
Home row (y=1):   A(0.25,1) S(1.25,1) D(2.25,1) F(3.25,1) G(4.25,1) H(5.25,1) J(6.25,1) K(7.25,1) L(8.25,1)
Bottom row (y=2): Z(0.75,2) X(1.75,2) C(2.75,2) V(3.75,2) B(4.75,2) N(5.75,2) M(6.75,2)

Store as KEY_CENTERS: dict[str, tuple[float, float]].

Mapping gesture coordinates into key-space: the daemon's normalized 0–1 touchpad coordinates must be affine-mapped into this key-grid's coordinate range (x: 0–9.75, y: 0–2), with a small configurable margin so edge keys are reachable without hitting physical trackpad edges. Implement as a single to_key_space(x_norm, y_norm) -> (kx, ky) function using a linear transform with margin parameters in config.yaml (defaults: margin=0.05).

Path resampling (applied to both the live gesture and every candidate word's ideal path):

Fixed point count: N_RESAMPLE = 100 (config default).
Algorithm: compute cumulative arc length along the path's line segments; walk the path placing a new point every total_length / (N_RESAMPLE - 1) of arc length (standard equidistant resampling, linear interpolation between original points). Implement as resample_path(points: list[tuple[float,float]], n: int) -> list[tuple[float,float]].
Edge case: word length 1 (or degenerate near-stationary gesture) → ideal/gesture path is a single repeated point; skip arc-length division (would divide by zero), return [point] * n.

Candidate ideal paths (precomputed once at dictionary load):

For each dictionary word, collapse consecutive duplicate letters (e.g. "hello" → H,E,L,O — a swipe doesn't produce a distinguishable double-hit on one key).
Ideal path = polyline through KEY_CENTERS of the collapsed letter sequence, then resampled to N_RESAMPLE points via the same function above.
Cache word -> resampled_ideal_path in memory.

Scoring (run per gesture, over pruned candidate set):

Pruning step first (critical for speed): only score candidates whose first letter's key center is within PRUNE_RADIUS (config default 1.5 key-units) of the gesture's start point, AND whose last letter's key center is within PRUNE_RADIUS of the gesture's end point.
shape_score = mean(euclidean(gesture_resampled[i], candidate_resampled[i]) for i in range(N_RESAMPLE))
location_score = euclidean(gesture_start, key_center(first_letter)) + euclidean(gesture_end, key_center(last_letter))
frequency_score = -log(word_frequency + 1e-9) (word_frequency from the wordfreq package, see §7.6)
combined_score = (ALPHA * shape_score) + (BETA * location_score) + (GAMMA * frequency_score)
Defaults: ALPHA=1.0, BETA=0.4, GAMMA=0.15 (config-overridable; expect to hand-tune these once you're testing against your own real gestures).
Lowest combined_score wins → that's the committed word.
7.6 Dictionary
Use the wordfreq PyPI package (pip install wordfreq) — gives word frequencies for English out of the box, no need to source/clean a corpus yourself.
Build dictionary at startup: wordfreq.top_n_list('en', 30000) → 30,000 most frequent English words, paired with wordfreq.word_frequency(word, 'en') for each, filtered to alphabetic-only words (strip anything with apostrophes/hyphens for v1 simplicity).
DICT_SIZE = 30000 as a config default — tune down if decode latency is too high on first real testing.
7.7 Gesture classification (word vs. flick)

Applied when a one-finger (or two-finger, for word-delete) touch sequence ends (tracking ID goes to -1):

Compute total path duration dt and net displacement (dx, dy) in key-space from first to last point, and total path length path_len (sum of segment lengths).
Flick if: dt < FLICK_MAX_DURATION_MS (default 150ms) AND dx < -FLICK_MIN_DX (default -0.5 key-units, i.e. clearly leftward) AND abs(dy) < FLICK_MAX_DY (default 0.3 key-units) AND finger count matches (1 finger → char delete, 2 fingers simultaneously → word delete).
Otherwise → word gesture, proceed to §7.5 decode.
8. Repo / module structure
swipetype/
  daemon.py                  # main event loop, wires everything together
  config.yaml                # all tunable parameters
  capture/
    touchpad.py               # evdev reading, grab/ungrab, normalization
    socket_listener.py        # unix socket TOGGLE listener
  gesture/
    segmenter.py               # per-slot point buffering, flick vs word classification
  decoder/
    geometry.py                 # KEY_CENTERS table, to_key_space()
    resample.py                 # resample_path()
    dictionary.py                # wordfreq loading, ideal path precomputation
    scorer.py                    # shape_score/location_score/combined_score, pruning
  inject/
    base.py                       # TextInjector interface
    xdotool_injector.py
    ydotool_injector.py
  cli/
    swipetype_toggle.py            # tiny CLI, sends TOGGLE over socket
  install.sh                     # dependency check + systemd user service setup
9. Non-functional requirements
NFR1: Decode latency <50ms per gesture on typical laptop CPU (drives DICT_SIZE and N_RESAMPLE defaults — reduce these first if latency target is missed).
NFR2: Daemon must run as a systemd --user service so it survives across sessions without manual restart (Phase 5 deliverable).
NFR3: No crash on trackpad disconnect/reconnect — capture layer should re-scan evdev.list_devices() on device-not-found errors.