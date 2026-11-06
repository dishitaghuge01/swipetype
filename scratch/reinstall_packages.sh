#!/bin/bash
# Interactive, user-authorized Phase 4 reinstall test. No --noconfirm or dependency removal.
# Usage: bash scratch/reinstall_packages.sh CORE_ARCHIVE OVERLAY_ARCHIVE BACKUP_DIR EVIDENCE_DIR
# Requires reviewed archives, installed vendor services, swipe mode off, and the
# prepared checkout rollback script. Sudo prompts stay in the user's terminal.
set -euo pipefail
umask 077
if [ "$#" -ne 4 ]; then
    echo "Usage: $0 CORE_ARCHIVE OVERLAY_ARCHIVE BACKUP_DIR EVIDENCE_DIR" >&2
    exit 2
fi
core=$(realpath -- "$1")
overlay=$(realpath -- "$2")
backup=$(realpath -- "$3")
mkdir -p -- "$4"
evidence=$(realpath -- "$4")
test -f "$backup/rollback.sh"
bash "$backup/rollback.sh" --check
/usr/bin/python3 - "$core" "$overlay" "$evidence" <<'PY'
import sys,subprocess,json,os,socket,hashlib
from pathlib import Path
core,overlay,evidence=map(Path,sys.argv[1:])
packages={}
for name,path in [('swipetype-git',core),('swipetype-overlay-git',overlay)]:
    data=subprocess.check_output(['bsdtar','-xOf',str(path),'.PKGINFO'],text=True)
    info=dict(line.split(' = ',1) for line in data.splitlines() if ' = ' in line)
    assert info['pkgname']==name,info
    installed=subprocess.check_output(['pacman','-Q',name],text=True).split()[1]
    assert info['pkgver']==installed,(name,installed,info['pkgver'])
    packages[name]={'version':installed,'archive':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
for name in ('swipetype.service','swipetype-overlay.service'):
    fragment=subprocess.check_output(['systemctl','--user','show',name,'-p','FragmentPath','--value'],text=True).strip()
    assert fragment=='/usr/lib/systemd/user/'+name,fragment
config=Path('/usr/share/swipetype/config.yaml').read_bytes()
original=subprocess.check_output(['bsdtar','-xOf',str(core),'usr/share/swipetype/config.yaml'])
assert config==original,'Installed config was customized; preserve/review it before removal'
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
    sock.settimeout(3);sock.connect(os.environ['XDG_RUNTIME_DIR']+'/swipetype-overlay.sock');data=b''
    while b'\n' not in data:
        chunk=sock.recv(4096)
        if not chunk:raise ConnectionError('Daemon socket closed')
        data+=chunk
    assert json.loads(data.split(b'\n',1)[0])=={'event':'mode','swipe_mode':False},'Turn swipe mode OFF before this test'
layout=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'swipetype/overlay-layout.json'
snapshot={'packages':packages,'config_hex':config.hex(),'layout_path':str(layout),
          'layout_hex':layout.read_bytes().hex() if layout.exists() else None}
(evidence/'reinstall-before.json').write_text(json.dumps(snapshot,indent=2)+'\n')
print('Validated same-version archives and pristine config. Swipe mode is off.')
PY
recover() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then
        echo "Reinstall test failed/cancelled; restoring checkout services and bindings." >&2
        bash "$backup/rollback.sh" || echo "Automatic rollback failed; review $backup manually." >&2
    fi
    exit "$status"
}
trap recover EXIT
systemctl --user stop swipetype-overlay.service swipetype.service
sudo pacman -R swipetype-overlay-git swipetype-git
# Prove this was removal/reinstall, not merely installation over existing files.
test ! -e /usr/share/swipetype/daemon.py
test ! -e /usr/share/swipetype-overlay/swipetype_overlay.py
printf 'Both managed application trees were removed.\n' | tee "$evidence/reinstall-removal.txt"
sudo pacman -U "$core" "$overlay"
systemctl --user daemon-reload
systemctl --user start swipetype.service swipetype-overlay.service
/usr/bin/python3 - "$evidence" <<'PY'
from pathlib import Path
import sys,json,subprocess,time,socket,os
folder=Path(sys.argv[1]);before=json.loads((folder/'reinstall-before.json').read_text())
end=time.monotonic()+30
while True:
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(2);sock.connect(os.environ['XDG_RUNTIME_DIR']+'/swipetype-overlay.sock');data=b''
            while b'\n' not in data:
                chunk=sock.recv(4096)
                if not chunk:raise ConnectionError('Daemon socket closed')
                data+=chunk
            assert json.loads(data.split(b'\n',1)[0])=={'event':'mode','swipe_mode':False}
        break
    except OSError:
        if time.monotonic()>end:raise
        time.sleep(.2)
for name,info in before['packages'].items():
    assert subprocess.check_output(['pacman','-Q',name],text=True).split()[1]==info['version']
for name in ('swipetype.service','swipetype-overlay.service'):
    assert subprocess.check_output(['systemctl','--user','show',name,'-p','FragmentPath','--value'],text=True).strip()=='/usr/lib/systemd/user/'+name
    subprocess.run(['systemctl','--user','is-active','--quiet',name],check=True)
assert Path('/usr/share/swipetype/config.yaml').read_bytes().hex()==before['config_hex']
layout=Path(before['layout_path'])
assert (layout.read_bytes().hex() if layout.exists() else None)==before['layout_hex']
(folder/'reinstall-result.json').write_text(json.dumps({'status':'PASS','same_versions':True,
    'services_active':True,'vendor_units':True,'config_preserved':True,'layout_preserved':True},indent=2)+'\n')
print('PASS: same-version reinstall, vendor services, idle socket, config and layout preservation')
PY
trap - EXIT
