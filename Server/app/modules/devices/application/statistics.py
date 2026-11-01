import numpy as np


def robust_z_score(
    value: float, baseline: list[float]
) -> tuple[float | None, float | None, float | None]:
    values = np.asarray(baseline, dtype=float)
    center = float(np.median(values))
    scale = float(np.median(np.abs(values - center)) * 1.4826)
    if scale == 0:
        scale = float(np.std(values))
    if scale == 0:
        return None, center, None
    return abs(value - center) / scale, center, scale
