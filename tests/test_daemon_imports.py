"""Import the daemon without constructing it; no devices or GUI are opened."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(Path('/usr/bin/python').is_file(), 'requires target system Python')
class DaemonImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe = subprocess.run(
            ['/usr/bin/python', '-I', '-c',
             'import importlib.util; raise SystemExit(any(importlib.util.find_spec(n) is None '
             'for n in ("evdev","wordfreq","yaml")))'],
            capture_output=True, timeout=20,
        )
        if probe.returncode:
            raise unittest.SkipTest('system Python runtime dependencies are not installed')

    def test_foreign_cwd_config_cannot_shadow_private_config(self):
        with tempfile.TemporaryDirectory(prefix='swipetype-cwd-import-') as temp:
            cwd = Path(temp)
            (cwd / 'config.py').write_text('raise RuntimeError("wrong cwd config imported")\n')
            script = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import daemon, config
assert Path(config.__file__).resolve() == Path(sys.argv[1])/'config.py', config.__file__
assert 'gi' not in sys.modules
'''
            result = subprocess.run(['/usr/bin/python', '-I', '-c', script, str(ROOT)],
                                    cwd=cwd, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
