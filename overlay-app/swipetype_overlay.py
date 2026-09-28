#!/usr/bin/env python3
"""
overlay-app/swipetype_overlay.py

The SwipeType ghost overlay (PRD v2 section 8.5). A separate,
optional GTK4 process — never imported by or embedded in daemon.py
(see section 8.1 for why: GTK needs its own GLib main loop, and this
keeps the core daemon package free of a GTK dependency).

Shows a translucent, click-through, always-on-top QWERTY layout
(matching decoder/geometry.KEY_CENTERS exactly) plus a live trace of
the in-progress swipe, by connecting as a client to daemon.py's
OverlayBroadcaster Unix socket (overlay/broadcaster.py) and rendering
newline-delimited JSON events with Cairo.

Path A only: requires a wlr-layer-shell compositor (Sway, Hyprland,
river, labwc). Will fail to init on GNOME/KDE — see v2 PRD NG1.

Run manually: python overlay-app/swipetype_overlay.py
(Packaged as: swipetype-overlay, installed by swipetype-overlay-git)
"""

import json
import math
import os
import socket
import sys
import threading
import time

# Importable both from a dev checkout (this file lives in
# <repo>/overlay-app/, sibling to <repo>/decoder/ and <repo>/config.py)
# and from the installed package layout (PKGBUILD installs this file
# standalone to /usr/bin/swipetype-overlay, with the rest of the
# source tree under /usr/share/swipetype/).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in ("/usr/share/swipetype", os.path.dirname(_THIS_DIR)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from decoder.geometry import KEY_CENTERS
from config import load_config

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell as LayerShell
import cairo

SOCKET_PATH = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/swipetype-overlay.sock"
RECONNECT_INTERVAL_S = 2.0
FLASH_DURATION_MS = 800

# Key-space bounds for laying KEY_CENTERS out on the drawing area,
# with a little padding beyond the actual key extremes (x: Q=0 .. P=9,
# y: top row=0 .. bottom row=2 — see decoder/geometry.py).
KEY_SPACE_X_MIN, KEY_SPACE_X_MAX = -0.75, 9.75
KEY_SPACE_Y_MIN, KEY_SPACE_Y_MAX = -0.6, 2.6

OVERLAY_WIDTH = 780
OVERLAY_HEIGHT = 220


class OverlayApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.dishitaghuge.swipetype.overlay")
        self.window: Gtk.ApplicationWindow | None = None
        self.drawing_area: Gtk.DrawingArea | None = None

        self.swipe_mode = False
        self.trace_points: list[tuple[float, float]] = []
        self.flash_word: str | None = None
        self._flash_timeout_id: int | None = None

        self.cfg = load_config()

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
        self.window.set_resizable(False)

        # Stage (a): layer-shell setup. Must happen before the window
        # is realized/shown.
        LayerShell.init_for_window(self.window)
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        LayerShell.set_namespace(self.window, "swipetype-overlay")

        # Anchored to the bottom edge only; the compositor centers it
        # horizontally since neither LEFT nor RIGHT is anchored. This
        # is a floating ghost-keyboard panel, not a full-width bar, so
        # we deliberately do NOT reserve screen space for it.
        LayerShell.set_anchor(self.window, LayerShell.Edge.BOTTOM, True)
        LayerShell.set_margin(self.window, LayerShell.Edge.BOTTOM, 36)
        LayerShell.set_exclusive_zone(self.window, 0)

        # Never take keyboard focus — this is a purely visual overlay.
        LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.NONE)

        # Transparent background so only the drawn content shows.
        css = b"window { background-color: transparent; }"
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_content_width(OVERLAY_WIDTH)
        self.drawing_area.set_content_height(OVERLAY_HEIGHT)
        self.drawing_area.set_draw_func(self._on_draw, None)
        self.window.set_child(self.drawing_area)

        # Click-through: set once the window is actually mapped, since
        # the Gdk.Surface doesn't exist until then.
        self.window.connect("map", self._on_map)

        # Start hidden — only shown while swipe mode is on AND
        # ghost_overlay.enabled is true (re-checked on each mode
        # event, see _on_mode_event).
        self.window.set_visible(False)
        self.window.present()

    def _on_map(self, _widget) -> None:
        surface = self.window.get_surface()
        if surface is not None:
            # Empty region == nothing captures pointer input == fully
            # click-through. gtk4-layer-shell has no direct API for
            # this, so it's done via the underlying Gdk.Surface.
            surface.set_input_region(cairo.Region())

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
                # socket yet. Retry rather than crash — the overlay
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
                        # GTK widgets must only be touched from the
                        # GTK main thread; idle_add is the standard
                        # way to hand data across from this worker
                        # thread.
                        GLib.idle_add(self._handle_event, event)
            except OSError:
                pass
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

            time.sleep(RECONNECT_INTERVAL_S)

    # ---- Event handling (runs on GTK main thread via idle_add) ----

    def _handle_event(self, event: dict) -> bool:
        etype = event.get("event")

        if etype == "mode":
            self._on_mode_event(bool(event.get("swipe_mode")))
        elif etype == "clear":
            self.trace_points = []
            self.flash_word = None
            self._queue_redraw()
        elif etype == "point":
            x, y = event.get("x"), event.get("y")
            if x is not None and y is not None:
                self.trace_points.append((float(x), float(y)))
                self._queue_redraw()
        elif etype == "commit":
            word = event.get("word")
            if word:
                self._show_flash(word)

        return False  # GLib.idle_add: don't repeat this call

    def _on_mode_event(self, swipe_mode: bool) -> None:
        self.swipe_mode = swipe_mode
        if swipe_mode:
            # Cheap, tiny file — re-check on every mode-on so toggling
            # ghost_overlay.enabled never requires restarting anything.
            self.cfg = load_config()
            enabled = self.cfg.get("ghost_overlay", {}).get("enabled", True)
            self.window.set_visible(enabled)
        else:
            self.window.set_visible(False)
            self.trace_points = []
            self.flash_word = None

    def _show_flash(self, word: str) -> None:
        self.flash_word = word
        # Reset the trace now — ready for the next gesture's "clear"
        # event, which will also arrive shortly, harmlessly.
        self.trace_points = []
        if self._flash_timeout_id is not None:
            GLib.source_remove(self._flash_timeout_id)
        self._flash_timeout_id = GLib.timeout_add(FLASH_DURATION_MS, self._clear_flash)
        self._queue_redraw()

    def _clear_flash(self) -> bool:
        self.flash_word = None
        self._flash_timeout_id = None
        self._queue_redraw()
        return False  # don't repeat

    def _queue_redraw(self) -> None:
        if self.drawing_area is not None:
            self.drawing_area.queue_draw()

    # ---- Rendering ----

    def _key_to_pixel(self, kx: float, ky: float, width: int, height: int) -> tuple[float, float]:
        px = (kx - KEY_SPACE_X_MIN) / (KEY_SPACE_X_MAX - KEY_SPACE_X_MIN) * width
        py = (ky - KEY_SPACE_Y_MIN) / (KEY_SPACE_Y_MAX - KEY_SPACE_Y_MIN) * height
        return px, py

    def _on_draw(self, _area, cr: cairo.Context, width: int, height: int, _data) -> None:
        # Fully transparent clear first (window CSS is already
        # transparent, but the drawing area's own surface needs this
        # too or it can paint an opaque background).
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.restore()
        cr.set_operator(cairo.OPERATOR_OVER)

        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)

        # Faint QWERTY layout, straight from decoder/geometry.KEY_CENTERS.
        cr.set_font_size(13)
        for letter, (kx, ky) in KEY_CENTERS.items():
            px, py = self._key_to_pixel(kx, ky, width, height)

            cr.set_source_rgba(1, 1, 1, 0.14)
            cr.arc(px, py, 15, 0, 2 * math.pi)
            cr.fill()

            cr.set_source_rgba(1, 1, 1, 0.5)
            extents = cr.text_extents(letter.upper())
            cr.move_to(px - extents.width / 2 - extents.x_bearing, py + extents.height / 2)
            cr.show_text(letter.upper())

        # Live trace of the in-progress gesture.
        if len(self.trace_points) >= 2:
            cr.set_source_rgba(0.35, 0.8, 1.0, 0.85)
            cr.set_line_width(3)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            first_px, first_py = self._key_to_pixel(*self.trace_points[0], width, height)
            cr.move_to(first_px, first_py)
            for kx, ky in self.trace_points[1:]:
                px, py = self._key_to_pixel(kx, ky, width, height)
                cr.line_to(px, py)
            cr.stroke()

        # Flash the just-committed word briefly.
        if self.flash_word:
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_font_size(26)
            extents = cr.text_extents(self.flash_word)
            cr.move_to(width / 2 - extents.width / 2 - extents.x_bearing, height / 2 + extents.height / 2)
            cr.show_text(self.flash_word)


def main() -> None:
    app = OverlayApp()
    app.run(None)


if __name__ == "__main__":
    main()