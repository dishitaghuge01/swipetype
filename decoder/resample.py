"""
decoder/resample.py

Equidistant path resampling (PRD section 7.5). Applied to both the
live gesture path and every candidate word's ideal path, so shape
comparison in the scorer is point-for-point on paths of equal length.
"""

from typing import List, Tuple

N_RESAMPLE = 100  # config default


def resample_path(points: List[Tuple[float, float]], n: int = N_RESAMPLE) -> List[Tuple[float, float]]:
    """
    Resample a polyline to exactly n points, evenly spaced by arc
    length, via linear interpolation between the original points.

    Edge case: a single point (or a path where every point coincides,
    i.e. a near-stationary gesture) has zero arc length, so we can't
    divide by it — return the point repeated n times instead.
    """
    if len(points) == 0:
        return []

    if len(points) == 1 or n <= 1:
        return [points[0]] * max(n, 1)

    # Cumulative arc length at each original point.
    cum_lengths = [0.0]
    for i in range(1, len(points)):
        x1, y1 = points[i - 1]
        x2, y2 = points[i]
        segment_len = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        cum_lengths.append(cum_lengths[-1] + segment_len)

    total_length = cum_lengths[-1]

    if total_length == 0:
        # All points coincide despite there being more than one of them.
        return [points[0]] * n

    step = total_length / (n - 1)

    resampled: List[Tuple[float, float]] = []
    seg_idx = 0

    for i in range(n):
        target = i * step

        # Advance to the segment containing this arc-length target.
        while seg_idx < len(cum_lengths) - 2 and cum_lengths[seg_idx + 1] < target:
            seg_idx += 1

        seg_start_len = cum_lengths[seg_idx]
        seg_end_len = cum_lengths[seg_idx + 1]
        seg_len = seg_end_len - seg_start_len

        t = (target - seg_start_len) / seg_len if seg_len else 0.0

        x1, y1 = points[seg_idx]
        x2, y2 = points[seg_idx + 1]
        x = x1 + t * (x2 - x1)
        y = y1 + t * (y2 - y1)

        resampled.append((x, y))

    return resampled