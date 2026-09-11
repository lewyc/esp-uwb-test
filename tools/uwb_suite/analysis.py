from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Iterable


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    x = (len(data) - 1) * p
    lo, hi = math.floor(x), math.ceil(x)
    return data[lo] + (data[hi] - data[lo]) * (x - lo)


def summarize_measurements(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    successes = [r for r in rows if r.get("status") == "ok" and _number(r.get("range_m"))]
    errors = [float(r["range_m"]) - float(r["true_distance_m"])
              for r in successes if _number(r.get("true_distance_m"))]
    abs_errors = [abs(x) for x in errors]
    host_times = sorted(float(r["host_time_s"]) for r in successes if _number(r.get("host_time_s")))
    intervals = [b - a for a, b in zip(host_times, host_times[1:])]
    by_pair: dict[str, dict[str, int]] = defaultdict(lambda: {"attempts": 0, "successes": 0})
    for row in rows:
        pair = f'{row.get("node", "?")}-{row.get("peer", "?")}'
        by_pair[pair]["attempts"] += 1
        if row.get("status") == "ok":
            by_pair[pair]["successes"] += 1
    for value in by_pair.values():
        value["completion_ratio"] = value["successes"] / value["attempts"] if value["attempts"] else 0
    return {
        "attempts": len(rows),
        "successes": len(successes),
        "completion_ratio": len(successes) / len(rows) if rows else 0,
        "bias_m": statistics.fmean(errors) if errors else None,
        "mae_m": statistics.fmean(abs_errors) if abs_errors else None,
        "rmse_m": math.sqrt(statistics.fmean(x * x for x in errors)) if errors else None,
        "range_std_m": statistics.stdev(float(r["range_m"]) for r in successes) if len(successes) > 1 else None,
        "p95_abs_error_m": percentile(abs_errors, 0.95),
        "p99_abs_error_m": percentile(abs_errors, 0.99),
        "mean_update_interval_s": statistics.fmean(intervals) if intervals else None,
        "p95_update_interval_s": percentile(intervals, 0.95),
        "longest_dropout_s": max(intervals) if intervals else None,
        "pairs": dict(sorted(by_pair.items())),
    }


def solve_position(anchors: list[dict], ranges: dict[int, float], tag_z: float,
                   initial: tuple[float, float] | None = None) -> dict:
    usable = [a for a in anchors if int(a["node"]) in ranges]
    if len(usable) < 3:
        raise ValueError("at least three valid anchor ranges are required")
    x = initial[0] if initial else statistics.fmean(float(a["x"]) for a in usable)
    y = initial[1] if initial else statistics.fmean(float(a["y"]) for a in usable)
    condition = None
    for _ in range(25):
        h00 = h01 = h11 = g0 = g1 = 0.0
        for anchor in usable:
            dx, dy = x - float(anchor["x"]), y - float(anchor["y"])
            dz = tag_z - float(anchor.get("z", 0.0))
            predicted = max(math.sqrt(dx * dx + dy * dy + dz * dz), 1e-9)
            residual = predicted - ranges[int(anchor["node"])]
            sigma = max(float(anchor.get("sigma_m", 0.15)), 0.01)
            weight = 1.0 / (sigma * sigma)
            jx, jy = dx / predicted, dy / predicted
            h00 += weight * jx * jx
            h01 += weight * jx * jy
            h11 += weight * jy * jy
            g0 += weight * jx * residual
            g1 += weight * jy * residual
        det = h00 * h11 - h01 * h01
        trace = h00 + h11
        condition = trace * trace / max(det, 1e-18)
        if det < 1e-10 or condition > 1e7:
            raise ValueError("anchor geometry is singular or poorly conditioned")
        step_x = (-h11 * g0 + h01 * g1) / det
        step_y = (h01 * g0 - h00 * g1) / det
        x, y = x + step_x, y + step_y
        if math.hypot(step_x, step_y) < 1e-6:
            break
    residuals = []
    for anchor in usable:
        d = math.dist((x, y, tag_z), (float(anchor["x"]), float(anchor["y"]), float(anchor.get("z", 0))))
        residuals.append(d - ranges[int(anchor["node"])])
    return {
        "x_m": x, "y_m": y, "z_m": tag_z,
        "anchors_used": len(usable), "geometry_condition": condition,
        "range_residual_rmse_m": math.sqrt(statistics.fmean(r * r for r in residuals)),
    }


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))

