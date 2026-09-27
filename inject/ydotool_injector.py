"""
inject/ydotool_injector.py

Wayland backend for TextInjector (PRD section 7.4), using the
`ydotool` CLI via subprocess against numeric Linux keycodes — the
stable low-level interface across ydotool versions, rather than
relying on symbolic key names, which vary between versions.

Requires `ydotool` installed (Arch: sudo pacman -S ydotool, or AUR)
and the `ydotoold` background service running, with the invoking user
able to access /dev/uinput (see PRD section 5 / Phase 0 setup).
"""

import subprocess

from inject.base import TextInjector

# KEY_BACKSPACE from linux/input-event-codes.h
BACKSPACE_KEYCODE = 14


def _press_release_pairs(keycode: int, count: int) -> list[str]:
    pairs: list[str] = []
    for _ in range(count):
        pairs.append(f"{keycode}:1")
        pairs.append(f"{keycode}:0")
    return pairs


class YdotoolInjector(TextInjector):
    def commit_word(self, word: str) -> None:
        # Note: unlike xdotool, ydotool's `type` command has no
        # documented "--" end-of-options marker, so it's deliberately
        # not used here. Safe regardless, since `word` always comes
        # from the dictionary and never starts with a dash.
        subprocess.run(["ydotool", "type", word + " "], check=False)

    def delete_chars(self, count: int) -> None:
        if count <= 0:
            return
        args = ["ydotool", "key"] + _press_release_pairs(BACKSPACE_KEYCODE, count)
        subprocess.run(args, check=False)