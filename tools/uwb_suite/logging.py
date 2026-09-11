from __future__ import annotations

import csv
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .analysis import summarize_measurements

MEASUREMENT_FIELDS = [
    "host_time_iso", "host_time_s", "device_us", "run", "node", "peer", "boot", "seq",
    "exchange", "status", "range_m", "raw_range_m", "calibration_offset_m", "true_distance_m",
    "signed_error_m", "exchange_ms", "measurement_age_ms", "rx_power_dbm", "first_path_power_dbm",
    "first_path_index", "accum_count", "clock_offset_raw", "sts_quality", "status_bits",
    "channel", "transport", "station", "orientation_deg", "obstruction", "condition", "notes",
]
POSITION_FIELDS = [
    "host_time_iso", "cycle", "x_m", "y_m", "z_m", "true_x_m", "true_y_m", "true_z_m",
    "position_error_m", "anchors_used", "geometry_condition", "range_residual_rmse_m", "status",
]


class RunLogger:
    def __init__(self, root: Path, stage: str, manifest: dict):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_stage = re.sub(r"[^a-zA-Z0-9_-]+", "-", stage).strip("-") or "run"
        self.run_id = f"{stamp}-{safe_stage}-{uuid.uuid4().hex[:8]}"
        self.path = root / self.run_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.measurements: list[dict] = []
        self.positions: list[dict] = []
        self.event_counts: dict[str, int] = {}
        self.packet_stats = {"transmitted": 0, "tx_ok": 0, "received": 0, "duplicates": 0}
        self._sequence_state: dict[tuple[str, object], tuple[object, int]] = {}
        self._manifest = complete_manifest = dict(manifest)
        self._last_sync = time.monotonic()
        self._events = (self.path / "events.jsonl").open("a", encoding="utf-8", newline="\n")
        self._measurements_file = (self.path / "measurements.csv").open("w", encoding="utf-8", newline="")
        self._positions_file = (self.path / "positions.csv").open("w", encoding="utf-8", newline="")
        self._cir = (self.path / "cir.bin").open("wb")
        self._cir_index_file = (self.path / "cir_index.csv").open("w", encoding="utf-8", newline="")
        self._measurement_writer = csv.DictWriter(self._measurements_file, fieldnames=MEASUREMENT_FIELDS, extrasaction="ignore")
        self._position_writer = csv.DictWriter(self._positions_file, fieldnames=POSITION_FIELDS, extrasaction="ignore")
        self._cir_writer = csv.DictWriter(self._cir_index_file,
                                          fieldnames=["host_time_iso", "node", "peer", "exchange", "offset", "length", "samples", "format"])
        self._measurement_writer.writeheader(); self._position_writer.writeheader(); self._cir_writer.writeheader()
        complete_manifest.update({"run_id": self.run_id, "created_utc": _utc_now(), "log_schema": 1})
        self._write_json("manifest.json", complete_manifest)

    def event(self, event: dict, transport: str = "unknown", metadata: dict | None = None) -> dict:
        row = dict(event)
        now = time.time()
        row.setdefault("host_time_s", now)
        row.setdefault("host_time_iso", datetime.fromtimestamp(now, timezone.utc).isoformat())
        row.setdefault("transport", transport)
        if metadata:
            for key, value in metadata.items():
                row.setdefault(key, value)
        self._track_sequence(row)
        kind = str(row.get("type", "unknown"))
        self.event_counts[kind] = self.event_counts.get(kind, 0) + 1
        if kind == "packet_tx":
            self.packet_stats["transmitted"] += 1
            if row.get("status") == "ok":
                self.packet_stats["tx_ok"] += 1
        elif kind == "packet_rx":
            self.packet_stats["received"] += 1
            if row.get("duplicate"):
                self.packet_stats["duplicates"] += 1
        cir_hex = row.pop("cir_hex", None)
        if cir_hex:
            try:
                payload = bytes.fromhex(cir_hex)
                offset = self._cir.tell(); self._cir.write(payload)
                self._cir_writer.writerow({"host_time_iso": row["host_time_iso"], "node": row.get("node"),
                    "peer": row.get("peer"), "exchange": row.get("exchange"), "offset": offset,
                    "length": len(payload), "samples": row.get("cir_samples"), "format": row.get("cir_format")})
                row["cir_ref"] = {"offset": offset, "length": len(payload)}
            except ValueError:
                row["cir_decode_error"] = True
        self._events.write(json.dumps(row, separators=(",", ":"), allow_nan=False, default=_json_default) + "\n")
        if row.get("type") == "measurement":
            calibrated = _float(row.get("range_m"))
            offset = _float(row.get("calibration_offset_m")) or 0.0
            if calibrated is not None:
                row.setdefault("raw_range_m", calibrated)
                row["range_m"] = calibrated + offset
            truth = _float(row.get("true_distance_m"))
            if truth is not None and _float(row.get("range_m")) is not None:
                row["signed_error_m"] = float(row["range_m"]) - truth
            self._measurement_writer.writerow(row)
            self.measurements.append(dict(row))
        self._sync_if_due()
        return row

    def position(self, row: dict) -> None:
        data = {"host_time_iso": _utc_now(), **row}
        self._position_writer.writerow(data); self.positions.append(data); self._sync_if_due()

    def update_manifest(self, **values) -> None:
        self._manifest.update(values)
        self._write_json("manifest.json", self._manifest)

    def finish(self, status: str = "complete", limitation: str | None = None) -> dict:
        summary = summarize_measurements(self.measurements)
        summary.update({"run_id": self.run_id, "stage_status": status, "finished_utc": _utc_now(),
                        "positions": len(self.positions), "event_counts": self.event_counts,
                        "packet": self.packet_stats})
        if limitation:
            summary["interpretation_limit"] = limitation
        self._write_json("summary.json", summary)
        lines = [f"# UWB run {self.run_id}", "", f"Status: **{status}**", "",
                 f"Attempts: {summary['attempts']}", f"Successful ranges: {summary['successes']}",
                 f"Completion ratio: {summary['completion_ratio']:.3f}"]
        for label, key in [("Bias", "bias_m"), ("MAE", "mae_m"), ("RMSE", "rmse_m"),
                           ("P95 absolute error", "p95_abs_error_m"), ("P99 absolute error", "p99_abs_error_m"),
                           ("Longest observed update gap", "longest_dropout_s")]:
            value = summary[key]
            lines.append(f"{label}: {'unavailable' if value is None else f'{value:.6f} m' if key != 'longest_dropout_s' else f'{value:.6f} s'}")
        if limitation: lines += ["", "## Interpretation limit", "", limitation]
        if self.packet_stats["transmitted"]:
            lines += ["", "## Packet link", "",
                      f"Transmitted: {self.packet_stats['transmitted']}",
                      f"TX accepted: {self.packet_stats['tx_ok']}",
                      f"Received: {self.packet_stats['received']}",
                      f"Duplicates: {self.packet_stats['duplicates']}"]
        (self.path / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.close(); return summary

    def close(self) -> None:
        for stream in (self._events, self._measurements_file, self._positions_file, self._cir, self._cir_index_file):
            if not stream.closed:
                stream.flush(); os.fsync(stream.fileno()); stream.close()

    def _sync_if_due(self) -> None:
        if time.monotonic() - self._last_sync < 1: return
        for stream in (self._events, self._measurements_file, self._positions_file, self._cir, self._cir_index_file):
            stream.flush(); os.fsync(stream.fileno())
        self._last_sync = time.monotonic()

    def _write_json(self, name: str, value: dict) -> None:
        path = self.path / name
        with path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, indent=2, allow_nan=False, default=_json_default); f.write("\n"); f.flush(); os.fsync(f.fileno())

    def _track_sequence(self, row: dict) -> None:
        node, seq, boot = row.get("node"), row.get("seq"), row.get("boot")
        if node is None or not isinstance(seq, int):
            return
        key = (str(row.get("transport", "unknown")), node)
        previous = self._sequence_state.get(key)
        if previous:
            previous_boot, previous_seq = previous
            if boot != previous_boot:
                self._write_internal_event("host_restart_detected", row, previous_boot=previous_boot)
            elif seq > previous_seq + 1:
                self._write_internal_event("host_sequence_gap", row,
                                           first_missing=previous_seq + 1,
                                           last_missing=seq - 1,
                                           missing_count=seq - previous_seq - 1)
            elif seq <= previous_seq:
                self._write_internal_event("host_duplicate_or_reordered", row, previous_seq=previous_seq)
        self._sequence_state[key] = (boot, seq)

    def _write_internal_event(self, kind: str, source: dict, **values) -> None:
        record = {"v": 1, "type": kind, "host_time_iso": source.get("host_time_iso"),
                  "host_time_s": source.get("host_time_s"), "transport": source.get("transport"),
                  "node": source.get("node"), "boot": source.get("boot"), "seq": source.get("seq"), **values}
        self._events.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
        self.event_counts[kind] = self.event_counts.get(kind, 0) + 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float(value: object) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _json_default(value: object):
    if isinstance(value, Path): return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")
