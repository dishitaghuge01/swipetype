"""
inject/xdotool_injector.py

X11 backend for TextInjector (PRD section 7.4), using the `xdotool`
CLI via subprocess.

Requires `xdotool` installed:  sudo pacman -S xdotool
"""

import subprocess

from inject.base import TextInjector


class XdotoolInjector(TextInjector):
    def commit_word(self, word: str) -> None:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--", word + " "],
            check=False,
        )

    def delete_chars(self, count: int) -> None:
        if count <= 0:
            return
        subprocess.run(
            ["xdotool", "key", "--repeat", str(count), "BackSpace"],
            check=False,
        )