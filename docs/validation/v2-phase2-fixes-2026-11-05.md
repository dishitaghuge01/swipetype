# Phase 2 fixes and performance clarification — 2026-11-05

Follow-up to the [baseline verification](v2-phase2-2026-11-05.md).

## Status

- **Mode synchronization: fixed and verified.** Commit `7253469`.
- **Rendering CPU: improved, not proven to meet the original low-single-digit
  expectation.** Commit `897478b`. That expectation had no stated CPU convention
  or hardware baseline; do not silently reinterpret it as an achieved target.
- **Physical usability confirmed by the user after testing:** “everything is
  working fine and feels natural and smooth.” This closes the subjective swipe
  feel/smoothness check and supports proceeding to Phase 3 packaging.
- Battery/thermal impact has not been measured or explicitly confirmed. No
  replacement numerical CPU acceptance limit is assigned here; the original
  low-single-digit one-core expectation is not retroactively marked passed.

## Changes shipped

### Connection state

`OverlayBroadcaster` retains mode even with no clients. Connecting clients receive
an initial snapshot under the same lock as mode updates, so a snapshot cannot
miss/overtake a concurrent transition. A new daemon initializes this state to idle.
The overlay also returns to idle on socket EOF before retrying. Server shutdown
wakes its accept thread, and failed connection attempts close promptly.

The overlay now discards trace events while visually hidden or disabled, rather
than drawing invisibly. Typing is unaffected. Local layout editing remains
independent of the daemon's connection state.

### Rendering

- Group adjacent midpoint-Bezier segments into eight age buckets, at most 24
  strokes per frame for the three glow/core passes, rather than one per point/pass.
- Translate one cached key-glow element instead of restyling/repainting the glass
  keys' shadows and legends.
- Cancel outstanding animation on clear/idle/layout, and guard resize-before-init.
- Preserve the 420 ms trail, smoothing, full canvas resolution and animation rate.
  No frame-rate reduction or lower-resolution rendering was shipped.

Experimental reply-based IPC and WebGL rendering did not produce a validated
advantage and were removed. The existing Unix-socket/JSON/evaluate-JavaScript
architecture and Canvas2D renderer remain in use.

## Functional verification

Run with the isolated integration harness, against production code matching the
above commits. **17 checks passed**, including:

- Late overlay startup while already swiping: received `swipe`, visible immediately.
- Daemon disconnection: overlay reset to `idle` without a toggle.
- Replacement daemon started idle: overlay stayed idle, then followed the next
  actual mode event and reconnected normally.
- Synthetic `hello world `, one-character delete and subsequent word delete.
- Click-through and focus retention in a real disposable GTK TextView editor.
- Visible trace actually received points and issued Canvas strokes (not just
  working injection with an accidentally disabled renderer).
- Hidden overlay stopped receiving/rendering trace points while typing continued.
- Overlay termination did not stop typing.
- SIGKILL of WebKit web process: page, bridge and trace drawing recovered, and
  daemon injection continued.
- Saved layout restoration, corrupt-state fallback, disabled-overlay startup
  without helper processes, and typing while disabled.

The integration run **was not a complete successful benchmark run**: after those
functional checks and its idle measurement, editor focus changed during active
synthetic input. The safety guard aborted further input and cleaned up. The
result JSON intentionally retains that runner error. Its active CPU window is
not used or treated as a successful measurement.

Six hardware-free socket regression tests passed, including 20 connection/toggle
races; Node DOM/Canvas-stub checks cover batching bounds, cached glow, gesture
expiry, clear cancellation, idle/layout cancellation and pre-init resize.

## Matched CPU comparison (no desktop input injection)

Both revisions ran sequentially via `scratch/profile_overlay.py`, with:

- Intel Core i5-1235U, 10 physical cores / 12 logical CPUs; ~16 GB installed RAM.
- Intel Alder Lake integrated graphics, Hyprland/Wayland, 1920×1080 at ~60 Hz,
  scale 1. Package versions match the baseline report.
- AC connected, balanced power profile, powersave governor (queried before the
  comparison; not forcibly locked during the measurement).
- 5-second warm-up, 10-second idle and 10-second active windows per revision.
- Same ideal `hello` path, 70 samples/cycle, ~8 ms per point, no inter-word delay;
  1,213 baseline versus 1,208 optimized points sent. This is continuous replay,
  not the same pacing as the earlier editor-based baseline.
- Dev mode off; uninstrumented production overlay, private Unix socket/config/
  XDG state. No input-device grab, ydotool injection, mouse operation or focus
  change. Only the overlay process tree is measured.

| Metric | Baseline `1bb12ef` | Optimized `897478b` code |
|---|---:|---:|
| Idle CPU, one-logical-CPU convention | 0.30% | 0.20% |
| Active CPU, one-logical-CPU convention | 71.94% | 53.23% |
| Active CPU, divided by 12 logical CPUs | 6.00% | 4.44% |
| Idle tree PSS | 311.08 MiB | 311.59 MiB |
| Active tree PSS | 328.87 MiB | 327.93 MiB |

Active CPU fell approximately **26%** in this paired run. Earlier exploratory
replays also showed a reduction (~75% to ~53%). Short windows, CPU scheduling,
frequency scaling and other desktop work introduce variability; these are not
battery or thermal benchmarks or a statistical guarantee.

`100%` under the first convention means one CPU-second per elapsed second;
53.23% is ~0.532 CPU-seconds/second, not 53.23% of the laptop. Dividing by 12 gives
an aggregate utilization convention, not equal-performance capacity: this is a
hybrid P/E-core CPU with SMT. Neither convention estimates watts. GPU load and
compositor/daemon cost are excluded.

The earlier ~285 MiB readings are not contradicted by ~311 MiB here: PSS depends
on shared-library residency and other processes running. Use the paired run for
before/after comparison rather than mixing unlike runs.

## Acceptance recommendation

The laptop specifications alone do not establish that the original target is
impossible. They do establish why CPU units matter. Treat "low single digits"
as an unvalidated goal until its workload, hardware and convention are agreed.
A practical acceptance review should consider:

1. Negligible idle cost and no hidden animation (verified here).
2. Responsive, smooth visible swiping and retained recognition/injection latency
   goals (physical experience and latency still need validation).
3. Battery/fan/thermal impact during representative real usage (not measured).
4. CPU reported explicitly with a repeatable workload and regression baseline.

Do not weaken input latency or visual feedback solely to satisfy an ambiguous
CPU percentage. Equally, do not label the original one-core CPU goal "passed"
just because a normalized percentage is smaller.

## Reproduce

```bash
python -m unittest discover -s tests -v
node tests/test_overlay_ui.cjs
.venv/bin/python scratch/profile_overlay.py --seconds 10 --source-ref 1bb12ef
.venv/bin/python scratch/profile_overlay.py --seconds 10
```

The separate full integration test injects real desktop input and needs an
unattended session:

```bash
.venv/bin/python scratch/verify_v2_phase2.py
```

Evidence: [`v2-phase2-fixes-2026-11-05/`](v2-phase2-fixes-2026-11-05/).
`integration-results.json` and `integration-run.log.txt` retain all functional
results **and the guarded benchmark abort**. `replay-baseline.json` and
`replay-optimized.json` are the complete paired CPU results. Process logs retain
GTK theme/Vulkan initialization warnings; hardware acceleration was not assumed
from these warnings or used as an explanation without proof.

Normal daemon PID `657` remained active and was not restarted; no temporary test
processes remained after cleanup. Restart the normal daemon before manually
checking new connection behavior, since its already-running Python process still
contains the old code.
