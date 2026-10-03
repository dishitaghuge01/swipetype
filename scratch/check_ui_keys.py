#!/usr/bin/env python3
"""
scratch/check_ui_keys.py

Drift guard (PRD v2 rev2 build plan, stage 2b/2h): asserts that
overlay-app/ui/demo/keys.js's key-center map is byte-for-byte identical
to decoder.geometry.KEY_CENTERS, so the browser-only demo preview never
silently drifts out of sync with the real decoder geometry the
production overlay is fed from Python (PRD section 8.6's init() call,
"straight from decoder.geometry.KEY_CENTERS").

Run: python scratch/check_ui_keys.py
Exits non-zero (and prints exactly what differs) on any mismatch.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from decoder.geometry import KEY_CENTERS

KEYS_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "overlay-app", "ui", "demo", "keys.js",
)


def load_js_keys(path: str) -> dict:
    with open(path, "r") as f:
        text = f.read()
    match = re.search(r"window\.SWIPETYPE_DEMO_KEYS\s*=\s*(\{.*?\});", text, re.DOTALL)
    if not match:
        print(f"FAIL: could not find 'window.SWIPETYPE_DEMO_KEYS = {{...}};' in {path}")
        sys.exit(1)
    return json.loads(match.group(1))


def main():
    js_keys = load_js_keys(KEYS_JS_PATH)
    py_keys = {k: list(v) for k, v in KEY_CENTERS.items()}

    js_letters = set(js_keys.keys())
    py_letters = set(py_keys.keys())
    ok = True

    if js_letters != py_letters:
        ok = False
        print("FAIL: letter sets differ.")
        print(f"  only in JS: {sorted(js_letters - py_letters)}")
        print(f"  only in Python: {sorted(py_letters - js_letters)}")

    for letter in sorted(js_letters & py_letters):
        if list(js_keys[letter]) != list(py_keys[letter]):
            ok = False
            print(f"FAIL: '{letter}' differs -- JS: {js_keys[letter]}  Python: {py_keys[letter]}")

    if ok:
        print(f"OK: all {len(py_letters)} keys in demo/keys.js match decoder.geometry.KEY_CENTERS exactly.")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()