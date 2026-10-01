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


def timestamp_delta_40(later: int, earlier: int) -> int:
    """Unsigned DW3000 40-bit timestamp difference, including one wrap."""
    return (int(later) - int(earlier)) & ((1 << 40) - 1)


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
    result = {
        "attempts": len(rows),
        "successes": len(successes),
        "completion_ratio": len(successes) / len(rows) if rows else 0,
        "bias_m": statistics.fmean(errors) if errors else None,
        "mae_m": statistics.fmean(abs_errors) if abs_errors else None,
        "rmse_m": math.sqrt(statistics.fmean(x * x for x in errors)) if errors else None,
        "range_std_m": statistics.stdev(float(r["range_m"]) for r in successes) if len(successes) > 1 else None,
        "p95_abs_error_m": percentile(abs_errors, 0.95),
        "p99_abs_error_m": percentile(abs_errors, 0.99),
        "median_abs_error_m": statistics.median(abs_errors) if abs_errors else None,
        "mean_update_interval_s": statistics.fmean(intervals) if intervals else None,
        "p95_update_interval_s": percentile(intervals, 0.95),
        "longest_dropout_s": max(intervals) if intervals else None,
        "pairs": dict(sorted(by_pair.items())),
    }
    stations: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("station_id") is not None or row.get("station") is not None:
            stations[str(row.get("station_id", row.get("station")))].append(row)
    result["stations"] = {key: summarize_measurements_no_groups(value)
                          for key, value in sorted(stations.items())}
    return result


def summarize_measurements_no_groups(rows: Iterable[dict], range_key: str = "range_m") -> dict:
    """Error/completion metrics without recursive pair/station grouping."""
    rows = list(rows)
    successes = [r for r in rows if r.get("status") == "ok" and _number(r.get(range_key))]
    errors = [float(r[range_key]) - float(r["true_distance_m"])
              for r in successes if _number(r.get("true_distance_m"))]
    absolute = [abs(value) for value in errors]
    return {
        "attempts": len(rows), "successes": len(successes),
        "completion_ratio": len(successes) / len(rows) if rows else 0.0,
        "bias_m": statistics.fmean(errors) if errors else None,
        "mae_m": statistics.fmean(absolute) if absolute else None,
        "rmse_m": math.sqrt(statistics.fmean(value * value for value in errors)) if errors else None,
        "std_m": statistics.stdev(errors) if len(errors) > 1 else None,
        "median_abs_error_m": statistics.median(absolute) if absolute else None,
        "p95_abs_error_m": percentile(absolute, 0.95),
        "p99_abs_error_m": percentile(absolute, 0.99),
    }


def apply_calibration(raw_range_m: float, model: dict) -> float:
    return float(model.get("scale", 1.0)) * raw_range_m + float(model.get("offset_m", 0.0))


def fit_calibration(rows: Iterable[dict], stations: list[dict] | None = None) -> dict:
    """Fit baseline, constant-offset and affine software corrections."""
    rows = [dict(row) for row in rows]
    station_map = {str(item["station_id"]): dict(item) for item in (stations or [])}
    enriched = []
    for row in rows:
        meta = station_map.get(str(row.get("station_id")), {})
        item = {**row}
        for key in ("true_distance_m", "distance_uncertainty_m", "fit_role", "station_label", "notes"):
            if key in meta:
                item[key] = meta[key]
        enriched.append(item)
    fit_population = [row for row in enriched if row.get("fit_role", "fit") == "fit"
                      and not _truthy(row.get("excluded_from_fit"))]
    validation_population = [row for row in enriched if row.get("fit_role") == "validation"
                             and not _truthy(row.get("excluded_from_fit"))]
    usable = [row for row in enriched if row.get("status") == "ok"
              and _number(row.get("raw_range_m", row.get("range_m")))
              and _number(row.get("true_distance_m"))]
    fit_rows = [row for row in fit_population if row in usable]
    validation_rows = [row for row in validation_population if row in usable]
    distinct = sorted({float(row["true_distance_m"]) for row in fit_rows})
    warnings = []
    if len(distinct) < 2:
        warnings.append("Fewer than two distinct fitting distances; scale-plus-offset is not fitted.")
    if len(fit_rows) < 20:
        warnings.append("Fewer than 20 successful fitting samples; estimates may be unstable.")
    if distinct and max(distinct) - min(distinct) < 1.0:
        warnings.append("Fitting-distance coverage is under 1 m; scale estimation is weakly constrained.")
    if any(float(row.get("distance_uncertainty_m", 0) or 0) > 0.02 for row in fit_rows):
        warnings.append("At least one fitting station has ground-truth uncertainty above 2 cm.")
    if not validation_rows:
        warnings.append("No independent validation station: results are exploratory training-set results.")
    models = [{"name": "baseline", "scale": 1.0, "offset_m": 0.0}]
    if fit_rows:
        models.append({"name": "constant_offset", "scale": 1.0,
                       "offset_m": statistics.fmean(float(row["true_distance_m"]) - _raw(row)
                                                   for row in fit_rows)})
    if len(distinct) >= 2 and len(fit_rows) >= 2:
        xs, ys = [_raw(row) for row in fit_rows], [float(row["true_distance_m"]) for row in fit_rows]
        mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
        denominator = sum((value - mean_x) ** 2 for value in xs)
        if denominator > 1e-12:
            scale = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
            models.append({"name": "scale_plus_offset", "scale": scale,
                           "offset_m": mean_y - scale * mean_x})
        else:
            warnings.append("Measured fitting ranges are degenerate; affine model was not fitted.")
    evaluation_rows = validation_population or fit_population
    evaluation_kind = "independent_validation" if validation_rows else "training_exploratory"
    for model in models:
        model["fit"] = _model_metrics(fit_population, model)
        model["validation"] = _model_metrics(evaluation_rows, model)
    return {
        "schema": 1, "software_correction_only": True,
        "antenna_delay_conversion_performed": False,
        "evaluation_kind": evaluation_kind,
        "fit_station_count": len({str(row.get("station_id")) for row in fit_rows}),
        "validation_station_count": len({str(row.get("station_id")) for row in validation_rows}),
        "fit_attempts": len(fit_population), "validation_attempts": len(validation_population),
        "fit_samples": len(fit_rows), "validation_samples": len(validation_rows),
        "raw_attempts": len(enriched), "successful_before_exclusions": len(usable),
        "models": models, "warnings": warnings,
    }


