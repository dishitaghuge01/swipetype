# SwipeType overlay UI

UI source: hand-written static HTML/CSS/vanilla JS, with a Lovable export used only as a visual reference (PRD D1 default). There is no build step, no bundler and no npm.

Layout-mode trigger: the same keybind as the overlay-visibility toggle (PRD D2, changed by request).
- Swipe mode ON: show or hide the ghost keyboard.
- Swipe mode OFF: enter or exit layout mode (drag and resize).

Use `swipetype-overlay-toggle` when installed. It checks the session-bus owner,
starts `swipetype-overlay.service` if absent, and forwards `--show` (cold start)
or `--toggle` (already running). No terminal is required. It avoids the absent
process/desktop-entry problem of a bare `gapplication action` binding.

In a checkout, install `systemd/swipetype-overlay.service` as a user unit (adjust
its checkout path if necessary), then `systemctl --user daemon-reload` and
`systemctl --user enable --now swipetype-overlay.service`. Bind an absolute command
such as `/home/dishita/swipetype/.venv/bin/python /home/dishita/swipetype/cli/swipetype_overlay_toggle.py`.
The current user's bindings are Super+Ctrl+Space for swipe mode and Super+Shift+K
for the overlay; the helper does not change the daemon's swipe mode.

The host also accepts `--toggle` and idempotent `--show`, routed to a single
GApplication instance. A direct cold `--toggle` shows rather than immediately
hiding the keyboard. Actions wait for the initial daemon mode snapshot; if the
daemon is absent, layout editing remains available after a 2.5-second fallback.
The original `toggle-overlay` GApplication action remains available to a running
application (the `gapplication` command additionally expects an installed desktop
entry on some GLib versions).

## Files

| File | Purpose |
|---|---|
| `index.html` | Structure only: `#scrim`, `#stage` (`#keys`, `#comet`, `#flash`, `#outline` + handles), `#toolbar`. |
| `style.css` | All visual design: key glass look, comet canvas, layout-mode chrome, per-mode visibility. |
| `overlay.js` | Bridge API (`window.swipetype.{init,events,setMode}`), coordinate mapping, key generation, comet renderer, layout-mode drag/resize, demo mode. |
| `fonts/` | Self-hosted font files (optional: `Inter-Regular.woff2`, `Inter-Medium.woff2`). Falls back to system-ui. No remote fonts, ever. |
| `demo/keys.js` | Browser-preview copy of `decoder.geometry.KEY_CENTERS`. Never used in production. Checked by `scratch/check_ui_keys.py`. |
| `demo/demo-path.js` | One recorded swipe ("appropriate") replayed in demo mode. |

## Tunables

At the top of `overlay.js`: `TRAIL_MS`, `HEAD_WIDTH_U`, `TAIL_WIDTH_U`, `SMOOTHING`, `HEAD_DISC_FRESH_MS`, `ACTIVE_KEY_FRESH_MS`, `TRAIL_BUCKETS`, and `COMET_PASSES` (the layered glow and core strokes; no canvas blur). Age buckets batch neighboring curves, bounding strokes to `3 × TRAIL_BUCKETS` per frame instead of one per point/pass. Nearest-key feedback uses one cached `.gk-active-glow` translated over the static keys rather than changing their box shadows. Key look: `.gk-key` in `style.css`. Everything is sized in units of `--u` (pixels per key-unit), set by JS.

The 420 ms tail and animation frame rate are unchanged. Clearing a gesture or entering idle/layout mode cancels outstanding animation immediately; a hidden overlay drops incoming trace events. The host sends the current daemon mode to newly connected clients and returns to idle on disconnect.

Hardware-free regression checks: `python -m unittest discover -s tests -v` and `node tests/test_overlay_ui.cjs`. For CPU comparisons without synthetic input injection, use `python scratch/profile_overlay.py --seconds 10` (see its help for comparing a Git revision). CPU is reported per logical core and normalized across logical CPUs; normalized CPU is not a battery-power estimate.

## Preview in a plain browser (no daemon, no compositor)

Open `overlay-app/ui/index.html?demo` in Firefox or Chromium. The page replays the recorded swipe in a loop.
`?demo&mode=layout` shows layout-mode chrome. The demo page has a plain white background, so the translucent keys look flat there. That is expected, because the real overlay sits over your desktop.

## Dev mode in the real overlay

First stop `swipetype-overlay.service` and any other overlay instance. Otherwise
GApplication forwards to the existing owner, whose dev-mode environment cannot
be changed by starting a second process. Restart the service when finished.

```bash
SWIPETYPE_OVERLAY_DEV=1 python overlay-app/swipetype_overlay.py
```

This enables the WebKit inspector (right-click, Inspect Element) and reloads the page automatically whenever a file in `ui/` changes. Edit CSS, save, and see it. State is re-sent through the normal load, then `init()` path.

## Layout persistence

Saved to `${XDG_STATE_HOME:-~/.local/state}/swipetype/overlay-layout.json` (`{version, cx, cy, w}`, fractions of the screen). It is per-user runtime state, never packaged. Missing or corrupt means "fit to screen". Fit screen saves the default values. Reset deletes the file.

## Rules

- Classic `<script>` tags only (no ES modules, since `file://` module loading is blocked).
- No external URLs of any kind (the host app also blocks navigation).
- No `backdrop-filter`: nothing sits behind the page except the compositor, so it does nothing.
- If you add files to `ui/`, no PKGBUILD change is needed (`ui/` is copied recursively).