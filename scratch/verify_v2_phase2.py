#!/usr/bin/env python3
"""Opt-in Hyprland integration checks using disposable GUI/input processes.

Run from the checkout: .venv/bin/python scratch/verify_v2_phase2.py
Requires an unlocked Hyprland 0.57+ (Lua dispatch) session, uinput access,
ydotoold, and overlay deps. Exits nonzero on observed failures/errors.
Stop swipetype-overlay.service and any manual overlay first, then restart it
afterwards: private test sockets do not isolate GApplication's session-bus ID.
DO NOT interact with the desktop while running: synthetic input targets a test
editor. Focus is checked before gestures/clicks; use only in a disposable session.
Uses private sockets/config/state; never restarts the user's normal service.
Results and process logs are retained in a printed temporary directory.
No production methods are replaced: config lookup is redirected, and an overlay
subclass adds test-only DOM observation/JavaScript commands.
"""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def store(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def wait_for(fn, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.1)
    raise TimeoutError('condition not reached')


def redirect_config():
    import config
    original = config.load_config
    config.load_config = lambda: original(os.environ['SWIPETYPE_TEST_CONFIG'])


def editor_role():
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, GLib
    app = Gtk.Application(application_id='dev.swipetype.Phase2TestEditor')

    def activate(app):
        window = Gtk.ApplicationWindow(application=app, title='SwipeType disposable test editor')
        window.set_default_size(900, 600)
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_top_margin(40)
        view.set_left_margin(40)
        window.set_child(view)
        clicks = [0]

        def snapshot(*_):
            buf = view.get_buffer()
            store(os.environ['SWIPETYPE_TEST_EDITOR_STATE'], {
                'text': buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True),
                'clicks': clicks[0], 'active': window.is_active(),
                'focus': view.has_focus(), 'pid': os.getpid(),
            })
            return True

        def clicked(*_):
            clicks[0] += 1
            snapshot()

        gesture = Gtk.GestureClick()
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect('pressed', clicked)
        view.add_controller(gesture)
        GLib.timeout_add(100, snapshot)
        window.present()
        view.grab_focus()
    app.connect('activate', activate)
    app.run([])


def overlay_role():
    redirect_config()
    module = runpy.run_path(str(ROOT / 'overlay-app/swipetype_overlay.py'), run_name='phase2_overlay')
    from gi.repository import GLib
    Base = module['OverlayApp']

    class ObservedOverlay(Base):
        def __init__(self):
            super().__init__()
            self.loads = 0
            self.last_command = 0
            self.dom = None
            self.js_error = None
            GLib.timeout_add(200, self.observe)

        def _on_load_changed(self, webview, event):
            super()._on_load_changed(webview, event)
            if event == module['WebKit'].LoadEvent.FINISHED:
                self.loads += 1
                webview.evaluate_javascript('''(()=>{
                    window.__phase2Points=0; window.__phase2Strokes=0;
                    const dispatch=window.swipetype.events;
                    window.swipetype.events=function(batch){
                        window.__phase2Points+=batch.filter(e=>e.event==='point').length;
                        return dispatch(batch);
                    };
                    const ctx=document.querySelector('#comet').getContext('2d');
                    const stroke=ctx.stroke;
                    ctx.stroke=function(){window.__phase2Strokes++;return stroke.apply(this,arguments)};
                })()''', -1, None, None, None, None)

        def evaluated(self, webview, result, *_):
            try:
                self.dom = json.loads(webview.evaluate_javascript_finish(result).to_json(0))
            except Exception as exc:
                self.js_error = str(exc)

        def observe(self):
            command = load(os.environ['SWIPETYPE_TEST_COMMAND'])
            if self.webview is not None and self._page_ready:
                if command.get('seq', 0) > self.last_command:
                    self.last_command = command['seq']
                    self.webview.evaluate_javascript(command['js'], -1, None, None, None, None)
                js = '''JSON.stringify({mode:document.body.dataset.mode,
                    opacity:document.body.style.opacity,
                    points:window.__phase2Points, strokes:window.__phase2Strokes,
                    keys:document.querySelectorAll('.gk-key').length,
                    rect:(()=>{let r=document.querySelector('#stage').getBoundingClientRect();
                        return {x:r.x,y:r.y,w:r.width,h:r.height,vw:innerWidth,vh:innerHeight}})()})'''
                self.webview.evaluate_javascript(js, -1, None, None, None, self.evaluated)
            store(os.environ['SWIPETYPE_TEST_OVERLAY_STATE'], {
                'mode': self.mode, 'ready': self._page_ready,
                'window': self.window is not None, 'loads': self.loads,
                'layout': self.current_layout, 'dom': self.dom,
                'command': self.last_command, 'js_error': self.js_error,
                'pid': os.getpid(),
            })
            return True
    app = ObservedOverlay()
    app.run([])


