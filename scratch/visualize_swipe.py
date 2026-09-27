"""
scratch/visualize_swipe.py

Post-swipe visualization: plots the QWERTY key grid and overlays the
most recently logged word gesture's actual key-space path, so you can
see exactly where a swipe started, ended, and wandered, relative to
where it needed to go.

This is NOT a live on-screen overlay during swiping — the PRD scopes
that out of v1 (NG1). This is a quick after-the-fact debug plot: swipe
a word, toggle out of swipe mode (or leave it on), then run this
script in another terminal to see what that swipe actually looked
like.

Requires matplotlib: pip install matplotlib

Usage:
  python scratch/test_phase4.py   (in one terminal, swipe some words)
  python scratch/visualize_swipe.py   (in another, after each swipe)
"""

import json
import sys

import matplotlib.pyplot as plt

sys.path.insert(0, ".")  # allow running from repo root
from decoder.geometry import KEY_CENTERS

GESTURE_LOG_PATH = "scratch/last_swipe.json"


def main():
    try:
        with open(GESTURE_LOG_PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"No swipe logged yet at {GESTURE_LOG_PATH}. Swipe a word first.")
        return

    path = data.get("key_space_path", [])
    if not path:
        print("Logged gesture has no path data (path too short to classify).")
        return

    fig, ax = plt.subplots(figsize=(10, 4))

    # Draw the QWERTY key grid.
    for letter, (kx, ky) in KEY_CENTERS.items():
        ax.scatter(kx, ky, s=400, color="#dddddd", zorder=1)
        ax.text(kx, ky, letter, ha="center", va="center", fontsize=10, zorder=2)

    # Overlay the actual swipe path.
    xs = [p[0] for p in path]
    ys = [p[1] for p in path]
    ax.plot(xs, ys, color="blue", linewidth=1.5, zorder=3, label="actual swipe")
    ax.scatter([xs[0]], [ys[0]], color="green", s=150, zorder=4, label="start")
    ax.scatter([xs[-1]], [ys[-1]], color="red", s=150, zorder=4, label="end")

    candidates = data.get("candidates", [])
    candidates_str = ", ".join(f"{w}" for w, _ in candidates[:5])

    ax.set_title(f"Decoded: '{data.get('decoded_word')}'   top candidates: {candidates_str}")
    ax.legend(loc="upper right")
    ax.set_xlim(-1, 10.5)
    ax.set_ylim(2.5, -1)  # inverted so top row renders at the top

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()