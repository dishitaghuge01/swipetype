# PRD: SwipeType v2 (rev 2) — WebKit Ghost Overlay + AUR Release

**Implementation note (2026-11-05):** Phase 3 is prepared locally. Reviewed
packaging uses system Python/declared dependencies instead of the copied-venv
sample below, and includes an optional overlay service and cold-start shortcut
helper. See `packaging/README.md` for actual dependency, service and publication
instructions. AUR submission is deferred while registration is closed. The
older code samples remain as design history, not copy-and-run release recipes.

**Revision note.** Rev 1 drew the ghost keyboard with a `Gtk.DrawingArea` + Cairo. Rev 2 replaces *only the overlay client's rendering layer* with a WebKitGTK webview running static HTML/CSS/JS. Everything on the daemon side (config, device auto-detection, broadcaster, segmenter hooks, daemon wiring) is **unchanged and already shipped** — those sections are reproduced verbatim so this document is complete. Changed or new sections are marked **[CHANGED]** or **[NEW]**.

| Section | Status |
|---|---|
| 1 Summary, 2 Goals, 3 Non-goals, 4 Step 0 | **[CHANGED]** |
| 5 config.yaml + overlay state file | **[CHANGED]** (config.yaml schema itself unchanged; state file is new) |
| 6 config.py, 7 FR-DEV | unchanged (shipped) |
| 8.1 Design decision | **[CHANGED]** (adds WebKit cost) |
| 8.2 Broadcaster, 8.3 Segmenter, 8.4 Daemon | unchanged (shipped) |
| 8.5 Overlay host app | **[CHANGED]** (rewritten) |
| 8.6 Python↔JS bridge | **[NEW]** |
| 8.7 UI bundle (keyboard, comet, modes, layout mode) | **[NEW]** |
| 8.8 Dev workflow | **[NEW]** |
| 9 AUR packaging | **[CHANGED]** (new dependency, new install paths, launcher script) |
| 10 Risks and spike gates | **[NEW]** |

---

## 1. Summary

Two additions to the finished v1 daemon: (A) an optional, separate-process **WebKitGTK overlay** that shows a live ghost keyboard — full-size square keys — and a smooth white **comet** following the in-progress swipe, drawn by real HTML/CSS/JS inside a wlr-layer-shell window; and (B) packaging the whole project for the AUR so it installs and runs on any Arch machine, not just yours.

## 2. Goals

- **G1 [CHANGED]:** Ghost overlay — a transparent, click-through, always-on-top, **full-output** layer-shell surface hosting a WebKitGTK webview that renders the QWERTY layout (positions generated from `decoder/geometry.KEY_CENTERS`, injected by Python — never hand-copied) plus a live gesture trace.
- **G1a [NEW]:** Keys are **square, full-size rounded-rect keys** at true 1:1 pitch (one key-unit horizontally == one key-unit vertically in pixels), legends centered, styled with CSS.
- **G1b [NEW]:** The gesture trace is a **smooth, tapered white comet** (bright head with glow, fading tail), not a polyline.
- **G1c [NEW]:** **Layout mode** — while swipe mode is off, the keyboard can be **moved and resized by dragging** (aspect-locked corner handles), and the layout persists across restarts. Default layout is "fit to screen", centered.
- **G2:** Overlay is fully optional at runtime — the core daemon works identically whether or not the overlay process is installed or running. *(Unchanged; still verified explicitly.)*
- **G3:** `config.yaml` becomes real — at minimum `ghost_overlay.enabled` and `device_path` are read from it, with safe fallback defaults if the file is missing/incomplete.
- **G4:** Daemon auto-detects the trackpad device instead of using a hardcoded path.
- **G5 [CHANGED]:** Package as `swipetype-git` (core daemon, no GTK/WebKit dependency) + `swipetype-overlay-git` (optional overlay; now depends on `webkitgtk-6.0`) on the AUR, built from one split PKGBUILD.
- **G6:** Documented, repeatable submission process.
- **G7 [NEW]:** The overlay's look is editable **without touching Python** — plain files in `overlay-app/ui/`, previewable in a normal browser with a recorded swipe (demo mode), hot-reloaded in the real overlay in dev mode.
- **G8 [NEW]:** Fully offline — no CDN, no remote fonts, no network access from the webview.

## 3. Non-goals

- **NG1:** GNOME/KDE overlay support. Scoped to wlroots-family compositors only (Sway, Hyprland, river, labwc) — Mutter and KWin do not implement wlr-layer-shell. Unchanged.
- **NG2:** Versioned release tags / non-`-git` AUR package. Unchanged.
- **NG3:** Multi-touchpad selection UI. Unchanged.
- **NG4:** Full `config.yaml` wiring for every tunable. Only `device_path` and `ghost_overlay.enabled` are wired. Overlay geometry deliberately lives in a **separate user state file** (§5.2), not in `config.yaml`, because the packaged `config.yaml` is root-owned under `/usr/share/swipetype/` and the overlay must be able to write it.
- **NG5 [NEW]:** A React/Vite/Tailwind runtime (i.e. shipping a Lovable export as-is). Lovable is welcome as a **design reference**; the shipped UI is hand-ported static files with no build step (see Step 0, D1). Reason: a bundler adds `nodejs`/`npm` to `makedepends`, npm network access at AUR build time, hashed asset paths, and `file://` module-loading restrictions.
- **NG6 [NEW]:** Multi-monitor placement. The overlay lands on whichever output the compositor picks for a new layer surface. `Gtk4LayerShell.set_monitor` is not used in this round.
- **NG7 [NEW]:** Extra decorative rows (number row, space bar, modifiers). The decoder's key-space only covers three letter rows, so extra rows would misalign the comet with what the decoder actually sees.
- **NG8 [NEW]:** Theming UI / runtime theme switching. Theming = edit `style.css`.

