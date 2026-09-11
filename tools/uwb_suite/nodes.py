from __future__ import annotations

import json
import math
import queue
import random
import socket
import threading
import time
from dataclasses import dataclass


class NodeError(RuntimeError): pass


class StreamNode:
    def __init__(self, endpoint: str, stream, transport: str):
        self.endpoint, self.stream, self.transport = endpoint, stream, transport
        self._events: queue.Queue[dict] = queue.Queue()
        self._request = 0; self._closed = False
        self._reader = threading.Thread(target=self._read_loop, name=f"reader-{endpoint}", daemon=True)
        self._reader.start()

    def execute(self, command: dict, timeout: float = 5.0) -> list[dict]:
        self._request += 1
        request = self._request
        payload = {"v": 1, "request": request, **command}
        self.stream.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        if hasattr(self.stream, "flush"): self.stream.flush()
        events, deadline = [], time.monotonic() + timeout
        while time.monotonic() < deadline:
            try: event = self._events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty: break
            events.append(event)
            if event.get("type") == "ack" and event.get("request") == request:
                return events
        raise NodeError(f"{self.endpoint}: command {command.get('cmd')} timed out")

    def poll(self) -> list[dict]:
        result = []
        while True:
            try: result.append(self._events.get_nowait())
            except queue.Empty: return result

    def close(self) -> None:
        self._closed = True
        try: self.stream.close()
        except Exception: pass

    def _read_loop(self) -> None:
        while not self._closed:
            try: raw = self.stream.readline()
            except Exception as exc:
                self._events.put({"v": 1, "type": "host_transport_error", "status": str(exc)[:200]}); return
            if not raw:
                time.sleep(0.02); continue
            try:
                event = json.loads(raw.decode("utf-8", "replace"))
                if not isinstance(event, dict): raise ValueError("event is not an object")
                self._events.put(event)
            except (json.JSONDecodeError, ValueError) as exc:
                self._events.put({"v": 1, "type": "host_protocol_error", "status": str(exc),
                                  "raw": raw.decode("utf-8", "replace")[:500]})


class SocketStream:
    def __init__(self, host: str, port: int):
        self.socket = socket.create_connection((host, port), timeout=5)
        self.file = self.socket.makefile("rwb", buffering=0)
    def write(self, data): return self.file.write(data)
    def flush(self): return self.file.flush()
    def readline(self): return self.file.readline()
    def close(self): self.file.close(); self.socket.close()


def connect_endpoint(endpoint: str, baud: int = 115200):
    kind, _, target = endpoint.partition(":")
    if kind == "serial":
        try: import serial
        except ImportError as exc: raise NodeError("install pyserial: python -m pip install -r requirements.txt") from exc
        return StreamNode(endpoint, serial.Serial(target, baudrate=baud, timeout=0.1), "usb")
    if kind == "tcp":
        host, sep, port = target.rpartition(":")
        if not sep: host, port = target, "8765"
        return StreamNode(endpoint, SocketStream(host, int(port)), "wifi")
    raise NodeError(f"unsupported endpoint {endpoint!r}; use serial:COM6 or tcp:192.168.1.20:8765")


@dataclass
class SimSettings:
    bias_m: float = 0.04
    sigma_m: float = 0.025
    loss_rate: float = 0.04
    seed: int = 3000


class SimNetwork:
    def __init__(self, settings: SimSettings | None = None):
        self.settings = settings or SimSettings(); self.random = random.Random(self.settings.seed)
        self.nodes: dict[int, SimNode] = {}


class SimNode:
    transport = "simulation"
    def __init__(self, endpoint: str, network: SimNetwork, node_id: int):
        self.endpoint, self.network, self.node_id = endpoint, network, node_id
        self.boot = network.random.randrange(1, 2**32); self.seq = 0; self.request_seq = 0
        self.listening = False; self.channel = 5
        self.network.nodes[node_id] = self

    def execute(self, command: dict, timeout: float = 5.0) -> list[dict]:
        del timeout
        self.request_seq += 1
        cmd = command["cmd"]; request = command.get("request", self.request_seq); events = []
        if cmd == "configure":
            old = self.node_id; self.node_id = int(command.get("node", old)); self.channel = int(command.get("channel", 5))
            self.network.nodes.pop(old, None); self.network.nodes[self.node_id] = self
        elif cmd == "listen": self.listening = True
        elif cmd == "stop": self.listening = False
        elif cmd == "range":
            peer = int(command["peer"]); other = self.network.nodes.get(peer)
            ok = bool(other and other.listening and other.channel == self.channel and self.network.random.random() >= self.network.settings.loss_rate)
            true = float(command.get("_sim_true_distance_m", 2.0))
            value = true + self.network.settings.bias_m + self.network.random.gauss(0, self.network.settings.sigma_m) if ok else None
            events.append(self._event("measurement", request=request, exchange=request, peer=peer, status="ok" if ok else "timeout",
                range_m=value, exchange_ms=self.network.random.uniform(2.5, 5.0), rx_power_dbm=-65.0 if ok else None,
                first_path_power_dbm=-68.0 if ok else None, clock_offset_raw=42 if ok else None))
        elif cmd == "packet":
            peer = int(command["peer"]); other = self.network.nodes.get(peer)
            status = "ok" if other and other.listening else "timeout"
            events.append(self._event("packet_tx", request=request, peer=peer, exchange=request, status=status))
            if status == "ok": other._pending.append(other._event("packet_rx", peer=self.node_id, exchange=request, duplicate=False))
        events.append(self._event("ack", request=request, status="ok", radio_ready=True, device_id=0xDECA0302,
                                  channel=self.channel, firmware="sim-0.1", board="simulated"))
        return events

    _pending: list[dict]
    def poll(self) -> list[dict]:
        out, self._pending = getattr(self, "_pending", []), []
        return out
    def close(self): pass
    def _event(self, kind: str, **values) -> dict:
        self.seq += 1
        return {"v": 1, "type": kind, "node": self.node_id, "boot": self.boot, "seq": self.seq,
                "device_us": int(time.monotonic() * 1e6), **values}


def simulated_nodes(count: int, settings: SimSettings | None = None) -> list[SimNode]:
    network = SimNetwork(settings)
    nodes = []
    for index in range(count):
        node = SimNode(f"sim:{index + 1}", network, index + 1); node._pending = []; nodes.append(node)
    return nodes
