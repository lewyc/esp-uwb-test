from __future__ import annotations

import itertools
import math
import time
from pathlib import Path

from .analysis import solve_position
from .logging import RunLogger

STAGES = ["hardware", "packet", "range", "static", "orientation", "nlos", "rate",
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
        if channel not in (5, 9): raise ValueError("channel must be 5 or 9")
        node_info = []
        for index, node in enumerate(self.nodes, 1):
            events = self._execute(node, {"cmd": "configure", "node": index, "channel": channel,
                                         "antenna_delay": antenna_delay, "cir_hz": float(self.config.get("cir_hz", 0))})
            acknowledgement = next((event for event in reversed(events) if event.get("type") == "ack"), {})
            node_info.append({"endpoint": node.endpoint, "transport": node.transport, **acknowledgement})
        self.log.update_manifest(node_information=node_info)
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
                position = solve_position(normalized, ranges, tag_z); position.update({"cycle": cycle, "status": "ok"})
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
        for _ in range(attempts):
            started = time.monotonic()
            command_data = {"cmd": command, "peer": 2}
            if source.transport == "simulation" and metadata and metadata.get("true_distance_m") is not None:
                command_data["_sim_true_distance_m"] = metadata["true_distance_m"]
            self._execute(source, command_data, metadata); self._drain(); self._pace(started, rate)

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