## 4. Step 0 — assumptions to resolve before writing any code

Same pattern as before: run these once, record the results.

1. **License decision.** Default: MIT. *(Done in rev 1.)*
2. **Compositor detection** — determines whether the overlay is buildable at all: `echo $XDG_CURRENT_DESKTOP`. Contains `sway`, `Hyprland`, `river`, or you know you're on a wlroots compositor → **Path A** (supported). GNOME or KDE → **Path B** (skip the overlay build, still package it, mark it untested in `pkgdesc`). Anything else/empty → `loginctl show-session $(loginctl | grep $(whoami) | awk '{print $1}') -p Type`, treat as unsupported unless wlroots-family is positively confirmed. *(Done in rev 1.)*
3. **AUR account** and SSH key. *(Done in rev 1 or pending — Phase 0.)*
4. **[NEW] D1 — UI source decision.** Default: **hand-written static HTML/CSS/vanilla JS** in `overlay-app/ui/`, with any Lovable output used only as visual reference (screenshot or exported CSS values). If you insist on shipping a Lovable export directly ("Path L"): it is a React+Vite project, so you must `npm run build` with Vite `base: './'`, **commit the built `dist/`** into `overlay-app/ui/`, replace any Google Fonts links with bundled font files, and enable file-URL access in the WebView settings (module scripts over `file://` are blocked by default). Path L is supported by this PRD only as far as those steps; the rest of this document assumes the default.
5. **[NEW] D2 — Layout-mode trigger.** Default: a GApplication action on the overlay process, invoked with `gapplication action dev.dishitaghuge.swipetype.overlay layout`, bound to a compositor keybind (Hyprland: `bind = SUPER SHIFT, space, exec, gapplication action dev.dishitaghuge.swipetype.overlay layout`). Requires no daemon or socket-protocol change.
6. **[NEW] D3 — GPU/driver recon** (feeds the spike in §10): `lspci | grep -iE 'vga|3d'`. NVIDIA proprietary is the most likely source of WebKitGTK transparency/compositing glitches.
7. **[NEW] D4 — Package facts to verify on your machine before coding:** `pacman -Si webkitgtk-6.0` (must exist; this is the **GTK4** build — `webkit2gtk`/`webkit2gtk-4.1` are GTK3 and cannot be embedded in a GTK4 window), `pacman -Ql webkitgtk-6.0 | grep -i typelib` (expect `WebKit-6.0.typelib` and `JavaScriptCore-6.0.typelib`), `pacman -Ql gtk4-layer-shell | grep '\.so'` (note the exact `.so` path for §9.6).

## 5. Configuration

### 5.1 config.yaml — exact schema (unchanged from rev 1)

```yaml
device_path: null   # null = auto-detect (see FR-DEV below); string = explicit override, e.g. "/dev/input/event10"

ghost_overlay:
  enabled: true      # if false, daemon still runs the OverlayBroadcaster socket (harmless, ~zero cost). [CHANGED behavior: the overlay process still starts, but does NOT create the WebKit webview (and its helper processes) until enabled is true at a swipe-mode-on event.]
```

Both keys optional — a missing or empty file, or a missing key, falls back to `device_path: null` and `ghost_overlay.enabled: true`.

### 5.2 Overlay state file **[NEW]**

Path: `${XDG_STATE_HOME:-$HOME/.local/state}/swipetype/overlay-layout.json`. Written only by the overlay process, only when you finish a drag/resize. Missing, unreadable, or malformed → "fit to screen" default. Never packaged, never in `backup=()`.

```json
{ "version": 1, "cx": 0.5, "cy": 0.5, "w": 0.9 }
```

- `cx`, `cy`: center of the keyboard stage as a fraction of the surface's width/height (0–1).
- `w`: stage width as a fraction of surface width. Clamped on load to `[0.2, 1.0]` and to whatever keeps the stage height within the surface height.
- Fractions (not pixels) so the layout survives resolution and scale changes.
- Written atomically (write temp file in same dir, `os.replace`).

## 6. Module: config.py (repo root) — unchanged, shipped

```python
"""
config.py

Minimal config.yaml loader (PRD section 5). Missing file, missing
keys, or malformed YAML all fall back to safe defaults rather than
crashing the daemon — config.yaml is a convenience, not a requirement.
"""

import os
from typing import Any, Dict

import yaml

DEFAULTS: Dict[str, Any] = {
    "device_path": None,
    "ghost_overlay": {"enabled": True},
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    config = {
        "device_path": DEFAULTS["device_path"],
        "ghost_overlay": dict(DEFAULTS["ghost_overlay"]),
    }

    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return config

    if not isinstance(raw, dict):
        return config

    if "device_path" in raw:
        config["device_path"] = raw["device_path"]

    overlay_raw = raw.get("ghost_overlay")
    if isinstance(overlay_raw, dict) and "enabled" in overlay_raw:
        config["ghost_overlay"]["enabled"] = bool(overlay_raw["enabled"])

    return config
```

Dependency: `pyyaml` (Arch: `python-yaml`).

