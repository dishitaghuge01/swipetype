#!/usr/bin/env python3
"""Validate two reviewed, locally built app archives without installation.

Usage: python scratch/check_package_payloads.py CORE.pkg.tar.zst OVERLAY.pkg.tar.zst
Imports their Python code as your user: do not use on untrusted packages.
No daemon/application instance is constructed, no input is injected, and no
service is started. System Python and an empty cwd prevent checkout/venv imports.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.paths.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.paths.append(attrs['href'])


def check(core, overlay):
    with tempfile.TemporaryDirectory(prefix='swipetype-app-payload-') as temp:
        root = Path(temp)
        metadata = {}
        for expected, archive in [('swipetype-git', core), ('swipetype-overlay-git', overlay)]:
            subprocess.run(['bsdtar', '-xf', str(archive), '-C', str(root)], check=True)
            info = {}
            for line in (root / '.PKGINFO').read_text().splitlines():
                if ' = ' in line:
                    key, value = line.split(' = ', 1)
                    info.setdefault(key, []).append(value)
            assert info['pkgname'] == [expected], info
            assert not (root / 'home').exists(), 'Unexpected home/ payload'
            assert not (root / '.INSTALL').exists(), 'Unexpected install script'
            metadata[expected] = {'archive': str(archive), 'version': info['pkgver'][0],
                                 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                                 'runtime_dependencies': info.get('depend', [])}
        assert metadata['swipetype-git']['version'] == metadata['swipetype-overlay-git']['version']
        assert not any('gtk' in p or 'webkit' in p or 'gobject' in p
                       for p in metadata['swipetype-git']['runtime_dependencies'])
        for path in root.rglob('*'):
            assert '.venv' not in path.parts, path
            assert '__pycache__' not in path.parts, path
        for name in ('swipetype-daemon', 'swipetype-toggle', 'swipetype-overlay', 'swipetype-overlay-toggle'):
            launcher = root / 'usr/bin' / name
            assert stat.S_IMODE(launcher.stat().st_mode) == 0o755, launcher
            line = next(line for line in launcher.read_text().splitlines() if line.startswith('exec '))
            assert line.split()[1] == '/usr/bin/python3', line
            target = line.split()[2]
            assert target.startswith('/usr/share/'), line
            assert (root / target.lstrip('/')).is_file(), target
        ui = root / 'usr/share/swipetype-overlay/ui'
        assets = Assets()
        assets.feed((ui / 'index.html').read_text())
        for asset in assets.paths:
            url = urlsplit(asset)
            assert not url.scheme and not url.netloc, asset
            assert (ui / asset).is_file(), asset
        css = re.sub(r'/\*.*?\*/', '', (ui / 'style.css').read_text(), flags=re.S)
        for asset in re.findall(r'url\((.*?)\)', css):
            url = urlsplit(asset.strip(' \"\''))
            assert not url.scheme and not url.netloc, asset
        # CSS's optional local Inter fonts may be absent; system fonts are the
        # intentional fallback. This audits references, not network isolation.
        empty = root / 'empty-cwd'
        empty.mkdir()
        core_dir = root / 'usr/share/swipetype'
        overlay_dir = root / 'usr/share/swipetype-overlay'
        script = r'''
import sys, json, runpy
from pathlib import Path
core, overlay = map(Path, sys.argv[1:3])
sys.path.insert(0, str(core))
import daemon, config
assert 'gi' not in sys.modules, 'Core imported optional GTK bindings'
names = ('daemon','config','capture.touchpad','capture.socket_listener',
         'decoder.dictionary','decoder.geometry','decoder.resample','decoder.scorer',
         'gesture.segmenter','inject.base','inject.xdotool_injector',
         'inject.ydotool_injector','overlay.broadcaster')
modules = {name:sys.modules[name].__file__ for name in names}
assert all(Path(p).is_relative_to(core) for p in modules.values()), modules
assert Path(config.CONFIG_PATH) == core/'config.yaml'
cfg=config.load_config()
assert cfg['device_path'] is None and cfg['ghost_overlay']['enabled'] is True, cfg
from decoder.dictionary import load_dictionary
from decoder.scorer import decode
dictionary=load_dictionary()
assert len(dictionary)>25000
results={w:decode(dictionary[w]['ideal_path'],dictionary,top_k=1)[0][0]
         for w in ('hello','world','test','help')}
assert all(w==v for w,v in results.items()), results
# Import host definitions only. main() is deliberately not called.
host=runpy.run_path(str(overlay/'swipetype_overlay.py'),run_name='phase4_host_import')
assert Path(host['UI_DIR']) == overlay/'ui', host['UI_DIR']
helper=runpy.run_path(str(overlay/'swipetype_overlay_toggle.py'),run_name='phase4_helper_import')
command=helper['overlay_command']()
assert Path(command[1]) == overlay/'swipetype_overlay.py', command
print(json.dumps({'python':sys.executable,'prefix':sys.prefix,'modules':modules,
                  'dictionary_entries':len(dictionary),'ideal_path_decodes':results,
                  'overlay_imported':True,'helper_target':command[1]}))
'''
        output = subprocess.check_output(['/usr/bin/python', '-I', '-c', script,
                                          str(core_dir), str(overlay_dir)], cwd=empty, text=True)
        return {'packages': metadata, 'imports_and_decoder': json.loads(output),
                'relative_html_assets': assets.paths,
                'scope': 'Extracted-payload/import/static-asset checks; not installed runtime or network isolation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('core', type=Path)
    parser.add_argument('overlay', type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.core.resolve(), args.overlay.resolve()), indent=2))


if __name__ == '__main__':
    main()
