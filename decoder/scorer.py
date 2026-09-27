"""
decoder/scorer.py

SHARK2-style shape-matching scorer (PRD section 7.5): pruning by
start/end proximity, then shape_score + location_score + frequency_score
combined into a single ranking. Lowest combined_score wins.
"""

import math
from typing import Dict, List, Tuple

from decoder.geometry import KEY_CENTERS

PRUNE_RADIUS = 1.5  # key-units, config default
ALPHA = 1.0          # shape_score weight
BETA = 0.4           # location_score weight
GAMMA = 0.15         # frequency_score weight


def _euclidean(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5


def _prune_candidates(
    dictionary: Dict[str, dict],
    gesture_start: Tuple[float, float],
    gesture_end: Tuple[float, float],
    prune_radius: float = PRUNE_RADIUS,
) -> List[str]:
    candidates = []
    for word, entry in dictionary.items():
        first_key_center = KEY_CENTERS[entry["first_letter"]]
        last_key_center = KEY_CENTERS[entry["last_letter"]]

        if _euclidean(gesture_start, first_key_center) > prune_radius:
            continue
        if _euclidean(gesture_end, last_key_center) > prune_radius:
            continue

        candidates.append(word)
    return candidates


def score_word(
    gesture_resampled: List[Tuple[float, float]],
    gesture_start: Tuple[float, float],
    gesture_end: Tuple[float, float],
    word_entry: dict,
    alpha: float = ALPHA,
    beta: float = BETA,
    gamma: float = GAMMA,
) -> float:
    ideal_path = word_entry["ideal_path"]
    n = len(gesture_resampled)

    shape_score = sum(
        _euclidean(gesture_resampled[i], ideal_path[i]) for i in range(n)
    ) / n

    first_key_center = KEY_CENTERS[word_entry["first_letter"]]
    last_key_center = KEY_CENTERS[word_entry["last_letter"]]
    location_score = (
        _euclidean(gesture_start, first_key_center)
        + _euclidean(gesture_end, last_key_center)
    )

    frequency_score = -math.log(word_entry["frequency"] + 1e-9)

    return alpha * shape_score + beta * location_score + gamma * frequency_score


def decode(
    gesture_resampled: List[Tuple[float, float]],
    dictionary: Dict[str, dict],
    top_k: int = 1,
    prune_radius: float = PRUNE_RADIUS,
    alpha: float = ALPHA,
    beta: float = BETA,
    gamma: float = GAMMA,
) -> List[Tuple[str, float]]:
    """
    Returns up to top_k (word, combined_score) pairs, sorted ascending
    (best match first). Empty list if no candidates survive pruning.
    """
    if not gesture_resampled:
        return []

    gesture_start = gesture_resampled[0]
    gesture_end = gesture_resampled[-1]

    candidate_words = _prune_candidates(dictionary, gesture_start, gesture_end, prune_radius)

    scored = [
        (word, score_word(gesture_resampled, gesture_start, gesture_end, dictionary[word], alpha, beta, gamma))
        for word in candidate_words
    ]
    scored.sort(key=lambda pair: pair[1])

    return scored[:top_k]