## 7. FR-DEV — Device auto-detection — unchanged, shipped

`daemon.py` no longer hardcodes `DEVICE_PATH`. New function in `capture/touchpad.py`:

```python
def find_touchpad_device() -> str:
    """
    Auto-detect the trackpad's evdev device path (PRD FR-DEV). Scans
    all input devices, filters to ones exposing BOTH multitouch
    position axes AND the INPUT_PROP_BUTTONPAD device property — the
    same property libinput itself uses to distinguish a touchpad from
    a touchscreen or drawing tablet, which also expose
    ABS_MT_POSITION_X/Y but not this property.

    Verified API (python-evdev 2.0.0): InputDevice.input_props()
    returns a list of int property codes; ecodes.INPUT_PROP_BUTTONPAD
    == 2.

    Raises RuntimeError with a clear message if zero or multiple
    matches are found, listing the candidates in the multiple-match
    case so the user knows what to put in config.yaml's device_path.
    """
    import evdev
    from evdev import ecodes

    matches = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            abs_codes = {code for code, _ in caps.get(ecodes.EV_ABS, [])}
            has_mt_position = (
                ecodes.ABS_MT_POSITION_X in abs_codes
                and ecodes.ABS_MT_POSITION_Y in abs_codes
            )
            is_buttonpad = ecodes.INPUT_PROP_BUTTONPAD in dev.input_props()
        except (OSError, PermissionError):
            continue  # not readable (permissions, or device vanished) — skip

        if has_mt_position and is_buttonpad:
            matches.append((path, dev.name))

    if len(matches) == 0:
        raise RuntimeError(
            "No touchpad device found (looked for ABS_MT_POSITION_X/Y + "
            "INPUT_PROP_BUTTONPAD). Set device_path explicitly in "
            "config.yaml. Run scratch/list_devices.py to see all input "
            "devices and their capabilities."
        )
    if len(matches) > 1:
        listed = ", ".join(f"{p} ({name})" for p, name in matches)
        raise RuntimeError(
            f"Multiple touchpad-like devices found: {listed}. "
            f"Set device_path explicitly in config.yaml to disambiguate."
        )

    return matches[0][0]
```

`daemon.py`: in `main()`, `cfg = config.load_config()`, then `device_path = cfg["device_path"] or find_touchpad_device()`.

## 8. Ghost Overlay Architecture (Path A only — wlroots compositors)

### 8.1 Design decision: separate process, not embedded in daemon.py **[CHANGED]**

The overlay runs as its own process (`swipetype-overlay`), communicating with `daemon.py` over a Unix socket. Not embedded because:

- GTK requires its own main loop (GLib), which would mean restructuring `daemon.py`'s `run()` loop — real risk to working code.
- GTK4, gtk4-layer-shell **and now WebKitGTK** stay fully optional; `swipetype-git` stays dependency-light.
- If the overlay (or its WebKit web process) crashes, the daemon and your actual typing are unaffected.
- **[NEW] Cost acknowledged:** a WebKit webview spawns helper processes (web process, network process) and costs on the order of a hundred-plus MB of RAM versus a bare GTK window. That is acceptable for an optional, per-session tool; the lazy-creation rule in §8.5 ensures you pay it only when the overlay is enabled.

### 8.2 New socket: OverlayBroadcaster (`overlay/broadcaster.py`) — unchanged, shipped

Mirrors `capture/socket_listener.py` conventions exactly. A broadcaster (daemon → N overlay clients), newline-delimited JSON, one event per line. Safe with zero clients.

```python
"""
overlay/broadcaster.py

Unix domain socket server broadcasting live gesture events to any
connected overlay client(s), so the ghost overlay (a separate process)
can render a live trace without daemon.py knowing or caring whether
any overlay is even running. Newline-delimited JSON, one event per
line. Safe with zero clients connected — broadcast_* calls become
no-ops (write to zero sockets is trivially cheap).

Socket path mirrors capture/socket_listener.py's convention.
"""

import json
import os
import socket
import threading
from typing import List, Optional


class OverlayBroadcaster:
    def __init__(self):
        self.socket_path = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/swipetype-overlay.sock"
        self._server_socket: Optional[socket.socket] = None
        self._clients: List[socket.socket] = []
        self._clients_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(self.socket_path)
        self._server_socket.listen(5)

        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server_socket.accept()
            except OSError:
                break
            with self._clients_lock:
                self._clients.append(conn)

    def _broadcast(self, event: dict) -> None:
        line = (json.dumps(event) + "\n").encode("utf-8")
        with self._clients_lock:
            dead = []
            for client in self._clients:
                try:
                    client.sendall(line)
                except OSError:
                    dead.append(client)
            for d in dead:
                self._clients.remove(d)
                try:
                    d.close()
                except OSError:
                    pass

    def broadcast_mode(self, swipe_mode: bool) -> None:
        self._broadcast({"event": "mode", "swipe_mode": swipe_mode})

    def broadcast_clear(self) -> None:
        self._broadcast({"event": "clear"})

    def broadcast_point(self, kx: float, ky: float) -> None:
        self._broadcast({"event": "point", "x": kx, "y": ky})

    def broadcast_commit(self, word: str) -> None:
        self._broadcast({"event": "commit", "word": word})

    def stop(self) -> None:
        self._running = False
        if self._server_socket is not None:
            self._server_socket.close()
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
        if self._thread is not None:
            self._thread.join(timeout=1)
```

