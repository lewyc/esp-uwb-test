from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .nodes import connect_endpoint, simulated_nodes
from .runner import STAGES, SuiteRunner

ROOT = Path(__file__).resolve().parents[2]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DWM3000EVB test and logging suite")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("wizard", help="interactive test selection")
    run = sub.add_parser("run", help="run a saved JSON configuration")
    run.add_argument("config", type=Path); run.add_argument("--compact", action="store_true")
    sim = sub.add_parser("simulate", help="exercise the complete host workflow without radios")
    sim.add_argument("--stage", choices=STAGES, default="static"); sim.add_argument("--nodes", type=int, default=2)
    sim.add_argument("--attempts", type=int, default=20); sim.add_argument("--compact", action="store_true")
    sub.add_parser("discover", help="list serial ports")
    provision = sub.add_parser("provision", help="send Wi-Fi credentials over USB serial")
    provision.add_argument("endpoint", help="for example serial:COM6"); provision.add_argument("--ssid", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "discover":
        try:
            from serial.tools import list_ports
            ports = list(list_ports.comports())
        except ImportError:
            print("pyserial is not installed; run: python -m pip install -r requirements.txt", file=sys.stderr); return 2
        for port in ports: print(f"{port.device}\t{port.description}\t{port.hwid}")
        return 0 if ports else 1
    if args.command == "provision":
        node = connect_endpoint(args.endpoint)
        password = getpass.getpass("Wi-Fi password (not saved): ")
        print(node.execute({"cmd": "provision", "ssid": args.ssid, "password": password})); node.close(); return 0
    if args.command == "wizard": config, simulate, interactive, compact = wizard()
    elif args.command == "run":
        config = json.loads(args.config.read_text(encoding="utf-8")); simulate = bool(config.pop("simulate", False)); interactive = False; compact = args.compact
    else:
        config = {"stage": args.stage, "attempts": args.attempts, "rate_hz": 100,
                  "distances_m": [0.5, 1, 2], "anchors": _default_anchors(), "cycles": args.attempts,
                  "tag_z_m": 1, "true_position": [1.2, 1.4, 1]}
        simulate, interactive, compact = True, False, args.compact
    endpoints = config.pop("endpoints", [])
    nodes = simulated_nodes(int(config.pop("node_count", args.nodes if hasattr(args, "nodes") else 2))) if simulate else [connect_endpoint(e) for e in endpoints]
    if config["stage"] == "anchors" and len(nodes) < 5: raise SystemExit("anchors stage needs five nodes")
    runner = SuiteRunner(nodes, config, ROOT / "logs", interactive, compact)
    print(f"Run directory: {runner.log.path}")
    try: summary = runner.run()
    except Exception as exc: print(f"Test failed: {exc}", file=sys.stderr); return 1
    print(json.dumps(summary, indent=2)); return 0


def wizard() -> tuple[dict, bool, bool, bool]:
    print("\nESP32-S3 DWM3000EVB test wizard")
    print("Stages: " + ", ".join(STAGES))
    stage = _prompt("Stage", "static")
    if stage not in STAGES: raise SystemExit(f"unknown stage {stage}")
    simulated = _prompt("Use simulated nodes? [y/N]", "n").lower().startswith("y")
    required = 5 if stage == "anchors" else 2
    if simulated:
        count = int(_prompt("Number of simulated nodes", str(required))); endpoints = []
    else:
        endpoints = [x.strip() for x in _prompt("Endpoints, comma separated (serial:COM6 or tcp:IP:8765)").split(",") if x.strip()]
        count = len(endpoints)
    config = {"stage": stage, "endpoints": endpoints, "node_count": count,
              "channel": int(_prompt("UWB channel [5/9]", "5")),
              "attempts": int(_prompt("Attempts per station", "1000")),
              "rate_hz": float(_prompt("Attempt rate (Hz)", "10")), "cir_hz": float(_prompt("CIR capture rate (0-10 Hz)", "0"))}
    if stage == "static": config["distances_m"] = _floats(_prompt("Distances in metres", "0.5,1,2,3,5,8,10"))
    elif stage in ("range", "orientation", "nlos", "coexistence"):
        config["true_distance_m"] = float(_prompt("True antenna-to-antenna distance (m)", "1"))
    elif stage == "orientation": config["orientations_deg"] = _floats(_prompt("Orientations (degrees)", "0,90,180"))
    elif stage == "nlos": config["obstructions"] = [x.strip() for x in _prompt("Conditions", "los,human,wall,glass,corner").split(",")]
    elif stage == "rate":
        config["rates_hz"] = _floats(_prompt("Rates (Hz)", "1,5,10,20"))
        config["distances_m"] = _floats(_prompt("Distances in metres", "1,3,5"))
    elif stage == "coexistence": config["conditions"] = [x.strip() for x in _prompt("Conditions", "wifi_off,wifi_on,other_electronics_on").split(",")]
    elif stage == "anchors":
        config.update({"anchors": _default_anchors(), "cycles": int(_prompt("Position cycles", "100")),
                       "tag_z_m": float(_prompt("Known tag antenna height (m)", "1"))})
        truth = _prompt("Optional true tag x,y,z (blank if unknown)", "")
        if truth: config["true_position"] = _floats(truth)
    elif stage == "schedule":
        config["schedule"] = _prompt("Schedule [star/all_pairs/both]", "all_pairs")
    return config, simulated, True, False


def _default_anchors():
    return [{"x": 0, "y": 0, "z": 1}, {"x": 4, "y": 0, "z": 1},
            {"x": 4, "y": 4, "z": 1}, {"x": 0, "y": 4, "z": 1}]
def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip(); return value or default
def _floats(value: str) -> list[float]: return [float(x.strip()) for x in value.split(",")]
