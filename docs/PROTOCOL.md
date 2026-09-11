# Host/device protocol

USB and TCP port 8765 carry the same version-1 newline-delimited JSON protocol. One JSON object occupies one line. Commands include a host `request` number and optional `run` ID. Events contain `v`, `type`, node ID, boot ID, device sequence number and device monotonic time.

## Commands

| Command | Important fields | Purpose |
|---|---|---|
| `hello` | — | Firmware, driver, board, pin, radio and Wi-Fi information |
| `health` | — | Device ID, initialization state, counters and diagnostic support |
| `reset` | — | Hardware reset followed by radio reinitialization |
| `configure` | `node`, `channel`, `antenna_delay`, `cir_hz` | Save node/radio configuration in NVS and apply it |
| `listen` | — | Enter responder and packet-receiver operation |
| `range` | `peer` | Execute one four-message double-sided TWR exchange |
| `packet` | `peer` | Send one numbered packet |
| `stop` | — | Stop the current radio action |
| `wifi` | `enabled` | Enable or disable the provisioned station connection |
| `provision` | `ssid`, `password` | Save Wi-Fi credentials; intended for USB use |

Every command produces an acknowledgement. Measurement failures are measurement records too, with a status such as `timeout`, so failed attempts remain in the denominator.

## Timing rules

- `device_us`: ESP32 monotonic timestamp.
- DS-TWR fields: raw DW3110 40-bit TX/RX timestamps and an exchange duration.
- `host_time_s` and `host_time_iso`: laptop receipt time added by the logger.
- Host arrival time is not interpreted as RF time or one-way network latency.
- The driver handles 40-bit timestamp arithmetic; host tests cover wrap-safe synthetic calculations.

## Diagnostics

Events expose available receive power, first-path power/index, accumulator count, clock offset, status bits, temperature, supply-voltage estimate and STS quality. Fields unsupported by the active DW3110 configuration are emitted as JSON `null` rather than silently replaced by zero. Optional CIR samples are hex-encoded on transport and saved to `cir.bin` plus `cir_index.csv`.