**Wire protocol (unchanged, and the only contract the overlay depends on):** `mode{swipe_mode}`, `clear`, `point{x,y}` (key-space), `commit{word}`. There is no `finger_up` event and none is added: the comet's tail fades purely by age (§8.7.4), so lift needs no signal.

### 8.3 gesture/segmenter.py changes — additive only, unchanged, shipped

Two optional constructor callbacks:

```python
def __init__(self, dictionary=None, n_resample=N_RESAMPLE, debug_top_k=5,
             on_gesture_start=None, on_point=None):
    ...
    self.on_gesture_start = on_gesture_start   # Callable[[], None]
    self.on_point = on_point                    # Callable[[float, float], None] — key-space coords
```

`finger_down` fires `on_gesture_start` exactly once per touch group:

```python
if event_type == "finger_down":
    if not self._active_slots and self.on_gesture_start is not None:
        self.on_gesture_start()
    self._active_slots.add(slot)
    self._peak_concurrent = max(self._peak_concurrent, len(self._active_slots))
    return None
```

`move` fires `on_point` with the live key-space coordinate:

```python
if event_type == "move":
    if self.on_point is not None and slot in capture.active_fingers:
        x_norm, y_norm, _ts = capture.active_fingers[slot][-1]
        kx, ky = to_key_space(x_norm, y_norm)
        self.on_point(kx, ky)
    return None
```

The finger_up/_classify path is untouched.

### 8.4 daemon.py changes — unchanged, shipped

In `SwipeTypeDaemon.__init__`, after constructing `self.segmenter`:

```python
from overlay.broadcaster import OverlayBroadcaster
...
self.overlay = OverlayBroadcaster()
self.segmenter.on_gesture_start = self.overlay.broadcast_clear
self.segmenter.on_point = self.overlay.broadcast_point
```

In `run()`, alongside `self.listener.start()`:

```python
self.overlay.start()
print(f"Overlay socket listening at: {self.overlay.socket_path}")
```

In `_on_toggle`, after the existing grab/ungrab + print:

```python
self.overlay.broadcast_mode(self.swipe_mode)
if not self.swipe_mode:
    self.overlay.broadcast_clear()
```

In `_handle_word`, right after `self.injector.commit_word(decoded)`:

```python
self.overlay.broadcast_commit(decoded)
```

### 8.5 The overlay host app — `overlay-app/swipetype_overlay.py` **[CHANGED — rewritten]**

Still a separate, optional process, still a `Gtk.Application` (application id `dev.dishitaghuge.swipetype.overlay`, unchanged). What changes: the `Gtk.DrawingArea` + Cairo rendering is replaced by a `WebKit.WebView` (WebKitGTK **6.0**, the GTK4 API) as the window's child. **Python no longer draws anything**; it owns only: the layer-shell window, the input region, the socket client, the Python→JS event bridge, and layout persistence.

**Imports and versions.**

```python
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Gdk, Gio, GLib, Gtk4LayerShell as LayerShell, WebKit
import cairo   # still needed: cairo.Region() for the input region
```

The `sys.path` bootstrap for `decoder.geometry` and `config` (candidates: `/usr/share/swipetype` and the repo root) is unchanged. The UI directory is always `os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")` — this resolves correctly both in a dev checkout (`overlay-app/ui/`) and installed (`/usr/share/swipetype-overlay/ui/`, next to the installed `swipetype_overlay.py`).

**Layer-shell window — full output, fixed size, never resized.**

- `LayerShell.init_for_window(window)` **before** the window is realized; layer `OVERLAY`; namespace `"swipetype-overlay"`.
- **Anchor all four edges** (`TOP`, `BOTTOM`, `LEFT`, `RIGHT` = True) → the compositor sizes the surface to the whole output. All keyboard placement/resizing happens *inside* the page (CSS), so the layer surface itself never resizes (resizing layer surfaces forces configure round-trips and is a common source of flicker).
- `set_exclusive_zone(window, -1)`: extend across the output, ignoring other layers' reserved zones (so the surface covers a status bar too and normalized layout fractions mean the same thing regardless of bars). **Correction to rev 1:** rev 1 stated that `-1` reserves screen space. That is wrong — a *positive* zone reserves space; `-1` means "don't be shifted by others' zones"; `0` means "respect others' zones, reserve nothing". Nothing is reserved either way.
- `set_keyboard_mode(window, KeyboardMode.NONE)` — always, in every mode. The overlay never takes keyboard focus. (Consequence: layout mode cannot be exited with Esc; it has an on-screen Done button and the same `layout` action toggles it.)
- Window background transparent: CSS `window { background: transparent; }` on the GTK side **and** `webview.set_background_color(rgba)` with `rgba.parse("rgba(0,0,0,0)")` **and** `html, body { background: transparent; }` in the page. All three are needed.

**WebView setup.**

- Use an **ephemeral network session** so nothing is written to disk: `WebKit.NetworkSession.new_ephemeral()`, passed to `WebKit.WebView(network_session=..., user_content_manager=...)`.
- Settings: JavaScript on; `set_enable_developer_extras(True)` only when `SWIPETYPE_OVERLAY_DEV=1`; file-URL cross-access left at defaults (default UI is classic `<script src>` tags, not ES modules, so it works from `file://` without loosening anything).
- Navigation lock: connect `decide-policy`; for `NAVIGATION_ACTION` / `NEW_WINDOW_ACTION` decisions, `ignore()` anything that is not the initial `file://` UI URI. The page has no reason to navigate; this keeps G8 (offline) enforced, not just intended.
- Load: `webview.load_uri("file://" + os.path.join(UI_DIR, "index.html"))`. After `load-changed` with `WebKit.LoadEvent.FINISHED`, run the `init` call (§8.6) and flush any queued events.

