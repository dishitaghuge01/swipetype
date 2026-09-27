"""
decoder/dictionary.py

Dictionary loading and ideal-path precomputation (PRD section 7.6).
Uses the wordfreq package for English word frequencies. Only
alphabetic-only words are kept for v1 (strips anything with
apostrophes/hyphens per PRD simplicity note).
"""

from typing import Dict

import wordfreq

from decoder.geometry import KEY_CENTERS
from decoder.resample import resample_path, N_RESAMPLE

DICT_SIZE = 30000  # config default, tune down if decode latency is too high


def _collapse_repeated_letters(word: str) -> str:
    """'hello' -> 'helo' — a swipe doesn't produce a distinguishable
    double-hit on one key, so consecutive duplicate letters collapse."""
    collapsed = []
    prev = None
    for ch in word:
        if ch != prev:
            collapsed.append(ch)
        prev = ch
    return "".join(collapsed)


def load_dictionary(dict_size: int = DICT_SIZE, n_resample: int = N_RESAMPLE) -> Dict[str, dict]:
    """
    Returns dict[word] -> {
        "ideal_path": list of n_resample (x, y) key-space points,
        "frequency": wordfreq frequency for the word,
        "first_letter": first letter of the collapsed sequence (uppercase),
        "last_letter": last letter of the collapsed sequence (uppercase),
    }
    """
    words = wordfreq.top_n_list("en", dict_size)

    dictionary: Dict[str, dict] = {}

    for word in words:
        # str.isalpha() alone accepts any unicode letter (e.g. the "é"
        # in "café"), but KEY_CENTERS only covers ASCII A-Z. Require
        # both, or a non-ASCII letter can slip through as first/last
        # letter and KeyError in the scorer's KEY_CENTERS lookup.
        if not (word.isalpha() and word.isascii()):
            continue

        lower_word = word.lower()
        collapsed = _collapse_repeated_letters(lower_word)

        key_points = [KEY_CENTERS[ch.upper()] for ch in collapsed if ch.upper() in KEY_CENTERS]
        if not key_points:
            continue

        ideal_path = resample_path(key_points, n_resample)
        frequency = wordfreq.word_frequency(lower_word, "en")

        dictionary[lower_word] = {
            "ideal_path": ideal_path,
            "frequency": frequency,
            "first_letter": collapsed[0].upper(),
            "last_letter": collapsed[-1].upper(),
        }

    return dictionary