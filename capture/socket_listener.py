"""
capture/socket_listener.py

Unix domain socket listener for the SwipeType toggle mechanism (PRD
section 7.3). Runs in a background thread; on receiving the literal
bytes b"TOGGLE", invokes the callback passed at construction.
"""

import os
import socket
import threading
from typing import Callable, Optional


class SocketListener:
    def __init__(self, on_toggle: Callable[[], None]):
        self.on_toggle = on_toggle
        self.socket_path = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/swipetype.sock"
        self._server_socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(self.socket_path)
        self._server_socket.listen(1)

        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def _listen_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server_socket.accept()
            except OSError:
                # Socket was closed by stop() — exit the loop cleanly.
                break

            with conn:
                data = conn.recv(1024)
                if data == b"TOGGLE":
                    self.on_toggle()

    def stop(self) -> None:
        self._running = False
        if self._server_socket is not None:
            self._server_socket.close()
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
        if self._thread is not None:
            self._thread.join(timeout=1)