**Lazy creation (implements the §5.1 `enabled` behavior).** The `Gtk.Application` starts, connects the socket client thread, and registers the `layout` action regardless. The window + webview are built by `_ensure_window()`, called (a) at startup if `ghost_overlay.enabled` is true, or (b) on the first `mode: true` event / `layout` action after `enabled` becomes true (config re-read at each `mode: true`, as in rev 1). If `enabled` is false, no WebKit helper processes exist.

**Three overlay modes (Python-side state machine).**

| Mode | Window | Input region | Trigger in | Trigger out |
|---|---|---|---|---|
| `IDLE` | hidden (`set_visible(False)`) | n/a | startup; `mode:false` event; `layout-done` from JS; `layout` action while in `LAYOUT` | `mode:true` → `SWIPE`; `layout` action → `LAYOUT` |
| `SWIPE` | visible | **empty** `cairo.Region()` → fully click-through | `mode:true` event (and `enabled` true) | `mode:false` → `IDLE` |
| `LAYOUT` | visible | **default (whole surface)** → `surface.set_input_region(None)` | `layout` action while `IDLE` | `layout-done`, `layout` action again, or `mode:true` (swipe wins; layout mode is left) |

- The `layout` action is a `Gio.SimpleAction("layout")` added to the application; invoked externally with `gapplication action dev.dishitaghuge.swipetype.overlay layout`. Ignored (with a printed message) while in `SWIPE`, and ignored when `ghost_overlay.enabled` is false.
- The input region must be (re)applied on every mode change **and** every `map` event (a hide/show cycle re-creates the `Gdk.Surface`). Empty region in `SWIPE` is what keeps the v2 G-guarantee "clicks pass through, focus isn't stolen".
- Layout mode never touches the daemon: the trackpad is ungrabbed (swipe mode is off by definition), so your normal cursor does the dragging.

**Socket client — unchanged in behavior.** Background thread, reconnect every 2 s, newline-delimited JSON, `GLib.idle_add()` to hop to the GTK main thread. Only the *handler* changes: instead of mutating draw state and calling `queue_draw()`, it appends events to a pending list and schedules a flush (§8.6).

### 8.6 Python↔JS bridge **[NEW]**

**Python → JS (batched).** Pointer events arrive at touchpad-frame rate (often 60–120 Hz). Calling `evaluate_javascript` once per point is wasteful, so events are queued in order and flushed in one call per ~8–16 ms tick:

```python
def _enqueue(self, event: dict) -> None:
    self._pending.append(event)
    if self._flush_id is None:
        self._flush_id = GLib.timeout_add(10, self._flush)

def _flush(self) -> bool:
    self._flush_id = None
    if not (self._page_ready and self._pending):
        return False                      # keep events queued until page is ready
    batch, self._pending = self._pending, []
    js = f"window.swipetype.events({json.dumps(batch)})"
    self.webview.evaluate_javascript(js, -1, None, None, None, None)
    return False
```

- Order is preserved across event types (a `commit` never overtakes the last `point`).
- While the page isn't ready, the queue is capped (drop oldest `point` events beyond ~500) so a stalled load can't grow memory.
- The exact PyGObject signature of `evaluate_javascript` (trailing optional args) must be confirmed in the spike (§10, gate S1); the call above is the intended shape.

**One-time init call** after page load:

```js
window.swipetype.init({
  keys:   { "Q": [0.0, 0], "W": [1.0, 0], ... },      // straight from decoder.geometry.KEY_CENTERS
  stage:  { xmin: -0.75, xmax: 10.0, ymin: -0.75, ymax: 2.75 },
  layout: { cx: 0.5, cy: 0.5, w: 0.9 } | null,          // from overlay-layout.json, null = fit-to-screen
  dev:    false
})
```

`stage` bounds are defined **once in Python** (constants in `swipetype_overlay.py`) and derive from the geometry: key rects span `x ∈ [-0.5, 9.5]`, `y ∈ [-0.5, 2.5]` (centers ± 0.5); the decoder's `to_key_space()` output can reach `x = 9.75`, so the stage is the union of both plus 0.25 padding → the numbers above (stage is 10.75 × 3.5 units, aspect ≈ 3.07:1).

**Mode call:** `window.swipetype.setMode("idle" | "swipe" | "layout")`, sent on every state-machine transition.

**JS → Python** via a script message handler registered as `swipetype`:

```python
ucm = WebKit.UserContentManager()
ucm.register_script_message_handler("swipetype", None)
ucm.connect("script-message-received::swipetype", self._on_js_message)
# handler receives a JavaScriptCore.Value; use value.to_json(0) then json.loads
```

```js
window.webkit.messageHandlers.swipetype.postMessage({ type: "layout", layout: {cx, cy, w} })   // on drag/resize end
window.webkit.messageHandlers.swipetype.postMessage({ type: "layout-done" })                   // Done button
```

Python handles `layout` by validating/clamping and writing the state file atomically (§5.2), and `layout-done` by transitioning to `IDLE`. Malformed messages are ignored. (Exact `register_script_message_handler` signature in WebKit 6.0 — the second `world_name` argument — is confirmed in the spike, gate S1.)

