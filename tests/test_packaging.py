"""Stage both package functions without root, downloads, GTK, or system changes."""
import hashlib
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / 'packaging'
RECIPES = (PACKAGING / 'PKGBUILD', *sorted((PACKAGING / 'deps').glob('*/PKGBUILD')))


def shell_launchers(directory):
    # makepkg outputs share this prefix; archives and unit files are not shell.
    return sorted(path for path in directory.glob('swipetype-*')
                  if path.is_file() and not path.suffix)


def shell(script, *args, **kwargs):
    env = os.environ.copy()
    env.pop('SWIPETYPE_SOURCE_URL', None)
    env.update(kwargs.pop('env', {}))
    return subprocess.check_output(['bash', '-eu', '-c', script, 'packaging-test', *map(str, args)],
                                   env=env, **kwargs)


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='swipetype-package-test-')
        cls.addClassCleanup(cls.directory.cleanup)
        base = Path(cls.directory.name)
        src = base / 'src'
        src.mkdir()
        (src / 'swipetype').symlink_to(ROOT, target_is_directory=True)
        # Auxiliary files are declared makepkg sources, not copied from Git.
        for name in ('swipetype-daemon', 'swipetype-toggle', 'swipetype-overlay',
                     'swipetype-overlay-toggle', 'swipetype.service',
                     'swipetype-overlay.service', 'dev.dishitaghuge.swipetype.overlay.desktop'):
            shutil.copyfile(PACKAGING / name, src / name)
        cls.stages = {}
        cls.dependencies = {}
        for name in ('swipetype-git', 'swipetype-overlay-git'):
            stage = base / name
            stage.mkdir()
            output = shell('source "$1"; pkgname=$2; "package_$2"; printf "%s\\0" "${depends[@]}"',
                           PACKAGING / 'PKGBUILD', name,
                           env={'srcdir': str(src), 'pkgdir': str(stage)})
            cls.stages[name] = stage
            cls.dependencies[name] = output.decode().rstrip('\0').split('\0')

    def test_core_is_dependency_light_and_has_no_venv(self):
        stage = self.stages['swipetype-git']
        self.assertTrue((stage / 'usr/share/swipetype/daemon.py').is_file())
        self.assertTrue((stage / 'usr/share/swipetype/config.yaml').is_file())
        self.assertTrue((stage / 'usr/lib/systemd/user/swipetype.service').is_file())
        self.assertFalse((stage / 'usr/share/swipetype/.venv').exists())
        self.assertFalse((stage / 'usr/share/swipetype/cli/swipetype_overlay_toggle.py').exists())
        deps = self.dependencies['swipetype-git']
        self.assertIn('python-wordfreq>=3.1.1', deps)
        self.assertFalse(any('gtk' in p or 'gobject' in p or 'webkit' in p for p in deps))

    def test_build_dependencies_cover_split_external_dependencies(self):
        declared = shell('source "$1"; printf "%s\\0" "${makedepends[@]}"',
                         PACKAGING / 'PKGBUILD').decode().rstrip('\0').split('\0')
        base_name = lambda dependency: re.split(r'[<>=]', dependency, maxsplit=1)[0]
        required = {base_name(dep) for deps in self.dependencies.values() for dep in deps}
        required.difference_update(self.stages)  # sibling packages are built here
        self.assertLessEqual(required, {base_name(dep) for dep in declared})

    def test_overlay_host_and_ui_are_siblings(self):
        stage = self.stages['swipetype-overlay-git']
        for name in ('swipetype_overlay.py', 'swipetype_overlay_toggle.py',
                     'ui/index.html', 'ui/overlay.js', 'ui/style.css', 'ui/demo/keys.js'):
            self.assertTrue((stage / 'usr/share/swipetype-overlay' / name).is_file(), name)
        self.assertIn('webkitgtk-6.0', self.dependencies['swipetype-overlay-git'])
        self.assertTrue((stage / 'usr/lib/systemd/user/swipetype-overlay.service').is_file())

    def test_launchers_point_at_staged_files_not_build_or_home_paths(self):
        targets = {'swipetype-git': ('swipetype-daemon', 'swipetype-toggle'),
                   'swipetype-overlay-git': ('swipetype-overlay', 'swipetype-overlay-toggle')}
        for package, names in targets.items():
            stage = self.stages[package]
            for name in names:
                launcher = stage / 'usr/bin' / name
                self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o755)
                command = next(line for line in launcher.read_text().splitlines() if line.startswith('exec '))
                self.assertIn('/usr/bin/python3', command)
                target = command.split()[2]
                self.assertTrue((stage / target.lstrip('/')).is_file(), target)
                self.assertNotIn('.venv', command)
                self.assertNotIn('/home/', command)

    def test_only_intended_files_and_permissions_are_shipped(self):
        for stage in self.stages.values():
            for path in stage.rglob('*'):
                self.assertNotIn('__pycache__', path.parts)
                self.assertNotEqual(path.suffix, '.pyc')
                self.assertNotIn('.git', path.parts)
                if path.is_file() and path.suffix in ('.py', '.html', '.js', '.css', '.service'):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644, str(path))

    def test_auxiliary_source_checksums_match(self):
        values = shell('source "$1"; printf "%s\\0" "${source[@]}"; printf "SEP\\0"; '
                       'printf "%s\\0" "${sha256sums[@]}"', PACKAGING / 'PKGBUILD').decode().split('\0')
        sep = values.index('SEP')
        sources, hashes = values[:sep], values[sep + 1:-1]
        self.assertEqual(len(sources), len(hashes))
        self.assertEqual(hashes[0], 'SKIP')
        self.assertIn('git+https://github.com/dishitaghuge01/swipetype.git', sources[0])
        for name, digest in zip(sources[1:], hashes[1:]):
            self.assertEqual(hashlib.sha256((PACKAGING / name).read_bytes()).hexdigest(), digest, name)

    @unittest.skipUnless(shutil.which('makepkg'), 'makepkg is only available on Arch build hosts')
    def test_srcinfo_is_current(self):
        for recipe in RECIPES:
            env = os.environ.copy()
            env.pop('SWIPETYPE_SOURCE_URL', None)
            generated = subprocess.check_output(['makepkg', '--printsrcinfo'], cwd=recipe.parent, env=env)
            self.assertEqual(generated, (recipe.parent / '.SRCINFO').read_bytes(), str(recipe))

    def test_recipes_do_not_use_path_selected_virtualenv_python(self):
        for recipe in RECIPES:
            text = recipe.read_text()
            self.assertNotRegex(text, r'(?<![/\w-])python\s+-(?:m|c)\b', str(recipe))
            self.assertIn('/usr/bin/python', text, str(recipe))

    def test_shell_discovery_ignores_generated_archives(self):
        with tempfile.TemporaryDirectory(prefix='swipetype-shell-discovery-') as temp:
            directory = Path(temp)
            (directory / 'swipetype-daemon').write_text('#!/bin/sh\nexit 0\n')
            (directory / 'swipetype-git-r1.hash-1-any.pkg.tar.zst').write_bytes(b'not shell')
            (directory / 'swipetype.service').write_text('[Unit]\nDescription=not shell\n')
            self.assertEqual([path.name for path in shell_launchers(directory)], ['swipetype-daemon'])

    def test_recipes_and_launchers_have_valid_shell_syntax(self):
        for path in RECIPES:
            subprocess.run(['bash', '-n', path], check=True)
        for path in shell_launchers(PACKAGING):
            subprocess.run(['sh', '-n', path], check=True)


if __name__ == '__main__':
    unittest.main()
