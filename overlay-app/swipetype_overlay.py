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

STAGE 2d: webview + layer-shell + init() wiring only. The real-time
socket -> JS event bridge (section 8.6's batching) lands in stage 2e;
the IDLE/SWIPE/LAYOUT mode state machine lands in stage 2f; the
draggable layout mode lands in stage 2g. For now, swipe mode is forced
on at startup so this stage's own check (full-screen, transparent,
click-through, real KEY_CENTERS) can be verified on its own.

Fix history, proven in scratch/spike_webkit.py (Stage 2a, not shipped):
  - gtk4-layer-shell must be ctypes-loaded into the process's GLOBAL
    symbol table *before* `import gi` — LD_PRELOAD does not reliably
    apply to PyGObject processes on this system. No LD_PRELOAD env var
    is needed anywhere in the packaged launcher as a result.
  - The webview widget needs hexpand/vexpand explicitly set, or it
    renders at its own natural (roughly screen-centered) size instead
    of filling the full-screen anchored layer-shell surface — GTK4
    does not auto-expand children to fill their parent.
  - "Hiding" the overlay (future IDLE mode, stage 2f) must be done via
    page opacity + a forced-empty input region, never
    window.set_visible(False)/(True) — toggling window visibility
    breaks the layer-shell role on remap (the compositor starts tiling
    it as an ordinary window instead of keeping it a full-screen
    overlay).

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

from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell as LayerShell, WebKit
import cairo

SOCKET_PATH = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/swipetype-overlay.sock"
RECONNECT_INTERVAL_S = 2.0

UI_DIR = os.path.join(_THIS_DIR, "ui")
UI_INDEX_URI = "file://" + os.path.join(UI_DIR, "index.html")

# Stage bounds injected into window.swipetype.init() (PRD section 8.6).
# Key rects span x∈[-0.5,9.5], y∈[-0.5,2.5] (KEY_CENTERS ± 0.5);
# decoder.geometry.to_key_space() can reach x=9.75; +0.25 padding on
# all sides. Must match overlay-app/ui/overlay.js's own demo-mode
# constants exactly (scratch/check_ui_keys.py does not check this --
# worth eyeballing if the two ever need to change).
STAGE_BOUNDS = {"xmin": -0.75, "xmax": 10.0, "ymin": -0.75, "ymax": 2.75}

DEV_MODE = bool(os.environ.get("SWIPETYPE_OVERLAY_DEV"))


class OverlayApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.dishitaghuge.swipetype.overlay")
        self.window: Gtk.ApplicationWindow | None = None
        self.webview: WebKit.WebView | None = None

        self.swipe_mode = False
        self._page_ready = False

        self.cfg = load_config()  # acted on starting stage 2f (lazy creation gating)

    # ---- GTK application lifecycle ----

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return
        self._build_window()
        self._start_socket_thread()

    def _build_window(self) -> None:
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_decorated(False)

        # --- Layer shell: full output, all four edges anchored (PRD
        # section 8.5). All keyboard placement/resizing happens INSIDE
        # the page via CSS/JS (overlay.js's coordinate mapping); the
        # layer surface itself never resizes.
        LayerShell.init_for_window(self.window)
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        LayerShell.set_namespace(self.window, "swipetype-overlay")
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                     LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self.window, edge, True)
        # -1 = "don't be shifted by other layers' reserved zones" (NOT
        # "reserve space" -- rev1 had this backwards, corrected in the
        # rev2 PRD section 8.5).
        LayerShell.set_exclusive_zone(self.window, -1)
        LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.NONE)

        if DEV_MODE:
            print(f"[swipetype-overlay] LayerShell.is_layer_window() -> "
                  f"{LayerShell.is_layer_window(self.window)}")

        # Transparent background, place 1 of 3 (GTK CSS). Places 2 and
        # 3 are the webview's own background color and the page's own
        # CSS, both set below / in overlay-app/ui/style.css.
        css = b"window { background-color: transparent; }"
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # UserContentManager + script message handler: not used for
        # anything yet in stage 2d (layout-mode messages arrive in
        # stage 2g), but wired up now since it must exist before the
        # WebView is constructed.
        ucm = WebKit.UserContentManager()
        ucm.register_script_message_handler("swipetype", None)
        ucm.connect("script-message-received::swipetype", self._on_js_message)

        session = WebKit.NetworkSession.new_ephemeral()
        self.webview = WebKit.WebView(network_session=session, user_content_manager=ucm)

        # Without this, GTK4 renders the webview at its own natural
        # (roughly screen-centered) size instead of filling the
        # full-screen anchored window. See fix history at the top of
        # this file.
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)

        # Transparent background, place 2 of 3 (webview).
        rgba = Gdk.RGBA()
        rgba.parse("rgba(0,0,0,0)")
        self.webview.set_background_color(rgba)

        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(DEV_MODE)

        # Navigation lock (PRD section 8.5, G8 offline enforcement):
        # the page has no reason to ever navigate away from its own
        # file:// UI. Blocking this keeps "fully offline" an enforced
        # property, not just an intended one.
        self.webview.connect("decide-policy", self._on_decide_policy)

        self.window.set_child(self.webview)
        self.window.connect("map", self._on_map)

        # Stage 2d: force-shown at startup, unconditionally. Lazy
        # creation based on ghost_overlay.enabled, and the real
        # IDLE/SWIPE/LAYOUT state machine, land in stage 2f.
        self.window.set_visible(True)
        self.window.present()

        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.load_uri(UI_INDEX_URI)

    def _on_map(self, _widget) -> None:
        surface = self.window.get_surface()
        if surface is not None:
            # Stage 2d: always click-through. The real mode-driven
            # input-region switching (empty in SWIPE, full in LAYOUT)
            # lands in stage 2f.
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
        dev_json = "true" if DEV_MODE else "false"

        init_js = (
            f"window.swipetype.init({{"
            f"keys: {keys_json}, "
            f"stage: {stage_json}, "
            f"layout: null, "
            f"dev: {dev_json}"
            f"}});"
        )
        webview.evaluate_javascript(init_js, -1, None, None, None, None)

        # Stage 2d step 3: force swipe mode on at startup so this
        # stage's check (keys visible, positioned, click-through) can
        # be verified without the daemon or the socket bridge running.
        # The real mode state machine replaces this in stage 2f.
        self.swipe_mode = True
        webview.evaluate_javascript('window.swipetype.setMode("swipe");', -1, None, None, None, None)

        if DEV_MODE:
            print("[swipetype-overlay] page load FINISHED; init() + setMode('swipe') sent.")

    def _on_js_message(self, _ucm, value) -> None:
        # Not used until stage 2g (layout-mode messages). Logged in
        # dev mode so unexpected messages aren't silently dropped
        # during development.
        if DEV_MODE:
            try:
                print(f"[swipetype-overlay] JS message: {json.loads(value.to_json(0))}")
            except Exception as e:
                print(f"[swipetype-overlay] JS message (unparseable): {e}")

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
                # Daemon not running, or hasn't started its overlay
                # socket yet. Retry rather than crash -- the overlay
                # should never be a reason the whole system needs
                # restarting.
                time.sleep(RECONNECT_INTERVAL_S)
                continue

            buf = b""
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break  # daemon closed the connection
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        try:
                            event = json.loads(line.decode("utf-8"))
                        except json.JSONDecodeError:
                            continue
                        # GTK/WebKit must only be touched from the main
                        # thread; idle_add hands data across from this
                        # worker thread.
                        GLib.idle_add(self._handle_event, event)
            except OSError:
                pass
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

            time.sleep(RECONNECT_INTERVAL_S)

    def _handle_event(self, event: dict) -> bool:
        # Stage 2d: received and logged only. The real batched
        # dispatch into window.swipetype.events([...]) -- per PRD
        # section 8.6's _enqueue/_flush -- is built in stage 2e.
        if DEV_MODE:
            print(f"[swipetype-overlay] socket event (not yet dispatched to JS): {event}")
        return False  # GLib.idle_add: don't repeat this call


def main() -> None:
    app = OverlayApp()
    app.run(None)


if __name__ == "__main__":
    main()