### 8.7 The UI bundle — `overlay-app/ui/` **[NEW]**

#### 8.7.1 Files

```
overlay-app/ui/
  index.html          # structure only: #stage > #keys, #comet (canvas), #flash, #layout-ui; #scrim
  style.css           # ALL visual design lives here (key look, colors, glow, layout-mode chrome)
  overlay.js          # bridge API, coordinate mapping, key generation, comet renderer, modes, layout-mode drag/resize
  fonts/              # any font files, self-hosted (no remote fonts, no CDN)
  demo/
    keys.js           # copy of KEY_CENTERS for browser-only preview (checked against geometry.py by a scratch test)
    demo-path.js      # window.SWIPETYPE_DEMO = { path: [[kx,ky],...], word: "appropriate" } from scratch/last_swipe.json's key_space_path
```

Rules: classic scripts only (`<script src="overlay.js">`, no `type="module"`, no bundler); relative URLs only; no external URLs of any kind.

#### 8.7.2 Coordinate mapping (single source of truth)

Let the stage be `stage = {xmin,xmax,ymin,ymax}` (10.75 × 3.5 units), the on-screen stage width be `W_px = layout.w * viewportWidth`, and `u = W_px / (xmax - xmin)` (**pixels per key-unit, identical on both axes → keys are square**). Then:

```
px = (kx - xmin) * u        py = (ky - ymin) * u          // relative to the stage's top-left
```

The stage `<div>` is positioned at `left = cx*vw - W_px/2`, `top = cy*vh - H_px/2` with `H_px = (ymax - ymin) * u`, and exposes `--u` as a CSS custom property so key sizes/fonts/radii in `style.css` are expressed in units of `u` (e.g. `width: calc(var(--u) * 0.94)`). Default layout (no saved file) = *contain*: `w = min(1, (vh/vw) * (10.75/3.5))`, `cx = cy = 0.5`. The comet and the keys use the same mapping, so the comet is always exactly where the decoder thinks the finger is.

#### 8.7.3 Keyboard rendering

- Keys are generated by JS from the injected `keys` map (never hard-coded in HTML): one `<div class="key">` per letter, absolutely positioned by its center, size `0.94u × 0.94u` (0.06u gap), `border-radius ≈ 0.16u`, legend uppercase centered, `font-size ≈ 0.34u`.
- Because nothing sits *behind* the page's content except the compositor, **`backdrop-filter` blur has no effect** here. Achieve the "glass" look with layered gradients, a 1px translucent border and an inner top highlight instead. Default fill is faint (`rgba(255,255,255,0.07–0.12)`) so the text you're composing stays visible through the keyboard.
- Only the three decoder rows are drawn (NG7).
- Optional polish (Phase 2h stretch): the key nearest the comet head gets an `.active` glow class (nearest-center in key-space, updated at most once per animation frame).

#### 8.7.4 The comet

- **Data:** a ring buffer of `{x, y, t}` points in stage pixels, `t = performance.now()` at arrival. Points older than `TRAIL_MS` (default 420 ms) are dropped. `clear` empties the buffer; `commit` triggers the word flash and does not clear the tail (it fades by age).
- **Head smoothing:** each incoming point is blended with the previous head (`head += (p - head) * SMOOTHING`, default 0.55) to remove touchpad quantization jitter without adding perceptible lag.
- **Curve:** the buffer is drawn as a quadratic-Bézier chain through segment midpoints (standard smooth-polyline technique) — no visible corners.
- **Taper and fade:** per segment, `age = (now - t) / TRAIL_MS`; width interpolates from `HEAD_WIDTH` (≈0.13u) at the head to `TAIL_WIDTH` (≈0.02u) at the tail; alpha falls off as `(1 - age)^1.5`; round line caps.
- **Look:** white core stroke + a wider, low-alpha blue-white glow pass (`globalCompositeOperation = "lighter"` or `shadowBlur`), plus a radial-gradient glow disc at the head while the last point is younger than ~80 ms (so the head dims after lift).
- **Loop:** `requestAnimationFrame` runs **only while the buffer is non-empty** (idle cost ≈ zero). Canvas is sized to the stage in CSS pixels × `min(devicePixelRatio, 2)`, and re-sized on layout change.
- **Word flash:** on `commit`, the word fades in near the comet's last position, holds ~600 ms, fades out (total ≈ 800 ms, same as rev 1); a CSS transition, no canvas involvement.
- All tunables are constants at the top of `overlay.js` (`TRAIL_MS`, `HEAD_WIDTH`, `TAIL_WIDTH`, `SMOOTHING`, `GLOW`) so the look is tunable in one place.

#### 8.7.5 Modes in the page

`document.body.dataset.mode` ∈ `idle | swipe | layout`, set by `setMode()`. CSS keys off it:

