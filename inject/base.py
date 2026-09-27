"""
inject/base.py

TextInjector interface (PRD section 7.4). Abstracts the two synthetic
input backends (xdotool for X11, ydotool for Wayland) behind one
interface so daemon.py doesn't need to know which is active — the
backend is chosen once at startup based on XDG_SESSION_TYPE.

Phase 5 bugfix: the original interface split deletes into
delete_char() (one raw Backspace) and delete_word(word) (computed
len(word)+1 internally). That let a word_delete flick recompute its
backspace count from a word length the injector had no way to know
was stale, if char_delete flicks had already trimmed that word in the
buffer without telling anyone. Collapsed to a single delete_chars(n)
primitive — daemon.py now owns the buffer-length bookkeeping (see its
_history handling) and always passes the exact count that's actually
still in the buffer, so this class no longer needs to reason about
words or spaces at all.
"""

from abc import ABC, abstractmethod


class TextInjector(ABC):
    @abstractmethod
    def commit_word(self, word: str) -> None:
        """Type `word` followed by a trailing space at the current
        cursor position, in whatever application currently has focus."""
        raise NotImplementedError

    @abstractmethod
    def delete_chars(self, count: int) -> None:
        """Send `count` Backspace key events. Caller is responsible
        for `count` being correct — this class does no bookkeeping."""
        raise NotImplementedError