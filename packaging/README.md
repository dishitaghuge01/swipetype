# Arch packaging and release preparation

Maintainer: Dishita Ghuge <ghugedishita@gmail.com>.

**Phase 3:** recipes, launchers, services, dependency recipes and `.SRCINFO` are
prepared. **Phase 4:** dependency-installed builds, namcap, installed runtime tests,
reinstall and clean-chroot verification remain to be completed. **Phase 5:** AUR
submission is deferred while account registration is closed. No account creation,
AUR push, pacman installation or upstream Git push was performed here.

## Design and deliberate changes from the sample PRD

- One split PKGBUILD, `pkgbase=swipetype-git`, produces the core and optional
  overlay. Both contain architecture-independent scripts/assets, so `arch=any`.
- Use system Python and declared pacman dependencies. **Do not build a venv and
  copy it from `$srcdir` into `/usr/share`**, and do not pip-download untracked
  dependencies during `build()`. The app itself has no compilation step.
- Dependency source releases are pinned with SHA-256 checksums. Their build
  backends use `--no-isolation`; all build dependencies are declared.
- The overlay and its helper use system PyGObject. The core has no GTK/WebKit
  runtime dependency and can run without an overlay installation or process.
  Building the split recipe requires the external dependency union in
  `makedepends`, including GTK/WebKit; makepkg resolves that before running the
  package-specific functions. This does not broaden the core archive's runtime
  dependencies.
- User services are tied to `graphical-session.target`, inherit the session type
  instead of forcing Wayland, and stop with the graphical session. The core unit
  does not assume your distribution supplies a `ydotoold.service` of that name.
- Optional overlay automation is included, extending the original manual-launch
  plan: `swipetype-overlay-toggle` starts the service if needed and forwards
  `--show` or `--toggle` through a single GApplication instance. The overlay unit
  uses `Type=dbus`, so start waits for the application bus name.
- Installing the optional package **does not automatically enable it**, claim a
  keyboard binding, modify compositor files, grant input permissions or enable
  a virtual-input daemon. Those are explicit user choices.

## Package contents

| Package | Installed interface |
|---|---|
| `swipetype-git` | `/usr/bin/swipetype-daemon`, `/usr/bin/swipetype-toggle`, core sources/config under `/usr/share/swipetype`, `swipetype.service` |
| `swipetype-overlay-git` | `/usr/bin/swipetype-overlay`, `/usr/bin/swipetype-overlay-toggle`, host/helper/UI together under `/usr/share/swipetype-overlay`, `swipetype-overlay.service`, hidden desktop entry |

Config is backed up as `usr/share/swipetype/config.yaml`, matching the current
loader's location. Edit installed config with `sudoedit`; pacman may produce
`.pacnew` on upgrades. No `config.local.yaml` override exists. User layout state
remains writable under XDG state, never under the root-owned install tree.

## Dependencies while AUR registration is closed

Anonymous AUR reads/builds do not require an AUR account. Registration closure
blocks publishing **new listings**, not local `makepkg` work.

Recon on 2026-11-05:

- Official repos contain `python-langcodes`, `python-msgpack`, `python-regex`,
  `python-wcwidth`, and the listed Python build tools.
- AUR RPC returned an existing `python-ftfy` package (6.3.1-3).
- RPC returned no `python-locate` or `python-wordfreq` listings; neither was in
  the official repository database. Local recipes are supplied here. **Recheck
  availability before publication**; do not overwrite somebody else's package.

Build prerequisites (review before running; these commands change the system):

```bash
sudo pacman -S --needed base-devel git python-build python-installer \
  python-setuptools python-setuptools-scm python-wheel python-poetry-core \
  python-wcwidth python-langcodes python-msgpack python-regex \
  python-evdev python-yaml
```

Install/build the existing `python-ftfy` AUR package through your normal reviewed
AUR workflow (e.g. `paru -S python-ftfy` if you use paru). Then:

Build outside any activated virtualenv; the explicit clean environment also
protects third-party recipes/tools that select Python through PATH:

```bash
(cd packaging/deps/python-locate && env -u VIRTUAL_ENV -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin makepkg -si)
(cd packaging/deps/python-wordfreq && env -u VIRTUAL_ENV -u PYTHONHOME -u PYTHONPATH PATH=/usr/bin:/bin makepkg -si)
```

The reviewed `python-ftfy` AUR recipe additionally needs `python-hatchling` and
`python-pytest` from the official repositories for its build/test steps.

Neither dependency is a `-git` package. `python-wordfreq` preserves Apache-2.0
code notices plus the bundled data's CC-BY-SA-4.0/source attribution notice.
CJK extras and optional language-name datasets are not needed for English typing.
Phase 4 has built/linted and verified the system installation of `python-locate`
after fixing a venv-prefix contamination bug. The reviewed ftfy recipe also built
successfully (336 passed, 10 expected failures) and awaits user installation;
wordfreq build/install checks remain pending. See the [Phase 4 progress record](../docs/validation/v2-phase4-2026-11-05.md)
for current evidence and remaining installation gates.

## Local app build (no AUR account needed)

The normal PKGBUILD fetches the public GitHub repository. Before public builds,
**push the prepared application commits to GitHub**; an older remote revision
will not contain the new helper/source changes. No push is automatic.

For testing committed local code before an upstream push:

```bash
repo=$(git rev-parse --show-toplevel)
(cd packaging && SWIPETYPE_SOURCE_URL="file://$repo" makepkg -s)
```

VCS cloning reads Git commits, not uncommitted edits. Commit source changes first.
The local-source override is development-only: never generate release `.SRCINFO`
with it set. Once dependencies are available, install the built core archive and,
if desired, overlay archive with pacman or `makepkg -si`.

A limited archive smoke build has already succeeded using `makepkg --nodeps`
against a local Git clone. **That deliberately skipped dependency checks, installed
nothing, and is not proof of an installed runtime or a clean-chroot build.** Do
not use `--nodeps` as the normal installation workflow.

## Backend and permission setup

- Touchpad: user needs read/grab access to its evdev node. An `input` group setup
  is common but broad: it can expose keyboard events too. Prefer appropriate
  device ACLs/targeted rules where practical. Packaging grants no permissions.
- Wayland: install/configure `ydotool` and a running `ydotoold`, including correct
  `/dev/uinput` permissions and socket access. Verify your distribution's daemon
  arrangement; a unit named `ydotoold.service` is not assumed. The client normally
  uses `$XDG_RUNTIME_DIR/.ydotool_socket`; use `YDOTOOL_SOCKET` if your setup differs.
- X11: install `xdotool`; ensure the user service sees the X11 environment.
- Choose the appropriate injection backend package; both are conditional optional
  dependencies because the same core package supports either session type.
- Overlay: requires a layer-shell-capable Wayland compositor. Merely installing
  GTK dependencies does not add GNOME/KDE overlay support.

## Installed services and keybindings

With a graphical session and working injection backend:

```bash
systemctl --user daemon-reload
systemctl --user enable --now swipetype.service
# Optional login-started overlay:
systemctl --user enable --now swipetype-overlay.service
```

The shortcut helper also starts the optional overlay service on demand if not
already running. Starting the overlay wants the core service but does not couple
its lifetime to daemon restarts; it reconnects if the core restarts.

For a compositor not managing `graphical-session.target`, integrate that user
target and environment explicitly or start the services from session startup.
Do not run them at a non-graphical boot target with a stale display environment.
If necessary, from the graphical session:

```bash
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_SESSION_TYPE XDG_CURRENT_DESKTOP
```

Hyprland 0.57+ Lua examples:

```lua
hl.bind("SUPER + CTRL + SPACE", hl.dsp.exec_cmd("swipetype-toggle"))
hl.bind("SUPER + SHIFT + K", hl.dsp.exec_cmd("swipetype-overlay-toggle"))
```