def evaluate_filters(rows: Iterable[dict], median_window: int = 5,
                     hampel_window: int = 7, hampel_sigma: float = 3.0) -> dict:
    """Evaluate offline-only filters without mutating source rows."""
    source = [dict(row) for row in rows]
    grouped: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for index, row in enumerate(source):
        grouped[str(row.get("station_id", "all"))].append((index, row))
    median_values, rejected, weights = {}, set(), {}
    for group in grouped.values():
        valid = [(index, _raw(row)) for index, row in group
                 if row.get("status") == "ok" and _number(row.get("raw_range_m", row.get("range_m")))]
        values = [value for _, value in valid]
        for position, (index, value) in enumerate(valid):
            median_values[index] = statistics.median(_window(values, position, median_window))
            samples = _window(values, position, hampel_window)
            center = statistics.median(samples)
            mad = statistics.median(abs(sample - center) for sample in samples)
            robust_sigma = 1.4826 * mad
            if robust_sigma > 0 and abs(value - center) > hampel_sigma * robust_sigma:
                rejected.add(index)
        for index, row in group:
            rx, fp = row.get("rx_power_dbm"), row.get("first_path_power_dbm")
            weights[index] = (1.0 / (1.0 + max(0.0, float(rx) - float(fp)))
                              if _number(rx) and _number(fp) else None)
    median_rows = [{**row, **({"filtered_range_m": median_values[index]}
                              if index in median_values else {})}
                   for index, row in enumerate(source)]
    hampel_rows = [row for index, row in enumerate(source) if index not in rejected]
    return {
        "settings": {"median_window": median_window, "hampel_window": hampel_window,
                     "hampel_sigma": hampel_sigma,
                     "quality_method": "1/(1+max(0,total_rx_dbm-first_path_dbm))"},
        "baseline": summarize_measurements_no_groups(source, "raw_range_m"),
        "median": {**summarize_measurements_no_groups(median_rows, "filtered_range_m"),
                   "retained": len(median_rows)},
        "hampel": {**summarize_measurements_no_groups(hampel_rows, "raw_range_m"),
                   "retained": len(hampel_rows), "rejected": len(rejected)},
        "quality_weighting": {"available": sum(value is not None for value in weights.values()),
                              "missing": sum(value is None for value in weights.values()),
                              "retained": len(source), "hard_cutoff_used": False},
    }


def _model_metrics(rows: list[dict], model: dict) -> dict:
    corrected = []
    for row in rows:
        item = dict(row)
        if row.get("status") == "ok" and _number(row.get("raw_range_m", row.get("range_m"))):
            item["corrected_range_m"] = apply_calibration(_raw(row), model)
        corrected.append(item)
    return summarize_measurements_no_groups(corrected, "corrected_range_m")


def _raw(row: dict) -> float:
    return float(row.get("raw_range_m", row.get("range_m")))


def _window(values: list[float], position: int, width: int) -> list[float]:
    width = max(1, int(width)); radius = width // 2
    return values[max(0, position - radius):min(len(values), position + radius + 1)]


def _truthy(value: object) -> bool:
    return value is True or str(value).lower() in ("1", "true", "yes")


def solve_position(anchors: list[dict], ranges: dict[int, float], tag_z: float,
                   initial: tuple[float, float] | None = None,
                   robust: bool = False, huber_delta_m: float = 0.4) -> dict:
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
            if robust and abs(residual) > huber_delta_m:
                weight *= huber_delta_m / abs(residual)
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
        "solver": "huber_irls" if robust else "weighted_least_squares",
    }


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
