#!/usr/bin/env python3
"""
cli/swipetype_toggle.py

Tiny CLI: connects to the SwipeType daemon's Unix domain socket and
sends "TOGGLE". Bind this script to a keyboard shortcut (PRD section
7.3) to switch the daemon between IDLE and SWIPE_MODE.
"""

import os
import socket
import sys


def main() -> int:
    socket_path = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/swipetype.sock"

    if not os.path.exists(socket_path):
        print(
            f"Error: SwipeType daemon is not running (no socket at {socket_path})",
            file=sys.stderr,
        )
        return 1

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(socket_path)
            sock.sendall(b"TOGGLE")
    except OSError as e:
        print(f"Error: could not send TOGGLE to daemon: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())