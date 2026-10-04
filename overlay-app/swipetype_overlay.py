#!/usr/bin/env python3
"""
overlay-app/swipetype_overlay.py

The SwipeType ghost overlay (PRD v2 rev2 section 8.5). A separate,
optional process — never imported by or embedded in daemon.py (section
8.1: GTK needs its own GLib main loop, and this keeps the core daemon
package free of a GTK/WebKit dependency).

Shows the HTML/CSS/JS ghost keyboard (overlay-app/ui/) in a
transparent, click-through, always-on-top WebKitGTK view, by
connecting as a client to daemon.py's OverlayBroadcaster Unix socket
(overlay/broadcaster.py).

Path A only: requires a wlr-layer-shell compositor (Sway, Hyprland,
river, labwc). Will fail to init on GNOME/KDE — see v2 PRD NG1.

STAGE 2g: adds LAYOUT mode -- draggable/resizable via the overlay's
own JS (overlay-app/ui/overlay.js), persisted to
~/.local/state/swipetype/overlay-layout.json.

DEVIATIONS FROM THE PRD, BY REQUEST:
  1. A single GApplication action ("toggle-overlay") is CONTEXT-
     SENSITIVE, bound to one keybind, rather than two separate actions
     on two separate keybinds:
       - While daemon mode == "swipe": toggles self.overlay_visible,
         an on/off preference for whether the ghost keyboard is shown
         while actively swiping. Swiping/decoding/injection are
         unaffected either way.
       - While daemon mode == "idle": toggles self.layout_mode_active
         instead -- there is nothing useful for a "visible while
         swiping" preference to do when you aren't swiping, and this
         is exactly when repositioning the keyboard is useful.
     Rationale: "visibility toggle" and "layout mode" are genuinely
     different actions, but the single piece of context that decides
     which one you want (swipe mode on vs. off) is already known at
     keypress time, so one key suffices.
  2. Turning swipe mode on always exits layout mode cleanly (PRD 8.5
     table's own rule), ensuring the two states can never overlap.

Fix history, proven in scratch/spike_webkit.py (Stage 2a, not shipped):
  - gtk4-layer-shell must be ctypes-loaded into the process's GLOBAL
    symbol table *before* `import gi` — LD_PRELOAD does not reliably
    apply to PyGObject processes on this system. No LD_PRELOAD env var
    is needed anywhere in the packaged launcher as a result.
  - The webview widget needs hexpand/vexpand explicitly set, or it
    renders at its own natural (roughly screen-centered) size instead
    of filling the full-screen anchored layer-shell surface — GTK4
    does not auto-expand children to fill their parent.
  - "Hiding" the overlay must be done via page opacity + a forced-
    empty input region, never window.set_visible(False)/(True) --
    toggling window visibility breaks the layer-shell role on remap.

Run manually: python overlay-app/swipetype_overlay.py
(Packaged as: swipetype-overlay, installed by swipetype-overlay-git)
"""

# MUST be the very first thing that touches GTK/Wayland -- before
# `import gi` and before any gi.require_version() call. See fix
# history above.
import ctypes
try:
    ctypes.CDLL("/usr/lib/libgtk4-layer-shell.so", mode=ctypes.RTLD_GLOBAL)
except OSError as e:
    print(f"[swipetype-overlay] WARNING: failed to ctypes-load "
          f"libgtk4-layer-shell.so ({e}). The overlay will likely "
          f"render as an ordinary tiled window instead of a full-screen "
          f"layer-shell surface.", file=__import__("sys").stderr)

import json
import os
import socket
import sys
import threading
import time

# Importable both from a dev checkout (this file lives in
# <repo>/overlay-app/, sibling to <repo>/decoder/ and <repo>/config.py)
# and from the installed package layout (PKGBUILD installs this file
# standalone to /usr/share/swipetype-overlay/, with the daemon's source
# tree under /usr/share/swipetype/).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in ("/usr/share/swipetype", os.path.dirname(_THIS_DIR)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from decoder.geometry import KEY_CENTERS
from config import load_config

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, Gdk, Gio, GLib, Gtk4LayerShell as LayerShell, WebKit
import cairo

