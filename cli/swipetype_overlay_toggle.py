#!/usr/bin/env python3
"""Start the optional overlay when absent, then show/toggle it via GApplication.

Bind this helper, not a bare `gapplication action`, so the first shortcut also
works after login or after the overlay has been stopped. Requires the installed
or checkout-specific swipetype-overlay.service user unit.
"""
import argparse
from pathlib import Path
import subprocess
import sys

APP_ID = 'dev.dishitaghuge.swipetype.overlay'
SERVICE = 'swipetype-overlay.service'


def overlay_running() -> bool:
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'NameHasOwner',
            GLib.Variant('(s)', (APP_ID,)), GLib.VariantType.new('(b)'),
            Gio.DBusCallFlags.NONE, 5000, None,
        )
        return reply.unpack()[0]
    except GLib.Error as exc:
        raise RuntimeError(f'Cannot contact the graphical session bus: {exc}') from exc


def overlay_command() -> list[str]:
    here = Path(__file__).resolve().parent
    # Installed helper and host are siblings; in a checkout this is cli/.
    candidates = (here / 'swipetype_overlay.py', here.parent / 'overlay-app/swipetype_overlay.py')
    for host in candidates:
        if host.is_file():
            return [sys.executable, str(host)]
    raise RuntimeError('Cannot locate swipetype_overlay.py next to the helper or in the checkout')


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        running = overlay_running()
        if not running:
            subprocess.run(['systemctl', '--user', 'start', SERVICE], check=True)
        # Type=dbus makes start wait for the service's bus name. The host
        # defers this request until its daemon mode snapshot is known.
        return subprocess.call(overlay_command() + ['--toggle' if running else '--show'])
    except (OSError, RuntimeError, ImportError, subprocess.CalledProcessError) as exc:
        print(f'SwipeType overlay shortcut failed: {exc}\n'
              f'Check: systemctl --user status {SERVICE}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
