"""
decoder/geometry.py

Keyboard geometry for the decoder engine (PRD section 7.5).

Pulled forward from Phase 4: gesture/segmenter.py (Phase 3) needs
to_key_space() to classify flick vs word gestures in key-space units,
per section 7.7. The rest of the decoder (resampling, dictionary,
scoring) still lands in Phase 4 untouched.
"""

from typing import Dict, Tuple

# Normalized key-unit coordinates. Top row y=0, home row y=1, bottom
# row y=2, standard QWERTY stagger (PRD section 7.5).
KEY_CENTERS: Dict[str, Tuple[float, float]] = {
    "Q": (0.0, 0), "W": (1.0, 0), "E": (2.0, 0), "R": (3.0, 0), "T": (4.0, 0),
    "Y": (5.0, 0), "U": (6.0, 0), "I": (7.0, 0), "O": (8.0, 0), "P": (9.0, 0),

    "A": (0.25, 1), "S": (1.25, 1), "D": (2.25, 1), "F": (3.25, 1), "G": (4.25, 1),
    "H": (5.25, 1), "J": (6.25, 1), "K": (7.25, 1), "L": (8.25, 1),

    "Z": (0.75, 2), "X": (1.75, 2), "C": (2.75, 2), "V": (3.75, 2), "B": (4.75, 2),
    "N": (5.75, 2), "M": (6.75, 2),
}

# Key-grid extents (PRD section 7.5): x range 0-9.75, y range 0-2
KEY_SPACE_X_MIN = 0.0
KEY_SPACE_X_MAX = 9.75
KEY_SPACE_Y_MIN = 0.0
KEY_SPACE_Y_MAX = 2.0

DEFAULT_MARGIN = 0.05  # config default per PRD section 7.5


def to_key_space(x_norm: float, y_norm: float, margin: float = DEFAULT_MARGIN) -> Tuple[float, float]:
    """
    Affine-map normalized 0-1 touchpad coordinates into key-grid space
    (x: 0-9.75, y: 0-2), with a small margin carved off each edge so
    edge keys (Q, P, Z, M) are reachable without needing to hit the
    physical trackpad's physical edges.

    Input is clamped into the usable range first, so a touch that lands
    inside the margin maps to the nearest valid key-space position
    instead of extrapolating past the key grid.
    """
    usable_min = margin
    usable_max = 1.0 - margin
    usable_range = usable_max - usable_min

    x_clamped = min(max(x_norm, usable_min), usable_max)
    y_clamped = min(max(y_norm, usable_min), usable_max)

    x_frac = (x_clamped - usable_min) / usable_range if usable_range else 0.0
    y_frac = (y_clamped - usable_min) / usable_range if usable_range else 0.0

    kx = KEY_SPACE_X_MIN + x_frac * (KEY_SPACE_X_MAX - KEY_SPACE_X_MIN)
    ky = KEY_SPACE_Y_MIN + y_frac * (KEY_SPACE_Y_MAX - KEY_SPACE_Y_MIN)

    return kx, ky