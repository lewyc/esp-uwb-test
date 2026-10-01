from __future__ import annotations

import itertools
import math
import time
from pathlib import Path

from .analysis import evaluate_filters, fit_calibration, solve_position
from .logging import RunLogger

STAGES = ["hardware", "packet", "range", "static", "calibration", "orientation", "nlos", "rate",
          "anchors", "schedule", "motion", "coexistence"]


class SuiteRunner:
    def __init__(self, nodes: list, config: dict, logs_root: Path, interactive: bool = False, compact: bool = False):
        self.nodes, self.config, self.interactive, self.compact = nodes, config, interactive, compact
        stage = config["stage"]
        manifest = {"stage": stage, "config": config, "nodes": [n.endpoint for n in nodes],
                    "transport": [n.transport for n in nodes], "host_tool": "0.1.0"}
        self.log = RunLogger(logs_root, stage, manifest)
        self.run_id = self.log.run_id; self._interrupted = False

    def run(self) -> dict:
        status, limitation = "complete", None
        try:
            self._prepare()
            getattr(self, f"_stage_{self.config['stage']}")()
            if self._interrupted:
                status = "interrupted"
            if self.config["stage"] == "motion" and not self.config.get("ground_truth_available", False):
                limitation = "No independent dynamic ground truth was supplied; results describe continuity, timing and dropout only."
        except KeyboardInterrupt:
            status = "interrupted"; self._interrupted = True
        except Exception as exc:
            status = "failed"; self.log.event({"v": 1, "type": "host_error", "status": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            for node in self.nodes:
                try: self._execute(node, {"cmd": "stop"})
                except Exception: pass
            summary = self.log.finish(status, limitation)
            for node in self.nodes: node.close()
        return summary

    def _prepare(self) -> None:
        if not self.nodes: raise ValueError("at least one node is required")
        channel = int(self.config.get("channel", 5)); antenna_delay = int(self.config.get("antenna_delay", 16385))
        timestamp_mode = str(self.config.get("timestamp_mode", "ipatov_adjusted"))
        if channel not in (5, 9): raise ValueError("channel must be 5 or 9")
        if timestamp_mode not in ("ipatov_adjusted", "standard_adjusted", "raw_unadjusted"):
            raise ValueError("invalid timestamp_mode")
        node_info = []
        for index, node in enumerate(self.nodes, 1):
            settings = self._node_settings(index, antenna_delay)
            events = self._execute(node, {"cmd": "configure", "node": index, "channel": channel,
                                         "antenna_delay": antenna_delay,
                                         "tx_antenna_delay": settings["tx_antenna_delay"],
                                         "rx_antenna_delay": settings["rx_antenna_delay"],
                                         "timestamp_mode": timestamp_mode,
                                         "cir_hz": float(self.config.get("cir_hz", 0))})
            acknowledgement = next((event for event in reversed(events) if event.get("type") == "ack"), {})
            info = {"endpoint": node.endpoint, "transport": node.transport, **acknowledgement}
            if acknowledgement.get("status") != "ok" or not acknowledgement.get("radio_ready", False):
                raise RuntimeError(f"{node.endpoint}: radio is not ready after configuration")
            node_info.append(info)
        modes = {item.get("timestamp_mode") for item in node_info}
        channels = {item.get("channel") for item in node_info}
        if modes != {timestamp_mode} or channels != {channel}:
            raise RuntimeError("nodes did not confirm matching channel/timestamp settings")
        self.log.update_manifest(node_information=node_info, timestamp_mode=timestamp_mode,
                                 antenna_delays=[{"node": index + 1,
                                     "tx_antenna_delay": item.get("tx_antenna_delay"),
                                     "rx_antenna_delay": item.get("rx_antenna_delay")}
                                     for index, item in enumerate(node_info)])
        self.log.event({"v": 1, "type": "host_stage", "status": "started", "stage": self.config["stage"], "run": self.run_id})

    def _stage_hardware(self):
        for node in self.nodes: self._execute(node, {"cmd": "reset"}); self._execute(node, {"cmd": "health"})

    def _stage_packet(self): self._pair_attempts("packet")
    def _stage_range(self):
        self._pair_attempts("range", {"true_distance_m": self.config.get("true_distance_m")})

    def _stage_static(self):
        distances = self.config.get("distances_m", [0.5, 1, 2, 3, 5, 8, 10])
        for distance in distances:
            self._pause(f"Place antennas {distance:g} m apart, then press Enter")
            self._pair_attempts("range", {"true_distance_m": float(distance), "station": f"{distance:g}m"})

    def _stage_calibration(self):
        if len(self.nodes) < 2:
            raise ValueError("calibration stage needs exactly two selected nodes")
        print("\nCALIBRATION DISTANCES MUST BE MEASURED BETWEEN ANTENNA REFERENCE POINTS.")
        print("Do not use board edges or a guessed separation as surveyed ground truth.")
        stations = [dict(item) for item in self.config.get("calibration_stations", [])]
        if self.interactive and not stations:
            stations = self._interactive_station_plan()
        if not stations:
            raise ValueError("no calibration stations were configured")
        collected = []
        interrupted = False
        try:
            for number, station in enumerate(stations, 1):
                normalized = self._normalize_station(station, number)
                self._pause(
                    f"Place antenna reference points {normalized['true_distance_m']:g} m apart "
                    f"(station {normalized['station_id']}: {normalized['station_label']}). "
                    "Press Enter to start"
                )
                result = self._collect_calibration_station(normalized)
                collected.append({**normalized, **result})
                self._execute(self.nodes[1], {"cmd": "stop"})
                print(f"Station complete: {result['successes']}/{result['attempts_completed']} successful.")
                if result.get("interrupted"):
                    interrupted = True
                    print("Collection stopped during this station; partial samples were preserved.")
                    break
                if self.interactive and number < len(stations):
                    action = input("Press Enter for the next station, or type 'finish' to stop early: ").strip().lower()
                    if action in ("finish", "f", "stop"):
                        interrupted = True
                        break
        except KeyboardInterrupt:
            interrupted = True
            print("\nCollection stopped. Already logged samples will be preserved and analysed.")
            self.log.event({"v": 1, "type": "host_calibration_stop", "status": "partial",
                            "stations_completed": len(collected)})
        if not collected:
            raise KeyboardInterrupt
        reviewed = self._review_stations(collected)
        result = fit_calibration(self.log.measurements, reviewed)
        filters = evaluate_filters(
            self.log.measurements,
            int(self.config.get("median_window", 5)),
            int(self.config.get("hampel_window", 7)),
            float(self.config.get("hampel_sigma", 3.0)),
        )
        if interrupted:
            result["warnings"].append("Collection ended early; the calibration dataset is partial.")
            self._interrupted = True
        self.log.write_calibration(reviewed, result, filters)
        self.log.update_manifest(calibration_stations=reviewed,
                                 calibration_evaluation=result["evaluation_kind"],
                                 calibration_partial=interrupted)
        print(f"Calibration analysis saved in {self.log.path}")
        for warning in result["warnings"]:
            print(f"WARNING: {warning}")

    def _stage_orientation(self):
        for angle in self.config.get("orientations_deg", [0, 90, 180]):
            self._pause(f"Set orientation to {angle:g} degrees, then press Enter")
            self._pair_attempts("range", {"orientation_deg": angle, "true_distance_m": self.config.get("true_distance_m")})

    def _stage_nlos(self):
        for obstruction in self.config.get("obstructions", ["los", "human", "wall", "glass", "corner"]):
            self._pause(f"Set condition to {obstruction}, then press Enter")
            self._pair_attempts("range", {"obstruction": obstruction, "true_distance_m": self.config.get("true_distance_m")})

    def _stage_rate(self):
        original = self.config.get("rate_hz", 10)
        for distance in self.config.get("distances_m", [self.config.get("true_distance_m")]):
            if distance is not None:
                self._pause(f"Place antennas {float(distance):g} m apart, then press Enter")
            for rate in self.config.get("rates_hz", [1, 5, 10, 20]):
                self.config["rate_hz"] = rate
                self._pair_attempts("range", {"condition": f"rate_{rate:g}hz",
                                              "true_distance_m": distance})
        self.config["rate_hz"] = original

    def _stage_coexistence(self):
        for condition in self.config.get("conditions", ["wifi_off", "wifi_on", "other_electronics_on"]):
            self._pause(f"Set electronics condition to {condition}, then press Enter")
            self._pair_attempts("range", {"condition": condition, "true_distance_m": self.config.get("true_distance_m")})

    def _stage_schedule(self):
        if len(self.nodes) < 2: raise ValueError("schedule stage needs at least two nodes")
        for node in self.nodes: self._execute(node, {"cmd": "listen"})
        attempts = int(self.config.get("attempts", 100)); rate = float(self.config.get("rate_hz", 10))
        mode = self.config.get("schedule", "all_pairs")
        schedules = []
        if mode in ("star", "both"):
            schedules.append(("star", [(0, target) for target in range(1, len(self.nodes))]))
        if mode in ("all_pairs", "both"):
            schedules.append(("all_pairs", list(itertools.combinations(range(len(self.nodes)), 2))))
        if not schedules:
            raise ValueError("schedule must be star, all_pairs or both")
        for name, pairs in schedules:
            for _ in range(attempts):
                for source, target in pairs:
                    started = time.monotonic()
                    self._execute(self.nodes[source], {"cmd": "range", "peer": target + 1},
                                  {"condition": name})
                    self._drain(); self._pace(started, rate * len(pairs))

    def _stage_motion(self): self._pair_attempts("range", {"condition": "motion"})

    def _stage_anchors(self):
        anchors = self.config.get("anchors")
        if not isinstance(anchors, list) or len(anchors) < 3: raise ValueError("anchors stage needs at least three anchor definitions")
        if len(self.nodes) < len(anchors) + 1: raise ValueError("first node is tag; one additional node is required per anchor")
        tag, anchor_nodes = self.nodes[0], self.nodes[1:len(anchors) + 1]
        normalized = []
        for index, anchor in enumerate(anchors, 2): normalized.append({"node": index, **anchor})
        for node in anchor_nodes: self._execute(node, {"cmd": "listen"})
        cycles = int(self.config.get("cycles", 100)); tag_z = float(self.config.get("tag_z_m", 0))
        for cycle in range(cycles):
            ranges = {}
            for anchor, node in zip(normalized, anchor_nodes):
                command = {"cmd": "range", "peer": anchor["node"]}
                truth = self.config.get("true_position")
                if tag.transport == "simulation" and truth:
                    command["_sim_true_distance_m"] = math.dist(
                        tuple(float(value) for value in truth),
                        (float(anchor["x"]), float(anchor["y"]), float(anchor.get("z", 0))),
                    )
                events = self._execute(tag, command, {"cycle": cycle})
                for event in events:
                    if event.get("type") == "measurement" and event.get("status") == "ok" and event.get("range_m") is not None:
                        ranges[int(anchor["node"])] = float(event["range_m"])
            try:
                position = solve_position(normalized, ranges, tag_z,
                                          robust=bool(self.config.get("robust_positioning", False)),
                                          huber_delta_m=float(self.config.get("huber_delta_m", 0.4)))
                position.update({"cycle": cycle, "status": "ok"})
                truth = self.config.get("true_position")
                if truth:
                    position.update({"true_x_m": truth[0], "true_y_m": truth[1], "true_z_m": truth[2]})
                    position["position_error_m"] = math.dist((position["x_m"], position["y_m"], position["z_m"]), truth)
            except ValueError as exc: position = {"cycle": cycle, "status": str(exc), "anchors_used": len(ranges)}
            self.log.position(position); print("POSITION", position)

    def _pair_attempts(self, command: str, metadata: dict | None = None):
        if len(self.nodes) < 2: raise ValueError(f"{self.config['stage']} stage needs at least two nodes")
        source, target = self.nodes[0], self.nodes[1]
        self._execute(target, {"cmd": "listen"})
        attempts = int(self.config.get("attempts", 1000)); rate = float(self.config.get("rate_hz", 10))
        for attempt_index in range(attempts):
            started = time.monotonic()
            command_data = {"cmd": command, "peer": 2}
            if source.transport == "simulation" and metadata and metadata.get("true_distance_m") is not None:
                command_data["_sim_true_distance_m"] = metadata["true_distance_m"]
            attempt_metadata = {**(metadata or {}), "attempt_index": attempt_index + 1}
            self._execute(source, command_data, attempt_metadata); self._drain(); self._pace(started, rate)

    def _collect_calibration_station(self, station: dict) -> dict:
        source, target = self.nodes[0], self.nodes[1]
        self._execute(target, {"cmd": "listen"})
        attempts = int(station["attempts"])
        rate = float(self.config.get("rate_hz", 10))
        successes = failures = completed = 0
        interrupted = False
        try:
            for attempt_index in range(attempts):
                started = time.monotonic()
                metadata = {
                    "station": station["station_label"], "station_id": station["station_id"],
                    "station_label": station["station_label"],
                    "true_distance_m": station["true_distance_m"],
                    "distance_uncertainty_m": station["distance_uncertainty_m"],
                    "fit_role": station["fit_role"], "notes": station["notes"],
                    "attempt_index": attempt_index + 1,
                    "warmup": attempt_index < station["warmup_attempts"],
                    "excluded_from_fit": attempt_index < station["warmup_attempts"],
                    "exclude_reason": "warmup" if attempt_index < station["warmup_attempts"] else "",
                }
                command = {"cmd": "range", "peer": 2}
                if source.transport == "simulation":
                    command["_sim_true_distance_m"] = station["true_distance_m"]
                events = self._execute(source, command, metadata)
                measurements = [event for event in events if event.get("type") == "measurement"]
                completed += 1
                if any(event.get("status") == "ok" for event in measurements):
                    successes += 1
                else:
                    failures += 1
                self._drain()
                print(f"  station {station['station_id']}: {completed}/{attempts} attempts, "
                      f"{successes} successful, {failures} failed, {attempts - completed} remaining")
                self._pace(started, rate)
        except KeyboardInterrupt:
            interrupted = True
            self.log.event({"v": 1, "type": "host_calibration_stop", "status": "partial",
                            "station_id": station["station_id"], "attempts_completed": completed})
        return {"attempts_completed": completed, "successes": successes,
                "failures": failures, "interrupted": interrupted}

    def _interactive_station_plan(self) -> list[dict]:
        raw_count = input("Number of stations (blank/0 = add stations until finished) [0]: ").strip() or "0"
        count = int(raw_count)
        stations = []
        number = 1
        while count == 0 or number <= count:
            if count == 0 and number > 1:
                more = input("Add another station? [Y/n]: ").strip().lower()
                if more.startswith("n"):
                    break
            print(f"\nStation {number}")
            distance = float(input("Measured antenna-reference-point distance (m): ").strip())
            uncertainty = float(input("Distance uncertainty (m) [0]: ").strip() or "0")
            label = input(f"Label [station-{number}]: ").strip() or f"station-{number}"
            notes = input("Notes (optional): ").strip()
            role = input("Use for fit or independent validation? [fit]: ").strip().lower() or "fit"
            attempts = int(input(f"Ranging attempts [{self.config.get('attempts', 1000)}]: ").strip()
                           or str(self.config.get("attempts", 1000)))
            warmup = int(input("Initial warm-up attempts to exclude from fitting [0]: ").strip() or "0")
            stations.append({"station_id": number, "true_distance_m": distance,
                             "distance_uncertainty_m": uncertainty, "station_label": label,
                             "notes": notes, "fit_role": role, "attempts": attempts,
                             "warmup_attempts": warmup})
            number += 1
        return stations

    def _normalize_station(self, station: dict, number: int) -> dict:
        role = str(station.get("fit_role", "fit")).lower()
        if role not in ("fit", "validation"):
            raise ValueError("station fit_role must be 'fit' or 'validation'")
        attempts = int(station.get("attempts", self.config.get("attempts", 1000)))
        warmup = int(station.get("warmup_attempts", self.config.get("warmup_attempts", 0)))
        distance = float(station["true_distance_m"])
        uncertainty = float(station.get("distance_uncertainty_m", 0))
        if distance <= 0 or uncertainty < 0 or attempts < 1 or warmup < 0 or warmup >= attempts:
            raise ValueError("invalid station distance, uncertainty, attempts, or warm-up count")
        return {"station_id": station.get("station_id", number),
                "station_label": str(station.get("station_label", f"station-{number}")),
                "true_distance_m": distance, "distance_uncertainty_m": uncertainty,
                "fit_role": role, "notes": str(station.get("notes", "")),
                "attempts": attempts, "warmup_attempts": warmup}

    def _review_stations(self, stations: list[dict]) -> list[dict]:
        print("\nStation review (metadata corrections do not alter raw radio events):")
        for station in stations:
            print(f"  {station['station_id']}: {station['true_distance_m']} m +/- "
                  f"{station['distance_uncertainty_m']} m, role={station['fit_role']}, "
                  f"success={station['successes']}/{station['attempts_completed']}")
        if not self.interactive:
            return stations
        if not input("Correct station metadata before fitting? [y/N]: ").strip().lower().startswith("y"):
            return stations
        reviewed = []
        for station in stations:
            item = dict(station)
            value = input(f"Station {item['station_id']} distance [{item['true_distance_m']}]: ").strip()
            if value: item["true_distance_m"] = float(value)
            value = input(f"Uncertainty [{item['distance_uncertainty_m']}]: ").strip()
            if value: item["distance_uncertainty_m"] = float(value)
            value = input(f"Label [{item['station_label']}]: ").strip()
            if value: item["station_label"] = value
            value = input(f"Role fit/validation [{item['fit_role']}]: ").strip().lower()
            if value: item["fit_role"] = value
            value = input(f"Notes [{item['notes']}]: ").strip()
            if value: item["notes"] = value
            reviewed.append(item)
        return reviewed

    def _node_settings(self, node_id: int, fallback: int) -> dict:
        settings = self.config.get("node_settings", {})
        if isinstance(settings, list):
            item = settings[node_id - 1] if node_id <= len(settings) else {}
        else:
            item = settings.get(str(node_id), settings.get(node_id, {}))
        return {"tx_antenna_delay": int(item.get("tx_antenna_delay", fallback)),
                "rx_antenna_delay": int(item.get("rx_antenna_delay", fallback))}

    def _execute(self, node, command: dict, metadata: dict | None = None) -> list[dict]:
        command = {"run": self.run_id, **command}
        events = node.execute(command, timeout=float(self.config.get("command_timeout_s", 5)))
        return [self._record(event, node.transport, metadata) for event in events]

    def _record(self, event: dict, transport: str, metadata: dict | None = None) -> dict:
        combined = dict(metadata or {})
        if "calibration_offset_m" in self.config:
            combined.setdefault("calibration_offset_m", self.config["calibration_offset_m"])
        row = self.log.event(event, transport, combined)
        kind = row.get("type")
        if kind == "measurement":
            rng = row.get("range_m"); power = row.get("rx_power_dbm"); status = row.get("status")
            print(f"RANGE node={row.get('node')} peer={row.get('peer')} status={status} distance={rng if rng is not None else 'NA'} m rx={power if power is not None else 'NA'} dBm")
        elif not self.compact or kind in ("host_error", "radio_error", "protocol_error"):
            print(f"EVENT {kind} node={row.get('node', '?')} status={row.get('status', '')}")
        return row

    def _drain(self):
        for node in self.nodes:
            for event in node.poll(): self._record(event, node.transport)

    def _pause(self, prompt: str):
        if self.interactive: input(prompt + ": ")

    @staticmethod
    def _pace(start: float, rate: float):
        if rate <= 0: return
        delay = 1.0 / rate - (time.monotonic() - start)
        if delay > 0: time.sleep(delay)
