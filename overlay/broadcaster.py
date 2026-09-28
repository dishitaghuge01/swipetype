"""
overlay/broadcaster.py

Unix domain socket server broadcasting live gesture events to any
connected overlay client(s), so the ghost overlay (a separate process)
can render a live trace without daemon.py knowing or caring whether
any overlay is even running. Newline-delimited JSON, one event per
line. Safe with zero clients connected — broadcast_* calls become
no-ops (write to zero sockets is trivially cheap).

Socket path mirrors capture/socket_listener.py's convention.
"""

import json
import os
import socket
import threading
from typing import List, Optional


class OverlayBroadcaster:
    def __init__(self):
        self.socket_path = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/swipetype-overlay.sock"
        self._server_socket: Optional[socket.socket] = None
        self._clients: List[socket.socket] = []
        self._clients_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(self.socket_path)
        self._server_socket.listen(5)

        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server_socket.accept()
            except OSError:
                break
            with self._clients_lock:
                self._clients.append(conn)

    def _broadcast(self, event: dict) -> None:
        line = (json.dumps(event) + "\n").encode("utf-8")
        with self._clients_lock:
            dead = []
            for client in self._clients:
                try:
                    client.sendall(line)
                except OSError:
                    dead.append(client)
            for d in dead:
                self._clients.remove(d)
                try:
                    d.close()
                except OSError:
                    pass

    def broadcast_mode(self, swipe_mode: bool) -> None:
        self._broadcast({"event": "mode", "swipe_mode": swipe_mode})

    def broadcast_clear(self) -> None:
        self._broadcast({"event": "clear"})

    def broadcast_point(self, kx: float, ky: float) -> None:
        self._broadcast({"event": "point", "x": kx, "y": ky})

    def broadcast_commit(self, word: str) -> None:
        self._broadcast({"event": "commit", "word": word})

    def stop(self) -> None:
        self._running = False
        if self._server_socket is not None:
            self._server_socket.close()
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
        if self._thread is not None:
            self._thread.join(timeout=1)