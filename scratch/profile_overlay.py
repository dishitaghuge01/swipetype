#!/usr/bin/env python3
"""Replay socket points to measure an overlay, without grabbing/typing/clicking.

.venv/bin/python scratch/profile_overlay.py --seconds 10
.venv/bin/python scratch/profile_overlay.py --seconds 10 --source-ref 1bb12ef

Uses private sockets/config/state; launches a click-through overlay. Do not run
alongside another overlay with the same GApplication ID. CPU is percent of one
logical CPU (100% = one CPU-second per second), not whole-machine percent.
--source-ref extracts the requested local Git revision into a temporary directory;
it does not alter the checkout. Results/logs are retained in the printed directory.
"""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decoder.geometry import KEY_CENTERS
from decoder.resample import resample_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--source-ref', help='Local Git revision, otherwise current working tree')
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    spec = importlib.util.spec_from_file_location('measure', ROOT / 'scratch/measure_overlay.py')
    measure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measure)
    directory = Path(tempfile.mkdtemp(prefix='swipetype-profile-'))
    print(f'Evidence directory: {directory}', flush=True)
    source = ROOT
    if args.source_ref:
        archive = subprocess.check_output(['git', 'archive', args.source_ref, '--',
            'config.py', 'decoder/geometry.py', 'overlay-app'], cwd=ROOT)
        source = directory / 'source'
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(source, filter='data')
    env = os.environ.copy()
    runtime = env.get('XDG_RUNTIME_DIR', '/tmp')
    wayland = env.get('WAYLAND_DISPLAY', 'wayland-0')
    env['WAYLAND_DISPLAY'] = wayland if wayland.startswith('/') else runtime + '/' + wayland
    env['XDG_RUNTIME_DIR'] = str(directory)
    env['XDG_STATE_HOME'] = str(directory / 'state')
    env.pop('SWIPETYPE_OVERLAY_DEV', None)
    config_path = directory / 'config.yaml'
    config_path.write_text('ghost_overlay:\n  enabled: true\n')
    points = resample_path([KEY_CENTERS[c.upper()] for c in 'helo'], 70)
    process = None

    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)

    def summarize(before, after, elapsed):
        processes = {str(pid): {
            'name': value[0],
            'cpu_percent_one_core': round((value[1] - before.get(pid, value)[1])
                                         / measure.CLK_TCK / elapsed * 100, 2),
            'memory_mib': round(value[2] / 1024, 2), 'memory_kind': value[3],
        } for pid, value in after.items()}
        cpu = sum(p['cpu_percent_one_core'] for p in processes.values())
        return {'seconds': elapsed, 'processes': processes,
                'cpu_percent_one_core': round(cpu, 2),
                'cpu_percent_normalized_logical_cpus': round(cpu / os.cpu_count(), 2),
                'memory_mib': round(sum(p['memory_mib'] for p in processes.values()), 2)}

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(directory / 'swipetype-overlay.sock'))
            server.listen(1)
            server.settimeout(30)
            bootstrap = (
                'import runpy,sys;sys.path.insert(0,' + repr(str(source)) + ');'
                'import config;original=config.load_config;config.load_config=lambda:original('
                + repr(str(config_path)) + ');runpy.run_path(sys.argv[1],run_name="__main__")')
            with open(directory / 'overlay.log', 'w') as log:
                process = subprocess.Popen([sys.executable, '-c', bootstrap,
                    str(source / 'overlay-app/swipetype_overlay.py')],
                    cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
            conn, _ = server.accept()
            with conn:
                def send(event):
                    conn.sendall((json.dumps(event) + '\n').encode())
                send({'event': 'mode', 'swipe_mode': True})
                time.sleep(5)  # cold-start not included in steady-state cost
                before = measure.sample(process.pid)
                start = time.monotonic()
                time.sleep(args.seconds)
                after = measure.sample(process.pid)
                idle = summarize(before, after, time.monotonic() - start)
                before = measure.sample(process.pid)
                start = time.monotonic()
                count = 0
                while time.monotonic() - start < args.seconds:
                    if count % len(points) == 0:
                        send({'event': 'clear'})
                    x, y = points[count % len(points)]
                    send({'event': 'point', 'x': x, 'y': y})
                    count += 1
                    time.sleep(.008)
                after = measure.sample(process.pid)
                active = summarize(before, after, time.monotonic() - start)
                result = {
                    'source': args.source_ref or 'working tree',
                    'logical_cpus': os.cpu_count(), 'idle': idle, 'active': active,
                    'points_sent': count, 'dev_mode': False,
                    'scope': 'Overlay process tree only; excludes compositor/daemon/GPU power',
                }
                (directory / 'results.json').write_text(json.dumps(result, indent=2))
                print(json.dumps(result), flush=True)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == '__main__':
    sys.exit(main())
