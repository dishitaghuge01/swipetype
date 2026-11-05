"""Hardware/GTK-free shortcut policy and helper subprocess tests."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('overlay_toggle', ROOT / 'cli/swipetype_overlay_toggle.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class ShortcutHelperTests(unittest.TestCase):
    def test_running_overlay_toggles_without_starting_service(self):
        with patch.object(helper, 'overlay_running', return_value=True), \
             patch.object(helper, 'overlay_command', return_value=['overlay']), \
             patch.object(helper.subprocess, 'run') as start, \
             patch.object(helper.subprocess, 'call', return_value=0) as invoke:
            self.assertEqual(helper.main([]), 0)
            start.assert_not_called()
            invoke.assert_called_once_with(['overlay', '--toggle'])

    def test_absent_overlay_starts_service_then_shows(self):
        with patch.object(helper, 'overlay_running', return_value=False), \
             patch.object(helper, 'overlay_command', return_value=['overlay']), \
             patch.object(helper.subprocess, 'run') as start, \
             patch.object(helper.subprocess, 'call', return_value=0) as invoke:
            self.assertEqual(helper.main([]), 0)
            start.assert_called_once_with(['systemctl', '--user', 'start', helper.SERVICE], check=True)
            invoke.assert_called_once_with(['overlay', '--show'])

    def test_start_failure_does_not_launch_unmanaged_copy(self):
        with patch.object(helper, 'overlay_running', return_value=False), \
             patch.object(helper.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'systemctl')), \
             patch.object(helper.subprocess, 'call') as invoke, patch('sys.stderr'):
            self.assertEqual(helper.main([]), 1)
            invoke.assert_not_called()


class ActionPolicyTests(unittest.TestCase):
    def setUp(self):
        # Execute just policy methods from the actual source. No GTK import or
        # GUI initialization is needed; desktop integration covers lifecycle.
        tree = ast.parse((ROOT / 'overlay-app/swipetype_overlay.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'OverlayApp')
        names = {'do_command_line', '_request_overlay_action', '_drain_overlay_actions', '_apply_mode'}
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.glib = types.SimpleNamespace(timeout_add=Mock(return_value=42), source_remove=Mock())
        namespace = {'GLib': self.glib, '_log': lambda *_: None}
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<overlay-policy>', 'exec'), namespace)
        app = types.SimpleNamespace(
            _activated=False, _mode_known=False, _pending_actions=[], _action_timeout_id=None,
            mode='idle', overlay_visible=True, layout_mode_active=False, window=None,
            _overlay_enabled=Mock(return_value=True), _ensure_window=Mock(), _refresh_visual_state=Mock(),
        )
        app.activate = lambda: setattr(app, '_activated', True)
        for name in names:
            setattr(app, name, types.MethodType(namespace[name], app))
        self.app = app

    def command(self, **options):
        command = Mock()
        command.get_options_dict.return_value.contains.side_effect = lambda key: bool(options.get(key))
        return command

    def test_cold_toggle_waits_for_snapshot_then_shows_swipe_keyboard(self):
        self.assertEqual(self.app.do_command_line(self.command(toggle=True)), 0)
        self.assertEqual(self.app._pending_actions, ['show'])
        self.app._apply_mode('swipe')
        self.assertTrue(self.app.overlay_visible)
        self.assertFalse(self.app.layout_mode_active)
        self.assertFalse(self.app._pending_actions)

    def test_warm_toggle_hides_swipe_keyboard(self):
        self.app._activated = True
        self.app._apply_mode('swipe')
        self.app.do_command_line(self.command(toggle=True))
        self.assertFalse(self.app.overlay_visible)

    def test_cold_idle_toggle_enters_layout(self):
        self.app.do_command_line(self.command(toggle=True))
        self.app._apply_mode('idle')
        self.assertTrue(self.app.layout_mode_active)

    def test_offline_timeout_allows_layout_then_swipe_exits_it(self):
        self.app.do_command_line(self.command(show=True))
        self.glib.timeout_add.assert_called_once()
        self.app._drain_overlay_actions()
        self.assertTrue(self.app.layout_mode_active)
        self.app._apply_mode('swipe')
        self.assertFalse(self.app.layout_mode_active)

    def test_quick_double_press_is_not_coalesced(self):
        self.app.do_command_line(self.command(toggle=True))
        self.app.do_command_line(self.command(toggle=True))
        self.app._apply_mode('swipe')
        self.assertFalse(self.app.overlay_visible)

    def test_conflicting_flags_are_rejected(self):
        command = self.command(toggle=True, show=True)
        self.assertEqual(self.app.do_command_line(command), 2)
        command.printerr_literal.assert_called_once()
        self.assertFalse(self.app._activated)

    def test_disabled_config_does_not_queue_or_create_window(self):
        self.app._overlay_enabled.return_value = False
        self.app.do_command_line(self.command(toggle=True))
        self.assertFalse(self.app._pending_actions)
        self.app._ensure_window.assert_not_called()


if __name__ == '__main__':
    unittest.main()