SOCKET_PATH = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/swipetype-overlay.sock"
RECONNECT_INTERVAL_S = 2.0

UI_DIR = os.path.join(_THIS_DIR, "ui")
UI_INDEX_URI = "file://" + os.path.join(UI_DIR, "index.html")

STAGE_BOUNDS = {"xmin": -0.75, "xmax": 10.0, "ymin": -0.75, "ymax": 2.75}

DEV_MODE = bool(os.environ.get("SWIPETYPE_OVERLAY_DEV"))
MAX_PENDING_POINTS = 500

# Layout state file (PRD section 5.2). Written only by this process,
# only when a drag/resize finishes. NEVER packaged, never in
# backup=() -- per-user runtime state, separate from config.yaml.
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "swipetype"
)
LAYOUT_STATE_PATH = os.path.join(STATE_DIR, "overlay-layout.json")


def _load_layout() -> dict | None:
    """Missing, unreadable, or malformed -> None (= "fit to screen" default)."""
    try:
        with open(LAYOUT_STATE_PATH, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    try:
        cx = float(data["cx"])
        cy = float(data["cy"])
        w = float(data["w"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "cx": min(1.0, max(0.0, cx)),
        "cy": min(1.0, max(0.0, cy)),
        "w": min(1.0, max(0.2, w)),
    }


def _save_layout_atomic(layout: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp_path = LAYOUT_STATE_PATH + ".tmp"
        payload = {"version": 1, "cx": layout["cx"], "cy": layout["cy"], "w": layout["w"]}
        with open(tmp_path, "w") as f:
            json.dump(payload, f)
        os.replace(tmp_path, LAYOUT_STATE_PATH)  # atomic on the same filesystem
    except (OSError, KeyError, TypeError) as e:
        if DEV_MODE:
            print(f"[swipetype-overlay] failed to save layout: {e}")


def _delete_layout() -> None:
    try:
        os.remove(LAYOUT_STATE_PATH)
    except FileNotFoundError:
        pass
    except OSError as e:
        if DEV_MODE:
            print(f"[swipetype-overlay] failed to delete layout state: {e}")


class OverlayApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.dishitaghuge.swipetype.overlay")
        self.window: Gtk.ApplicationWindow | None = None
        self.webview: WebKit.WebView | None = None

        # self.mode: the daemon's actual IDLE/SWIPE state, from
        # daemon.py's "mode" socket events.
        self.mode = "idle"

        # Independent on/off preference for whether the ghost keyboard
        # is shown WHILE SWIPE MODE IS ON, toggled by the same key as
        # layout mode -- see _on_toggle_overlay_action().
        self.overlay_visible = True

        # LAYOUT mode: toggled by the SAME key as overlay_visible, but
        # only reachable while swipe mode is off. Takes priority over
        # mode/overlay_visible whenever active -- see
        # _refresh_visual_state().
        self.layout_mode_active = False
        self.current_layout = _load_layout()  # None = fit-to-screen default

        self._page_ready = False
        self._pending: list[dict] = []
        self._flush_id: int | None = None

        self.cfg = load_config()

    # ---- GTK application lifecycle ----

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return
        self._build_window()
        self._register_actions()
        self._start_socket_thread()

    def _register_actions(self) -> None:
        toggle_action = Gio.SimpleAction.new("toggle-overlay", None)
        toggle_action.connect("activate", self._on_toggle_overlay_action)
        self.add_action(toggle_action)

    def _build_window(self) -> None:
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_decorated(False)

        LayerShell.init_for_window(self.window)
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        LayerShell.set_namespace(self.window, "swipetype-overlay")
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                     LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self.window, edge, True)
        LayerShell.set_exclusive_zone(self.window, -1)
        LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.NONE)

        if DEV_MODE:
            print(f"[swipetype-overlay] LayerShell.is_layer_window() -> "
                  f"{LayerShell.is_layer_window(self.window)}")

        css = b"window { background-color: transparent; }"
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        ucm = WebKit.UserContentManager()
        ucm.register_script_message_handler("swipetype", None)
        ucm.connect("script-message-received::swipetype", self._on_js_message)

        session = WebKit.NetworkSession.new_ephemeral()
        self.webview = WebKit.WebView(network_session=session, user_content_manager=ucm)

        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)

        rgba = Gdk.RGBA()
        rgba.parse("rgba(0,0,0,0)")
        self.webview.set_background_color(rgba)

        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(DEV_MODE)

        self.webview.connect("decide-policy", self._on_decide_policy)

        self.window.set_child(self.webview)
        self.window.connect("map", self._on_map)

        self.window.set_visible(True)
        self.window.present()

        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.load_uri(UI_INDEX_URI)

    def _on_map(self, _widget) -> None:
        self._apply_input_region()

    def _apply_input_region(self) -> None:
        surface = self.window.get_surface()
        if surface is None:
            return
        if self.layout_mode_active:
            # Full region: catches all clicks, so dragging the
            # keyboard and clicking the toolbar actually work.
            surface.set_input_region(None)
        else:
            # Both IDLE and SWIPE stay fully click-through.
            surface.set_input_region(cairo.Region())

    def _on_decide_policy(self, _webview, decision, decision_type) -> bool:
        if decision_type in (
            WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri != UI_INDEX_URI:
                decision.ignore()
                return True
        return False

    # ---- Page load -> window.swipetype.init() (PRD section 8.6) ----

    def _on_load_changed(self, webview, event) -> None:
        if event != WebKit.LoadEvent.FINISHED:
            return
        self._page_ready = True

        keys_json = json.dumps({letter: list(xy) for letter, xy in KEY_CENTERS.items()})
        stage_json = json.dumps(STAGE_BOUNDS)
        layout_json = json.dumps(self.current_layout)  # None -> JSON null
        dev_json = "true" if DEV_MODE else "false"

        init_js = (
            f"window.swipetype.init({{"
            f"keys: {keys_json}, "
            f"stage: {stage_json}, "
            f"layout: {layout_json}, "
            f"dev: {dev_json}"
            f"}});"
        )
        webview.evaluate_javascript(init_js, -1, None, None, None, None)

        self._refresh_visual_state()
        GLib.idle_add(self._flush)

        if DEV_MODE:
            print(f"[swipetype-overlay] page load FINISHED; init() sent "
                  f"(saved layout: {self.current_layout}).")

    def _on_js_message(self, _ucm, value) -> None:
        try:
            payload = json.loads(value.to_json(0))
        except Exception as e:
            if DEV_MODE:
                print(f"[swipetype-overlay] JS message (unparseable): {e}")
            return

        msg_type = payload.get("type")

        if msg_type == "layout":
            raw = payload.get("layout") or {}
            try:
                cx = float(raw["cx"])
                cy = float(raw["cy"])
                w = float(raw["w"])
            except (KeyError, TypeError, ValueError):
                if DEV_MODE:
                    print(f"[swipetype-overlay] malformed layout message, ignored: {payload}")
                return
            self.current_layout = {
                "cx": min(1.0, max(0.0, cx)),
                "cy": min(1.0, max(0.0, cy)),
                "w": min(1.0, max(0.2, w)),
            }
            _save_layout_atomic(self.current_layout)
            if DEV_MODE:
                print(f"[swipetype-overlay] layout saved: {self.current_layout}")

        elif msg_type == "layout-reset":
            _delete_layout()
            self.current_layout = None
            if DEV_MODE:
                print("[swipetype-overlay] layout reset -- state file deleted.")

        elif msg_type == "layout-done":
            self.layout_mode_active = False
            self._refresh_visual_state()
            if DEV_MODE:
                print("[swipetype-overlay] layout-done -- exiting layout mode.")

        elif DEV_MODE:
            print(f"[swipetype-overlay] JS message (unhandled type): {payload}")

    # ---- Mode / visibility state machine ----

    def _apply_mode(self, mode: str) -> None:
        """Called for daemon.py's real 'mode' socket events (IDLE<->SWIPE only)."""
        self.mode = mode
        if mode == "swipe" and self.layout_mode_active:
            # Swipe mode always wins -- leave layout mode cleanly.
            self.layout_mode_active = False
            if DEV_MODE:
                print("[swipetype-overlay] swipe mode turned on -- exiting layout mode.")
        if DEV_MODE:
            print(f"[swipetype-overlay] daemon mode -> {mode}")
        self._refresh_visual_state()

    def _on_toggle_overlay_action(self, _action, _param) -> None:
        # Context-sensitive by design (see module docstring): the same
        # key means "show/hide while swiping" when swipe mode is on,
        # and "edit where it sits" when swipe mode is off.
        if self.mode == "swipe":
            self.overlay_visible = not self.overlay_visible
            if DEV_MODE:
                print(f"[swipetype-overlay] overlay_visible preference -> {self.overlay_visible}")
        else:
            self.layout_mode_active = not self.layout_mode_active
            if DEV_MODE:
                print(f"[swipetype-overlay] layout_mode_active -> {self.layout_mode_active}")
        self._refresh_visual_state()

    def _refresh_visual_state(self) -> None:
        # LAYOUT takes priority over everything else.
        if self.layout_mode_active:
            visual_mode = "layout"
        elif self.mode == "swipe" and self.overlay_visible:
            self.cfg = load_config()
            enabled = self.cfg.get("ghost_overlay", {}).get("enabled", True)
            visual_mode = "swipe" if enabled else "idle"
        else:
            visual_mode = "idle"

        opacity = "0" if visual_mode == "idle" else "1"

        if self._page_ready:
            js = (f"document.body.style.transition = 'opacity 150ms'; "
                  f"document.body.style.opacity = '{opacity}';")
            self.webview.evaluate_javascript(js, -1, None, None, None, None)
            self.webview.evaluate_javascript(
                f'window.swipetype.setMode("{visual_mode}");', -1, None, None, None, None)

        self._apply_input_region()

        if visual_mode == "idle":
            if self._page_ready:
                self.webview.evaluate_javascript(
                    'window.swipetype.events([{"event": "clear"}]);', -1, None, None, None, None)
            self._pending = []

    # ---- Socket client (background thread) ----

    def _start_socket_thread(self) -> None:
        thread = threading.Thread(target=self._socket_loop, daemon=True)
        thread.start()

    def _socket_loop(self) -> None:
        while True:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(SOCKET_PATH)
            except OSError:
                time.sleep(RECONNECT_INTERVAL_S)
                continue

            buf = b""
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        try:
                            event = json.loads(line.decode("utf-8"))
                        except json.JSONDecodeError:
                            continue
                        GLib.idle_add(self._handle_event, event)
            except OSError:
                pass
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

            time.sleep(RECONNECT_INTERVAL_S)

    # ---- Event bridge: socket -> JS, batched (PRD section 8.6) ----

    def _handle_event(self, event: dict) -> bool:
        etype = event.get("event")
        if etype == "mode":
            self._apply_mode("swipe" if event.get("swipe_mode") else "idle")
            return False
        self._enqueue(event)
        return False

    def _enqueue(self, event: dict) -> None:
        self._pending.append(event)

        if not self._page_ready:
            total_points = sum(1 for e in self._pending if e.get("event") == "point")
            excess = total_points - MAX_PENDING_POINTS
            if excess > 0:
                trimmed = []
                points_dropped = 0
                for e in self._pending:
                    if e.get("event") == "point" and points_dropped < excess:
                        points_dropped += 1
                        continue
                    trimmed.append(e)
                self._pending = trimmed

        if self._flush_id is None:
            self._flush_id = GLib.timeout_add(10, self._flush)

    def _flush(self) -> bool:
        self._flush_id = None
        if not (self._page_ready and self._pending):
            return False
        batch, self._pending = self._pending, []
        if DEV_MODE:
            kinds = [e.get("event") for e in batch]
            print(f"[swipetype-overlay] flushing {len(batch)} event(s) to JS: {kinds}")
        js = f"window.swipetype.events({json.dumps(batch)});"
        self.webview.evaluate_javascript(js, -1, None, None, None, None)
        return False


def main() -> None:
    app = OverlayApp()
    app.run(None)


if __name__ == "__main__":
    main()