- `swipe`: keyboard + comet visible, `#layout-ui` and `#scrim` hidden, `pointer-events: none` everywhere.
- `layout`: adds `#scrim` (≈35% black so it's obvious the whole screen is interactive), a dashed outline around the stage, four corner handles, a small toolbar with **Fit screen**, **Reset**, **Done**. The comet is hidden.
- `idle`: everything hidden (window is normally hidden anyway).

#### 8.7.6 Layout mode behavior

- **Drag the body** of the stage → move (updates `cx`, `cy`). **Drag a corner handle** → uniform scale about the opposite corner (updates `w`, and `cx`,`cy` to keep the opposite corner fixed). **Aspect ratio is always locked** (square keys are the point).
- Clamps: `w ∈ [0.2, 1.0]`, stage kept within the viewport (at least 50% of the stage must stay on screen so it can't be lost).
- Uses Pointer Events with `setPointerCapture`; layout updates are applied live via CSS; `postMessage({type:"layout", ...})` is sent **once on pointer-up**, not per-move.
- **Fit screen** → recompute the contain layout and persist it. **Reset** → delete the state file (Python side, on a `{type:"layout-reset"}` message) and re-apply the default. **Done** → `{type:"layout-done"}`.

### 8.8 Dev workflow **[NEW]**

1. **Browser preview (no daemon, no compositor):** open `overlay-app/ui/index.html?demo` directly in Firefox/Chromium. With `?demo`, or whenever `window.webkit.messageHandlers.swipetype` is absent, the page self-initializes from `demo/keys.js` and replays `demo/demo-path.js` in a loop (8 ms per point) so you can iterate on `style.css` with the real comet and keys, and a mode switcher (`?demo&mode=layout`) lets you test layout mode with a mouse.
2. **In the real overlay:** `SWIPETYPE_OVERLAY_DEV=1 python overlay-app/swipetype_overlay.py` enables WebKit developer extras (Inspector), and a `Gio.FileMonitor` on `ui/` calls `webview.reload()` on any change (page state is re-initialized via the normal ready → init path).
3. **Guard against drift:** `scratch/check_ui_keys.py` asserts `demo/keys.js` matches `decoder.geometry.KEY_CENTERS` exactly. The production overlay never uses `demo/keys.js`.

## 9. AUR Packaging Architecture **[CHANGED]**

### 9.1 Repo structure (additions and changes vs. rev 1)

```
swipetype/
  config.py                    # shipped — section 6
  config.yaml                  # shipped
  daemon.py                    # shipped — sections 7, 8.4
  overlay/
    broadcaster.py              # shipped — section 8.2
  overlay-app/
    swipetype_overlay.py         # [CHANGED] section 8.5 — WebKit host app
    ui/                          # [NEW] section 8.7 — index.html, style.css, overlay.js, fonts/, demo/
  gesture/
    segmenter.py                # shipped, additive — section 8.3
  capture/
    touchpad.py                  # shipped, additive — section 7
  scratch/
    check_ui_keys.py             # [NEW] section 8.8 drift guard
  packaging/
    PKGBUILD                     # section 9.2 [CHANGED]
    swipetype.service            # section 9.5
    swipetype-daemon              # launcher shell script
    swipetype-toggle              # launcher shell script
    swipetype-overlay             # [NEW] launcher shell script, section 9.6
  LICENSE
```

`systemd/swipetype.service` (repo root, local-dev unit) stays untouched.

### 9.2 packaging/PKGBUILD — split package, both subpackages from one source **[CHANGED]**

```bash
# Maintainer: Dishita Ghuge <ghugedishita@gmail.com>
pkgname=('swipetype-git' 'swipetype-overlay-git')
pkgver=r1.0000000  # placeholder — real value computed by pkgver() below at build time
pkgrel=1
arch=('x86_64' 'aarch64')
url="https://github.com/dishitaghuge01/swipetype"
license=('MIT')
makedepends=('git' 'python' 'python-pip' 'python-virtualenv')
source=("swipetype::git+https://github.com/dishitaghuge01/swipetype.git")
sha256sums=('SKIP')  # git source — VCS integrity is git's own, not a tarball checksum

pkgver() {
  cd "$srcdir/swipetype"
  printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

build() {
  cd "$srcdir/swipetype"
  python -m venv --system-site-packages "$srcdir/venv"
  "$srcdir/venv/bin/pip" install --no-cache-dir wordfreq pyyaml
}

package_swipetype-git() {
  pkgdesc="System-wide trackpad swipe-to-text daemon for Linux (X11/Wayland)"
  depends=('python' 'python-evdev' 'python-yaml')
  optdepends=(
    'ydotool: text injection backend under Wayland'
    'xdotool: text injection backend under X11'
    'swipetype-overlay-git: live ghost-keyboard overlay while swipe typing'
  )
  backup=("usr/share/swipetype/config.yaml")

  install -dm755 "$pkgdir/usr/share/swipetype"
  cp -r "$srcdir/swipetype/"{capture,cli,decoder,gesture,inject,overlay,daemon.py,config.py,config.yaml} \
        "$pkgdir/usr/share/swipetype/"
  cp -r "$srcdir/venv" "$pkgdir/usr/share/swipetype/.venv"

  install -Dm755 "$srcdir/swipetype/packaging/swipetype-daemon" "$pkgdir/usr/bin/swipetype-daemon"
  install -Dm755 "$srcdir/swipetype/packaging/swipetype-toggle" "$pkgdir/usr/bin/swipetype-toggle"
  install -Dm644 "$srcdir/swipetype/packaging/swipetype.service" \
                 "$pkgdir/usr/lib/systemd/user/swipetype.service"
  install -Dm644 "$srcdir/swipetype/LICENSE" \
                 "$pkgdir/usr/share/licenses/swipetype-git/LICENSE"
}

package_swipetype-overlay-git() {
  pkgdesc="Ghost-keyboard overlay (WebKitGTK) for swipetype-git (Sway/Hyprland/river/labwc only)"
  depends=('python' 'python-gobject' 'python-cairo' 'gtk4' 'gtk4-layer-shell'
           'webkitgtk-6.0' 'python-yaml' 'swipetype-git')

  # Python host app and its UI live side by side, so the app's
  # os.path.dirname(__file__)/ui lookup works in dev and installed layouts.
  install -Dm755 "$srcdir/swipetype/overlay-app/swipetype_overlay.py" \
                 "$pkgdir/usr/share/swipetype-overlay/swipetype_overlay.py"
  cp -r "$srcdir/swipetype/overlay-app/ui" "$pkgdir/usr/share/swipetype-overlay/ui"
  find "$pkgdir/usr/share/swipetype-overlay/ui" -type d -exec chmod 755 {} +
  find "$pkgdir/usr/share/swipetype-overlay/ui" -type f -exec chmod 644 {} +

  install -Dm755 "$srcdir/swipetype/packaging/swipetype-overlay" \
                 "$pkgdir/usr/bin/swipetype-overlay"
  install -Dm644 "$srcdir/swipetype/LICENSE" \
                 "$pkgdir/usr/share/licenses/swipetype-overlay-git/LICENSE"
}
```

Changes vs. rev 1: `webkitgtk-6.0` and `python-cairo` (the host app imports `cairo` for `cairo.Region`) added to `depends`; `pkgdesc` updated; overlay files now install under `/usr/share/swipetype-overlay/` with a launcher in `/usr/bin` instead of the single script being dropped into `/usr/bin` directly; UI directory installed with normalized permissions. `makedepends` is unchanged — the UI has no build step (NG5). The placeholder `pkgver=r1.0000000` is rewritten by `pkgver()`; never edit it by hand for a `-git` package.

### 9.3 packaging/swipetype-daemon (launcher script) — unchanged

```bash
#!/bin/sh
exec /usr/share/swipetype/.venv/bin/python /usr/share/swipetype/daemon.py "$@"
```

### 9.4 packaging/swipetype-toggle (launcher script) — unchanged

```bash
#!/bin/sh
exec /usr/share/swipetype/.venv/bin/python /usr/share/swipetype/cli/swipetype_toggle.py "$@"
```

### 9.5 packaging/swipetype.service (packaged variant) — unchanged

```ini
[Unit]
Description=SwipeType gesture-to-text daemon
After=ydotoold.service
Wants=ydotoold.service

[Service]
Type=simple
ExecStart=/usr/bin/swipetype-daemon
Environment=XDG_SESSION_TYPE=wayland
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

(No `WorkingDirectory` needed — `config.py`'s `CONFIG_PATH` is computed from `__file__`, and `find_touchpad_device()` removes the hardcoded-path dependency.)

### 9.6 packaging/swipetype-overlay (launcher script) **[NEW]**

Uses the **system** python (the overlay needs system `python-gobject`; it must not use the daemon's `.venv`):

```bash
#!/bin/sh
# Optional environment workarounds, enabled ONLY if the Phase 2a spike shows they are needed on this stack:
#   export WEBKIT_DISABLE_DMABUF_RENDERER=1          # NVIDIA/transparency or blank-render glitches
#   export WEBKIT_DISABLE_COMPOSITING_MODE=1         # last-resort fallback for black background
#   export LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so   # if layer-shell init fails/misbehaves (link-order issue); verify exact path with pacman -Ql
exec /usr/bin/python3 /usr/share/swipetype-overlay/swipetype_overlay.py "$@"
```

Caveat on `LD_PRELOAD`: it is inherited by WebKit's helper processes, some of which run inside a bubblewrap sandbox where that path may not exist. Enable it only if the spike proves it necessary, and re-test that the web process still starts.

## 10. Known risks and spike gates **[NEW]**

| # | Risk | Likelihood / impact | Mitigation / gate |
|---|---|---|---|
| S1 | PyGObject signatures for `evaluate_javascript`, `register_script_message_handler`, `NetworkSession.new_ephemeral`, `JavaScriptCore.Value.to_json` differ from the shapes in §8.6 | Medium / low | Phase 2a spike calls each once; adjust the snippets, not the design |
| S2 | Transparent webview renders black or opaque on your GPU/driver | Medium (higher on NVIDIA) / **blocking** | Phase 2a gate: transparent page must show the desktop through it. Env flags in §9.6. If unfixable → fall back to rev 1's Cairo overlay (kept in git history) |
| S3 | gtk4-layer-shell link-order problem (needs to load before libwayland-client) once WebKit is also loaded | Low–medium / blocking | Phase 2a gate; `LD_PRELOAD` branch in §9.6 with the sandbox caveat |
| S4 | Empty input region not honoured for the webview surface (clicks captured) | Low / blocking for click-through | Phase 2a gate: click a window under the overlay through it |
| S5 | Full-output transparent surface costs GPU/CPU while swiping | Low / medium | Canvas limited to stage size; rAF only while trail is non-empty; measure in Phase 2h |
| S6 | Hidden webview (`set_visible(False)`) is throttled or glitches on re-show | Low–medium / medium | Test in 2a; fallback: keep window mapped with page opacity 0 + empty input region instead of hiding |
| S7 | First toggle after enabling is slower (web process cold start) | Medium / low | Webview is created at startup when enabled (not at first toggle) |
| S8 | Wrong output on multi-monitor setups | Medium / low | NG6; documented limitation |
| S9 | Overlay RAM footprint | Certain / low | §8.1; lazy creation when `enabled: false` |