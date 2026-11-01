# SwipeType overlay UI

UI source: hand-written static HTML/CSS/vanilla JS, with a Lovable export used only as a visual reference (PRD D1 default). There is no build step, no bundler and no npm.

Layout-mode trigger: the same keybind as the overlay-visibility toggle (PRD D2, changed by request). It is the `toggle-overlay` GApplication action, bound in Hyprland as
`gapplication action dev.dishitaghuge.swipetype.overlay toggle-overlay`.
- Swipe mode ON: show or hide the ghost keyboard.
- Swipe mode OFF: enter or exit layout mode (drag and resize).

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

At the top of `overlay.js`: `TRAIL_MS`, `HEAD_WIDTH_U`, `TAIL_WIDTH_U`, `SMOOTHING`, `HEAD_DISC_FRESH_MS`, `ACTIVE_KEY_FRESH_MS`, and `COMET_PASSES` (the layered glow and core strokes; no canvas blur, which was a CPU hotspot). Key look: `.gk-key` in `style.css`. Everything is sized in units of `--u` (pixels per key-unit), set by JS.

## Preview in a plain browser (no daemon, no compositor)

Open `overlay-app/ui/index.html?demo` in Firefox or Chromium. The page replays the recorded swipe in a loop.
`?demo&mode=layout` shows layout-mode chrome. The demo page has a plain white background, so the translucent keys look flat there. That is expected, because the real overlay sits over your desktop.

## Dev mode in the real overlay

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