For older Hyprland syntax:

```ini
bind = SUPER CTRL, SPACE, exec, swipetype-toggle
bind = SUPER SHIFT, K, exec, swipetype-overlay-toggle
```

The second key means show/hide during swipe mode and layout editing during idle.
First activation starts/shows the absent overlay; it never grabs the touchpad or
turns swipe mode on. `ghost_overlay.enabled: false` still disables its UI.

### Migrating from checkout services (important on the development laptop)

`~/.config/systemd/user/swipetype*.service` overrides packaged units under
`/usr/lib/systemd/user`. Installing a package alone will **not** replace those
checkout units or the absolute checkout paths in your compositor config.

During Phase 4, after leaving swipe mode and when ready to stop input services:

1. Disable/stop both checkout services.
2. Back up/move the two checkout unit files out of `~/.config/systemd/user/`.
3. Reload the user manager; enable/start the packaged units.
4. Change bindings to the installed commands above, reload the compositor, and
   verify `systemctl --user show <unit> -p FragmentPath` points into `/usr/lib`.
5. Test with the checkout temporarily unavailable to catch accidental imports.

Keep backups to restore the development setup if installed testing fails. Do not
silently switch a working development session to untested installed packages.

## Validation and Phase 4 checklist

Already checked: Bash/sh syntax, desktop-entry validation, all auxiliary checksums,
`.SRCINFO` generation, package-function staging/permissions, absence of a copied
venv/pycache, optional dependency split, and a local `--nodeps` archive smoke build.

Before release:

- Build dependency recipes with installed prerequisites, then app without `--nodeps`.
- `namcap PKGBUILD` and both resulting archives. Namcap is now available and
  exposed a split-build dependency issue that is fixed; final dependency-resolved
  app archives still need rechecking.
- Build in a clean Arch chroot; verify dependency declarations and offline build
  phases after makepkg has acquired the declared sources.
- Test installed core only; then optional overlay, cold/warm shortcut, login
  autostart, daemon/WebKit restarts, layout persistence, permission failures and
  config upgrades. Re-run swipes/deletions in a disposable editor.
- Verify `ui/` loads from `/usr/share`, without checkout imports, and offline
  behavior does not depend on remote assets/fonts.
- Uninstall/reinstall and verify reproducibility. Aarch64 is not tested here.
- Never enable/install services as a side effect of a PKGBUILD `package()` call.

## AUR publication later

1. Recheck registration, dependency package availability, and current packaging
   conventions. Publish/adopt `python-locate` and `python-wordfreq` if still
   missing, after their Phase 4 checks. Reuse the existing `python-ftfy` listing.
2. Push reviewed app commits to GitHub so the default VCS source contains them.
3. Refresh `.SRCINFO` with the local override unset:

   ```bash
   (cd packaging && env -u SWIPETYPE_SOURCE_URL makepkg --printsrcinfo > .SRCINFO)
   (cd packaging/deps/python-locate && makepkg --printsrcinfo > .SRCINFO)
   (cd packaging/deps/python-wordfreq && makepkg --printsrcinfo > .SRCINFO)
   ```

4. Clone the AUR **package-base** repo `swipetype-git.git` when an account/SSH key
   is available. Copy `PKGBUILD`, `.SRCINFO` and its seven local auxiliary sources
   (the four launchers, two services and desktop entry). No application source,
   archives, venv or build directories belong in that AUR repo.
5. One split-package base publishes both package listings. **Do not create a
   second AUR Git repository for `swipetype-overlay-git` with the same split
   recipe**, contrary to the older build-plan sample.
6. Dependency recipes each use their own package-base repository/metadata.
7. Review the diff, commit and submit manually; test installation outside the
   development checkout on a clean supported system.

For `-git` builds `pkgver()` is computed from commit count/hash. The checked-in
version is a seed, not a release tag. Regenerate `.SRCINFO` after recipe changes;
regenerate source-file checksums if changing any launcher/service/desktop entry.
