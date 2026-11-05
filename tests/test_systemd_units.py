"""Rootless service-graph validation; no service is started or installed."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    shutil.which('systemd-analyze') and Path('/usr/lib/systemd/user/basic.target').is_file()
    and os.environ.get('XDG_RUNTIME_DIR') and Path(os.environ['XDG_RUNTIME_DIR']).is_dir(),
    'requires systemd user unit files and a user runtime directory',
)
class ServiceGraphTests(unittest.TestCase):
    def test_packaged_services_can_join_graphical_session_target(self):
        with tempfile.TemporaryDirectory(prefix='swipetype-unit-graph-') as temp:
            directory = Path(temp)
            target = directory / 'graphical-session.target'
            # Simulate the Wants links created by `systemctl --user enable`.
            target.write_text('[Unit]\nDescription=Isolated test session\n'
                              'Wants=swipetype.service swipetype-overlay.service\n')
            for name in ('swipetype.service', 'swipetype-overlay.service'):
                lines = (ROOT / 'packaging' / name).read_text().splitlines()
                # Installed /usr/bin/swipetype-* does not exist before Phase 4
                # installation. Substitute executables only, retain ordering,
                # lifetime and D-Bus declarations from the actual units.
                text = '\n'.join('ExecStart=/usr/bin/true' if line.startswith('ExecStart=')
                                 else line for line in lines) + '\n'
                (directory / name).write_text(text)
            env = os.environ.copy()
            env['SYSTEMD_UNIT_PATH'] = f'{directory}:/usr/lib/systemd/user'
            result = subprocess.run(
                ['systemd-analyze', '--user', '--generators=false', 'verify', str(target)],
                env=env, text=True, capture_output=True, timeout=20,
            )
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn('ordering cycle', output.lower(), output)


if __name__ == '__main__':
    unittest.main()
