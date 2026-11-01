from datetime import datetime
from typing import Iterable

import numpy as np


MINIMUM_SCORE_HISTORY = 20


def percentile_severity(
    score: float,
    earlier_scores: Iterable[float],
) -> tuple[str, str]:
    scores = [float(value) for value in earlier_scores if value is not None]
    if len(scores) < MINIMUM_SCORE_HISTORY:
        return "medium", "insufficient history for severity"

    history = np.asarray(scores, dtype=float)
    high = float(np.quantile(history, 0.95))
    critical = float(np.quantile(history, 0.995))
    if score >= critical:
        severity = "critical"
    elif score >= high:
        severity = "high"
    else:
        severity = "medium"
    return (
        severity,
        (
            f"Severity from earlier detector scores "
            f"(95th percentile {high:.6g}, 99.5th percentile {critical:.6g}; "
            f"{len(scores)} earlier scores)."
        ),
    )