def runner():
    from evdev import UInput, AbsInfo, ecodes as E
    from decoder.geometry import KEY_CENTERS
    from decoder.resample import resample_path
    spec = importlib.util.spec_from_file_location('measure_overlay', ROOT / 'scratch/measure_overlay.py')
    measure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measure)
    directory = Path(tempfile.mkdtemp(prefix='swipetype-phase2-'))
    os.chmod(directory, 0o700)
    (directory / 'runtime').mkdir(mode=0o700)
    env = os.environ.copy()
    runtime = env.get('XDG_RUNTIME_DIR', '/tmp')
    wayland = env.get('WAYLAND_DISPLAY', 'wayland-0')
    env.update({
        'XDG_RUNTIME_DIR': str(directory / 'runtime'),
        'YDOTOOL_SOCKET': env.get('YDOTOOL_SOCKET', runtime + '/.ydotool_socket'),
        'WAYLAND_DISPLAY': wayland if wayland.startswith('/') else runtime + '/' + wayland,
        'XDG_STATE_HOME': str(directory / 'state'),
        'SWIPETYPE_TEST_CONFIG': str(directory / 'config.yaml'),
        'SWIPETYPE_TEST_EDITOR_STATE': str(directory / 'editor.json'),
        'SWIPETYPE_TEST_OVERLAY_STATE': str(directory / 'overlay.json'),
        'SWIPETYPE_TEST_COMMAND': str(directory / 'command.json'),
        'SWIPETYPE_OVERLAY_DEV': '1', 'PYTHONUNBUFFERED': '1',
    })
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Received signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    processes = []
    results = []
    ui = None
    daemon = overlay = editor = None
    original_window = json.loads(subprocess.check_output(['hyprctl', '-j', 'activewindow']))
    previous_address = original_window.get('address')
    print(f'Evidence directory: {directory}', flush=True)

    def record(name, status, **details):
        result = dict(test=name, status=status, **details)
        results.append(result)
        store(directory / 'results.json', results)
        print(json.dumps(result), flush=True)

    def launch(role, suffix=''):
        if role == 'overlay':
            (directory / 'command.json').unlink(missing_ok=True)
        log = open(directory / f'{role}{suffix}.log', 'w')
        args = [sys.executable, str(Path(__file__).resolve()), role]
        if role == 'overlay':
            args.append(str(ROOT / 'overlay-app/swipetype_overlay.py'))
        proc = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        log.close()
        processes.append(proc)
        return proc

    def stop(proc):
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def state():
        return load(directory / 'overlay.json')

    def dom():
        value = state().get('dom')
        return json.loads(value) if isinstance(value, str) else (value or {})

    def ready():
        return state().get('ready') and dom().get('keys') == 26

    def config(enabled):
        (directory / 'config.yaml').write_text(
            f'device_path: {ui.device.path}\nghost_overlay:\n  enabled: {str(enabled).lower()}\n')

    def toggle():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.connect(str(directory / 'runtime/swipetype.sock'))
            conn.sendall(b'TOGGLE')
        time.sleep(.4)

    def focus():
        wait_for(lambda: any(w.get('pid') == editor.pid for w in json.loads(
            subprocess.check_output(['hyprctl', '-j', 'clients']))), 20)
        subprocess.run(['hyprctl', 'dispatch', f'hl.dsp.focus({{window="pid:{editor.pid}"}})'], check=True,
                       stdout=subprocess.DEVNULL)
        time.sleep(.25)
        guard()

    def guard():
        active = json.loads(subprocess.check_output(['hyprctl', '-j', 'activewindow']))
        if active.get('pid') != editor.pid:
            raise RuntimeError('Focus left disposable editor; refusing synthetic input')

    def text():
        return load(directory / 'editor.json').get('text', '')

    def contacts(points, fingers=1, interval=.008):
        guard()
        for i, (kx, ky) in enumerate(points):
            x = round((.05 + kx / 9.75 * .9) * 10000)
            y = round((.05 + ky / 2 * .9) * 10000)
            for slot in range(fingers):
                ui.write(E.EV_ABS, E.ABS_MT_SLOT, slot)
                if i == 0:
                    ui.write(E.EV_ABS, E.ABS_MT_TRACKING_ID, slot + 10)
                ui.write(E.EV_ABS, E.ABS_MT_POSITION_X, x)
                ui.write(E.EV_ABS, E.ABS_MT_POSITION_Y, y + slot * 80)
            ui.write(E.EV_KEY, E.BTN_TOUCH, 1)
            ui.syn()
            time.sleep(interval)
        guard()
        for slot in range(fingers):
            ui.write(E.EV_ABS, E.ABS_MT_SLOT, slot)
            ui.write(E.EV_ABS, E.ABS_MT_TRACKING_ID, -1)
        ui.write(E.EV_KEY, E.BTN_TOUCH, 0)
        ui.syn()

    def word(w, pause=.8):
        pts = [KEY_CENTERS[c.upper()] for i, c in enumerate(w) if i == 0 or c != w[i-1]]
        contacts(resample_path(pts, 70))
        time.sleep(pause)

    seq = [0]
    def command(js):
        seq[0] += 1
        store(directory / 'command.json', {'seq': seq[0], 'js': js})
        wait_for(lambda: state().get('command') == seq[0])
        time.sleep(.5)

    def web_pids():
        return [pid for pid in measure.descendants(overlay.pid)
                if (measure.read_stat(pid) or ('',))[0].startswith('WebKitWebProces')]

    def restart_overlay(suffix):
        nonlocal overlay
        stop(overlay)
        (directory / 'overlay.json').unlink(missing_ok=True)
        overlay = launch('overlay', suffix)
        wait_for(ready, 30)
        time.sleep(.5)

    try:
        axes = lambda max_: AbsInfo(0, 0, max_, 0, 0, 10)
        caps = {
            E.EV_KEY: [E.BTN_TOUCH, E.BTN_TOOL_FINGER, E.BTN_LEFT],
            E.EV_ABS: [(E.ABS_X, axes(10000)), (E.ABS_Y, axes(10000)),
                       (E.ABS_MT_SLOT, axes(4)), (E.ABS_MT_TRACKING_ID, axes(65535)),
                       (E.ABS_MT_POSITION_X, axes(10000)), (E.ABS_MT_POSITION_Y, axes(10000))],
        }
        ui = UInput(caps, name='SwipeType isolated acceptance touchpad',
                    input_props=[E.INPUT_PROP_POINTER, E.INPUT_PROP_BUTTONPAD])
        config(True)
        store(directory / 'environment.json', {
            'session': os.environ.get('XDG_SESSION_TYPE'),
            'desktop': os.environ.get('XDG_CURRENT_DESKTOP'),
            'virtual_touchpad': ui.device.path, 'python': sys.version,
            'note': 'Synthetic gestures; separate daemon; temporary config/runtime/layout. DEV diagnostics enabled.',
        })
        editor = launch('editor')
        wait_for(lambda: load(directory / 'editor.json').get('pid') == editor.pid)
        focus()
        daemon = launch('daemon')
        wait_for(lambda: (directory / 'runtime/swipetype.sock').exists(), 60)
        overlay = launch('overlay')
        wait_for(ready, 30)
        time.sleep(2)
        toggle()
        wait_for(lambda: dom().get('mode') == 'swipe')
        focus()
        word('hello')
        word('world')
        expected = 'hello world '
        record('words_with_overlay', 'PASS' if text() == expected else 'FAIL',
               expected=expected, actual=text(), overlay=dom())
        record('live_trace_rendering',
               'PASS' if dom().get('points', 0) > 50 and dom().get('strokes', 0) > 0 else 'FAIL',
               points=dom().get('points'), strokes=dom().get('strokes'))
        contacts([(5 - i * .18, 1) for i in range(12)], interval=.005)
        time.sleep(.7)
        record('one_finger_character_delete', 'PASS' if text() == 'hello world' else 'FAIL', actual=text())
        contacts([(5 - i * .18, 1) for i in range(12)], fingers=2, interval=.005)
        time.sleep(.7)
        record('two_finger_word_delete_after_character_delete',
               'PASS' if text() == 'hello ' else 'FAIL', actual=text())
        record('focus_during_typing', 'PASS' if load(directory / 'editor.json').get('focus') else 'FAIL',
               editor=load(directory / 'editor.json'))

        active = json.loads(subprocess.check_output(['hyprctl', '-j', 'activewindow']))
        x = active['at'][0] + active['size'][0] // 2
        y = active['at'][1] + active['size'][1] // 2
        # Stage center is deliberately over the editor; a real pointer click
        # tests the compositor input region rather than DOM synthetic clicks.
        old_clicks = load(directory / 'editor.json').get('clicks', 0)
        subprocess.run(['hyprctl', 'dispatch', f'hl.dsp.cursor.move({{x={x},y={y}}})'], check=True, stdout=subprocess.DEVNULL)
        guard()
        subprocess.run(['ydotool', 'click', '0xC0'], check=True, stdout=subprocess.DEVNULL)
        time.sleep(.6)
        clicked = load(directory / 'editor.json')
        record('click_through', 'PASS' if clicked.get('clicks', 0) > old_clicks and clicked.get('focus') else 'FAIL',
               before_clicks=old_clicks, editor=clicked)
        # Click may move caret: explicitly place it at end via a real key chord.
        guard()
        subprocess.run(['ydotool', 'key', '29:1', '107:1', '107:0', '29:0'], check=True)
        time.sleep(.2)

        subprocess.run(['gapplication', 'action', 'dev.dishitaghuge.swipetype.overlay',
                        'toggle-overlay'], check=True)
        wait_for(lambda: dom().get('mode') == 'idle')
        before = text()
        hidden_points = dom().get('points')
        hidden_strokes = dom().get('strokes')
        word('hello')
        record('hidden_overlay_does_not_render',
               'PASS' if text() == before + 'hello ' and dom().get('points') == hidden_points
               and dom().get('strokes') == hidden_strokes else 'FAIL',
               before=before, actual=text(), points=dom().get('points'), strokes=dom().get('strokes'))
        subprocess.run(['gapplication', 'action', 'dev.dishitaghuge.swipetype.overlay',
                        'toggle-overlay'], check=True)
        wait_for(lambda: dom().get('mode') == 'swipe')

        stop(overlay)
        before = text()
        word('test')
        record('typing_after_overlay_exit', 'PASS' if text() == before + 'test ' else 'FAIL',
               before=before, actual=text(), daemon_alive=daemon.poll() is None)
        restart_overlay('-late')
        observed = dom().get('mode')
        record('overlay_connects_during_swipe_mode', 'PASS' if observed == 'swipe' else 'FAIL',
               expected='swipe', actual=observed)
        # Work around missing initial snapshot only to continue independent tests.
        toggle()
        toggle()
        wait_for(lambda: dom().get('mode') == 'swipe')
        pids = web_pids()
        if not pids:
            raise RuntimeError('Could not identify test overlay WebKit web process')
        prior_loads = state()['loads']
        for pid in pids:
            os.kill(pid, signal.SIGKILL)
        before = text()
        word('hello')
        recovered = False
        try:
            wait_for(lambda: state().get('loads', 0) > prior_loads and ready(), 30)
            recovered = True
        except TimeoutError:
            pass
        record('webkit_crash_recovery', 'PASS' if recovered and text() == before + 'hello '
               and dom().get('points', 0) > 0 and dom().get('strokes', 0) > 0 else 'FAIL',
               killed=pids, replacement=web_pids(), loads=state().get('loads'),
               trace=dom(),
               daemon_alive=daemon.poll() is None, before=before, actual=text())

        stop(daemon)
        # Production overlay does not reset mode on EOF. Capture it as evidence.
        time.sleep(3)
        record('daemon_disconnect_visual_state',
               'PASS' if state().get('mode') == 'idle' else 'FAIL',
               expected='idle', overlay_mode=state().get('mode'))
        daemon = launch('daemon', '-restart')
        wait_for(lambda: (directory / 'runtime/swipetype.sock').exists(), 60)
        time.sleep(3)
        record('daemon_restart_initial_mode',
               'PASS' if state().get('mode') == 'idle' else 'FAIL',
               expected='idle', actual=state().get('mode'))
        before = text()
        toggle()
        wait_for(lambda: dom().get('mode') == 'swipe')
        word('world')
        record('daemon_restart_reconnect', 'PASS' if text() == before + 'world ' else 'FAIL',
               actual=text(), before=before, overlay_pid=overlay.pid, overlay=dom())

        # Measure process tree with the requested script (DEV diagnostics add
        # overhead). Then repeat clean production overlay below for final numbers.
        toggle()
        command('window.webkit.messageHandlers.swipetype.postMessage({type:"layout",layout:{cx:.42,cy:.44,w:.65}})')
        layout_path = directory / 'state/swipetype/overlay-layout.json'
        wait_for(lambda: load(layout_path).get('w') == .65)
        restart_overlay('-saved-layout')
        saved = state().get('layout')
        rectangle = dom().get('rect')
        record('layout_survives_restart', 'PASS' if saved == {'cx': .42, 'cy': .44, 'w': .65} else 'FAIL',
               saved_file=load(layout_path), restored=saved, rendered_rect=rectangle,
               method='Real JS bridge layout message; physical drag not tested')
        stop(overlay)
        layout_path.write_text('{invalid json')
        (directory / 'overlay.json').unlink(missing_ok=True)
        overlay = launch('overlay', '-corrupt-layout')
        wait_for(ready, 30)
        record('corrupt_layout_fallback', 'PASS' if state().get('layout') is None else 'FAIL',
               restored=state().get('layout'), rendered_rect=dom().get('rect'))
        stop(overlay)
        config(False)
        (directory / 'overlay.json').unlink(missing_ok=True)
        overlay = launch('overlay', '-disabled')
        wait_for(lambda: state().get('pid') == overlay.pid)
        time.sleep(3)
        descendants = measure.descendants(overlay.pid)
        helpers = [(pid, measure.read_stat(pid)[0]) for pid in descendants if measure.read_stat(pid)]
        record('disabled_overlay_no_webkit_helpers',
               'PASS' if not state().get('window') and len(descendants) == 1 else 'FAIL',
               window=state().get('window'), processes=helpers)
        before = text()
        toggle()
        word('test')
        record('typing_with_disabled_overlay', 'PASS' if text() == before + 'test ' and not state().get('window') else 'FAIL',
               before=before, actual=text(), window=state().get('window'))
        toggle()
        stop(overlay)
        config(True)
        layout_path.unlink(missing_ok=True)
        # Uninstrumented production host for performance figures.
        perf_env = env.copy()
        perf_env.pop('SWIPETYPE_OVERLAY_DEV', None)
        log = open(directory / 'overlay-performance.log', 'w')
        bootstrap = ('import sys,runpy;sys.path.insert(0,' + repr(str(ROOT)) + ');'
                     'import config;original=config.load_config;'
                     'config.load_config=lambda:original(' + repr(str(directory / 'config.yaml')) + ');'
                     'runpy.run_path(sys.argv[1],run_name="__main__")')
        overlay = subprocess.Popen([sys.executable, '-c', bootstrap,
                                    str(ROOT / 'overlay-app/swipetype_overlay.py')],
                                   cwd=ROOT, env=perf_env, stdout=log, stderr=subprocess.STDOUT)
        log.close()
        processes.append(overlay)
        time.sleep(5)
        toggle()
        focus()
        time.sleep(2)
        for label in ('idle', 'active'):
            output_path = directory / f'measure-{label}.txt'
            with open(output_path, 'w') as output:
                sampler = subprocess.Popen([sys.executable, str(ROOT / 'scratch/measure_overlay.py'), '10'],
                                           stdout=output, stderr=subprocess.STDOUT)
                if label == 'active':
                    end = time.monotonic() + 10
                    count = 0
                    while time.monotonic() < end:
                        word('hello', pause=.03)
                        count += 1
                sampler.wait(timeout=15)
            record(f'performance_{label}', 'MEASURED', output=output_path.read_text(),
                   synthetic_gestures=count if label == 'active' else 0,
                   dev_mode=False, overlay_pid=overlay.pid)
        toggle()
    except Exception as exc:
        import traceback
        record('runner_error', 'ERROR', error=str(exc), traceback=traceback.format_exc())
    finally:
        # Stop isolated daemon before removing virtual device; process teardown
        # releases exclusive grab. Never touch normal service or its sockets.
        for proc in reversed(processes):
            stop(proc)
        if ui is not None:
            ui.close()
        if previous_address:
            subprocess.run(['hyprctl', 'dispatch', f'hl.dsp.focus({{window="address:{previous_address}"}})'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f'Results retained in {directory}', flush=True)
    return 1 if any(r['status'] in ('FAIL', 'ERROR') for r in results) else 0


if __name__ == '__main__':
    role = sys.argv[1] if len(sys.argv) > 1 else 'runner'
    if role == 'editor':
        editor_role()
    elif role == 'overlay':
        overlay_role()
    elif role == 'daemon':
        redirect_config()
        runpy.run_path(str(ROOT / 'daemon.py'), run_name='__main__')
    else:
        sys.exit(runner())
