# Installed service/runtime validation — 2026-11-06

## Status

**Original v2 Phase 4 is complete for the tested Arch/Hyprland laptop.**
Installed service/runtime checks, same-version uninstall/reinstall, a repeated
clean build/payload comparison, and final user confirmation all passed.
Clean-chroot testing remains separately tracked pre-publication hardening, not a
claim made by this sign-off. AUR publication remains deferred.

Packages: `swipetype-git` and `swipetype-overlay-git`, both `r34.a8dc766-1`,
built from the local repository, not unpublished GitHub changes. `pacman -Qkk`
reported 34 core files / 25 overlay files, **0 altered** in each package.

## Checkout → installed migration

Before switching, verified swipe mode was off and the saved unit/binding files
still matched the prepared backup. Only the two SwipeType binding commands changed:

- Super+Ctrl+Space → `/usr/bin/swipetype-toggle`
- Super+Shift+K → `/usr/bin/swipetype-overlay-toggle`

Stopped/disabled the checkout units, moved their user overrides out of the unit
search path, reloaded the user manager and enabled/started both vendor units.
Hyprland configuration reloaded with no errors. Failure rollback was prepared
before switching; it was not needed.

Private backup and guarded rollback:

```text
/home/dishita/.local/state/swipetype/phase4-backups/20261105-234322/
/home/dishita/.local/state/swipetype/phase4-backups/20261105-234322/rollback.sh
```

Both services now resolve `FragmentPath` under `/usr/lib/systemd/user`, run
`/usr/bin/python3` with scripts under `/usr/share`, and use `/home/dishita` as
working directory, not the checkout or its venv. Installed normal-interpreter
imports, including a foreign-cwd config-shadow check, resolved to `/usr/share`
and system site-packages; the 29,152-entry dictionary loaded successfully.
No root-owned configuration was changed.

## Installed end-to-end run

Reused the integration harness with explicit installed targets, a **private
session bus**, temporary config/state, virtual touchpad and disposable editor.
The normal installed services stayed running. Config lookup alone selected the
private virtual device; decoding, injection and rendering were actual installed
code. An observer subclass counted DOM keys/points/Canvas strokes.

The test overlay additionally ran in bubblewrap with:

- its own network namespace (direct probe returned `ENETUNREACH`);
- checkout path masked by an empty mount;
- installed files readable, test state/cache/tmp writable.

**18 checks passed, none failed.** Performance was explicitly skipped, not claimed
re-measured. Passed cases:

- Actual installed overlay/config paths, masked checkout and network isolation.
- `hello world ` input; character delete and subsequent two-finger word delete.
- Live comet drawing, editor focus and real click-through.
- Hidden overlay does not render while typing continues.
- Typing with no overlay, late overlay startup in swipe mode.
- WebKit web-process SIGKILL/recovery with continued typing and trace recovery.
- Daemon disconnect, restart-state synchronization and reconnection.
- Layout state persistence and corrupt JSON fallback (real JS bridge, not a
  physical pointer-drag test).
- Disabled overlay: no window/WebKit helpers, typing unaffected.

All editor/test processes, private bus, sandbox helpers and virtual input device
were cleaned up; focus returned to the user's prior window. No normal service
was stopped by this run. No normal saved layout/config was overwritten.

Reproduce in an unattended Hyprland session:

```bash
SWIPETYPE_TEST_CORE_ROOT=/usr/share/swipetype \
SWIPETYPE_TEST_OVERLAY_HOST=/usr/share/swipetype-overlay/swipetype_overlay.py \
SWIPETYPE_TEST_ISOLATE_OVERLAY=1 SWIPETYPE_TEST_SKIP_PERF=1 \
/usr/bin/python scratch/verify_v2_phase2.py
```

## Real service/shortcut lifecycle checks

With normal swipe mode off, stopped just the real overlay and invoked the
installed helper twice. Both commands succeeded; cold start created a managed
installed process and warm invocation reused it. Then restarted the real daemon:
its PID changed while the real overlay PID stayed the same. Both services were
active and the new daemon socket reported idle. No typing was injected in these
live lifecycle checks. After the reinstall, the user confirmed that all
shortcuts, swipes, etc. work well, completing the physical-use check separately
from the programmatic helper checks.

## Reinstall gate

`scratch/reinstall_packages.sh` is an interactive, reviewed-archive test. Before
stopping anything it checks package names/versions, pristine installed config,
vendor unit paths, idle mode, and the prepared rollback guards. It removes only
the two app packages (not their dependencies), proves the managed app files were
removed, reinstalls the exact archives, then verifies versions, service startup,
idle socket, config and layout preservation. Sudo prompts remain in the user's
terminal; no automatic confirmation or privilege-policy change is used.

On cancellation/failure it attempts the prepared checkout rollback. The rollback
was adjusted to restore saved units even if vendor unit names are absent after
removal. A fake-HOME/mock-command test passed that failure path without touching
real services. The user then ran the real interactive test successfully. Its
receipt reports **PASS** for same versions, active vendor services, config and
layout preservation; the removal receipt confirms managed application files were
removed before reinstalling. Follow-up checks verified both packages remain
`r34.a8dc766-1`, with **0 altered files**, both services enabled/active under
`/usr/lib/systemd/user`, and no Hyprland configuration errors.

## Repeated clean build

After reinstall, rebuilt the exact tested source with
`SWIPETYPE_SOURCE_URL=file:///home/dishita/swipetype#commit=a8dc766`, normal
dependency checks and a fresh source/build directory. The build passed. Compared
all shipped `usr/` file contents and modes with the installed candidates: **both
payloads match exactly** (33 core entries, 24 overlay entries beneath `usr/`).
This excludes build metadata/timestamps: it is not a byte-for-byte archive
reproducibility or clean-chroot claim. No further installation was needed for
this comparison.

## Remaining release work (outside this local sign-off)

- Clean-chroot and broader-machine/session testing remain additional hardening.
- X11/aarch64, a real post-migration logout/login and battery/thermal benchmarking
  are not claimed tested here.
- Review/push the local commits before testing the default public GitHub source.
  No push was performed; AUR registration/publication remains deferred.
- Existing documented recognition/history and platform limitations still apply.

Evidence: [installed run artifacts](v2-phase4-installed-2026-11-06/), plus the
[earlier package build record](v2-phase4-2026-11-05.md).
