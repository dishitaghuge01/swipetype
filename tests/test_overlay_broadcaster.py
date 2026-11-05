"""Hardware/GUI-free regression checks: python -m unittest discover -s tests."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from overlay.broadcaster import OverlayBroadcaster


class BroadcasterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='swipetype-socket-test-')
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {'XDG_RUNTIME_DIR': self.directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.server = OverlayBroadcaster()
        self.server.start()
        self.addCleanup(self.server.stop)

    def connect(self):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        client.connect(self.server.socket_path)
        stream = client.makefile('rb')
        self.addCleanup(client.close)
        self.addCleanup(stream.close)
        return stream

    def event(self, stream):
        return json.loads(stream.readline())

    def test_idle_snapshot_on_initial_connection(self):
        self.assertEqual(self.event(self.connect()), {'event': 'mode', 'swipe_mode': False})

    def test_late_connection_receives_current_swipe_mode(self):
        self.server.broadcast_mode(True)  # no clients yet
        self.assertEqual(self.event(self.connect()), {'event': 'mode', 'swipe_mode': True})

    def test_existing_and_new_clients_see_ordered_state(self):
        first = self.connect()
        self.event(first)  # consume initial idle
        self.server.broadcast_mode(True)
        second = self.connect()
        self.assertEqual(self.event(first), self.event(second))
        self.server.broadcast_point(2.5, 1.0)
        self.server.broadcast_mode(False)
        for stream in (first, second):
            self.assertEqual(self.event(stream), {'event': 'point', 'x': 2.5, 'y': 1.0})
            self.assertEqual(self.event(stream), {'event': 'mode', 'swipe_mode': False})
        self.assertEqual(self.event(self.connect()), {'event': 'mode', 'swipe_mode': False})

    def test_connect_racing_with_mode_change_never_misses_update(self):
        for _ in range(20):
            self.server.broadcast_mode(False)
            with self.server._clients_lock:
                stream = self.connect()  # accept and broadcast both wait on lock
                worker = threading.Thread(target=self.server.broadcast_mode, args=(True,))
                worker.start()
            first = self.event(stream)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            if not first['swipe_mode']:
                self.assertEqual(self.event(stream), {'event': 'mode', 'swipe_mode': True})
            self.server.broadcast_mode(False)
            self.assertEqual(self.event(stream), {'event': 'mode', 'swipe_mode': False})

    def test_new_daemon_starts_idle_not_with_old_mode(self):
        self.server.broadcast_mode(True)
        self.server.stop()
        self.server = OverlayBroadcaster()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.assertEqual(self.event(self.connect()), {'event': 'mode', 'swipe_mode': False})

    def test_stop_closes_client_and_accept_thread(self):
        stream = self.connect()
        self.event(stream)
        self.server.stop()
        self.assertEqual(stream.readline(), b'')
        self.assertFalse(self.server._thread.is_alive())
        self.assertFalse(Path(self.server.socket_path).exists())


if __name__ == '__main__':
    unittest.main()
