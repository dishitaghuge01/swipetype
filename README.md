# SwipeType

Local swipe-to-type input for Linux touchpads. Toggle exclusive touchpad ownership,
swipe over a QWERTY layout, and commit words into the focused application. A quick
one-finger left flick deletes a character; a simultaneous multi-finger left flick
deletes the remainder of the last SwipeType-committed word.

The optional transparent WebKitGTK ghost keyboard provides a live trace and a
movable/resizable layout. Core injection supports X11 (`xdotool`) and Wayland
(`ydotool`); the overlay requires a layer-shell compositor such as Hyprland,
Sway, river or labwc. GNOME/KDE overlay support is out of scope.

## Arch packaging

[Packaging, dependencies, installation and release checklist](packaging/README.md).

- `swipetype-git`: daemon and swipe-mode toggle; no GTK/WebKit dependency.
- `swipetype-overlay-git`: optional host, static UI, shortcut helper and user service.
- Local recipes for missing `python-locate` and `python-wordfreq` dependencies
  are included under `packaging/deps/`.

**AUR submission is deferred while registration is closed.** Recipes and `.SRCINFO`
are prepared locally; no AUR availability is claimed. Local builds do not require
an AUR account. Full dependency-installed/package-installed testing is a separate
Phase 4 gate, not replaced by the successful archive/staging smoke tests.

## Run from a checkout

Install system packages appropriate to your setup: Python, GTK4, gtk4-layer-shell,
WebKitGTK 6.0, PyGObject/Pycairo for the overlay, and your injection backend. For
example, with a working system Python/GTK installation:

```bash
python -m venv --system-site-packages .venv
.venv/bin/pip install evdev wordfreq PyYAML
.venv/bin/python daemon.py
```

The user must be able to read/grab the touchpad. Wayland additionally needs a
configured `ydotoold` and virtual-input permissions; installing its client alone
is not enough. See the packaging guide's permissions/backend notes.

```bash
# Separate terminal, or bind this command to Super+Ctrl+Space:
.venv/bin/python cli/swipetype_toggle.py
```

For terminal-independent checkout services, copy `systemd/swipetype.service` and
`systemd/swipetype-overlay.service` to `~/.config/systemd/user/`, adjusting their
`%h/swipetype` paths if your checkout is elsewhere. The checkout overlay service
is tied to `graphical-session.target`; the existing checkout core unit retains
its original settings. Then reload and enable/start the services:

```bash
systemctl --user daemon-reload
systemctl --user enable --now swipetype.service swipetype-overlay.service
```

For Super+Shift+K, bind an **absolute** command to
`.venv/bin/python /absolute/checkout/cli/swipetype_overlay_toggle.py`.
The helper starts the overlay service if absent; it shows/toggles the keyboard
while swiping, or enters/exits layout editing while idle. A bare `gapplication`
action does not reliably start an absent, uninstalled overlay.

## Configuration and state

`config.yaml` currently reads only `device_path` (null = auto-detect) and
`ghost_overlay.enabled`. Geometry/decoder tunables remain source constants.
Overlay placement is saved separately under
`${XDG_STATE_HOME:-~/.local/state}/swipetype/overlay-layout.json`.
See [UI development documentation](overlay-app/ui/README.md) for browser demo,
styling, dev mode and layout controls.

## Tests and limitations

```bash
python -m unittest discover -s tests -v
node tests/test_overlay_ui.cjs
```

These checks do not need hardware/GTK or an npm install. Optional integration
and CPU tools are documented in
[the verification report](docs/validation/v2-phase2-fixes-2026-11-05.md).
The desktop integration script injects actual input and must run unattended.
Before integration/replay/manual-dev runs, stop `swipetype-overlay.service` and
any manual overlay: GApplication deliberately allows one overlay owner per
session bus. Restart the service afterward. Private test sockets alone do not
isolate that application identity.

Recognition is an English dictionary/template matcher, not a contextual language
model. Words are lowercase with trailing spaces; no punctuation/capitalization
interface or candidate picker. Deletion history does not track manual edits,
caret movement or application switches: test in a disposable text field first.
Recognition is offline, but decoded words/candidates are printed to logs (including
journald when run as a service). Treat logs as potentially sensitive.

License: [MIT](LICENSE).
