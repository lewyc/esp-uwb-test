# ESP32-S3 UWB testing suite

## Summary

Create a standalone ESP-IDF project inside `esp-uwb-test`, with firmware for XIAO ESP32-S3 and ESP32-S3 DevKitC boards, plus a Python terminal wizard for operating up to five DWM3000EVBs.

The wizard will select test stages, collect experiment metadata, print measurements and diagnostics, and automatically save timestamped experiment files on the laptop. USB provides initial setup and baseline measurements; Wi-Fi supports tests beyond USB cable reach.

Confirmed choices: ESP-IDF **6.1-rc1**, laptop storage, terminal interface, UWB diagnostics only, and bench testing without drone integration.

## Firmware and hardware integration

- Use the Qorvo DW3xxx driver source with an ESP-IDF hardware abstraction layer. Pin the selected source revision and preserve its licence. Implement working DW3110 communication and DS-TWR, rather than placeholder radio functions.
- Review the existing ESP-IDF integration in [libdeca](https://github.com/br101/libdeca) as a porting reference; explicitly verify compatibility with ESP-IDF 6.1-rc1.
- Provide selectable XIAO and DevKitC profiles and configurable GPIO overrides. Default to no PSRAM dependency and a partition layout that fits a 4 MB flash device.
- Use these proposed GPIO assignments, supported by both boards’ exposed pins:

  | DWM3000EVB signal | ESP32-S3 GPIO | XIAO label |
  |---|---:|---|
  | SPI CLK | 7 | D8 |
  | SPI MISO | 8 | D9 |
  | SPI MOSI | 9 | D10 |
  | SPI CSn | 4 | D3 |
  | IRQ | 5 | D4 |
  | RSTn | 6 | D5 |
  | WAKEUP | 2 | D1 |

- Document EVB connector orientation and J1 power selection from the supplied guide, including its separate 3.3 V and onboard-regulator paths. Verify the physical header mapping before first wiring. Use open-drain reset handling and leave reserved signals unconnected.
- Configure conservative SPI speeds for jumper wiring, with a slow initialization clock and configurable operating clock.
- Use one radio task to own SPI/radio state. GPIO interrupts wake that task; ranging uses the DW3110’s hardware timestamps and delayed transmission.
- Buffer telemetry separately from radio work. Record buffer overflow, failed transmissions, timeouts and resets explicitly.
- Store node identity and configuration in NVS. Runtime roles: initiator/tag, responder/anchor and packet-test transmitter/receiver.
- Provide USB Serial/JTAG and UART-console configurations to accommodate the boards’ different USB connectors.

Hardware references: [Seeed XIAO pinout](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/), [Espressif DevKitC documentation](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/), and the supplied guide. :codex-file-citation{path="C:\Users\yikch\OneDrive - National University of Singapore\projects_hackathon\SAFMC\DWM3000EVB Quick Start Guide.pdf" purpose="source"}

## Test stages and terminal workflow

The Python wizard will discover connected nodes, show firmware/configuration, select participating devices and roles, prompt for ground truth, create a run folder, and start or stop the experiment. A noninteractive command-line interface will support repeatable runs using saved configurations.

| Stage | Implemented behaviour |
|---|---|
| Hardware checks | Device ID, initialization, reset recovery, firmware information and diagnostic availability. |
| Packet link | Numbered TX/RX packets; transmitted, received, duplicate, CRC-error and timeout counts. |
| Two-node ranging | DS-TWR with raw hardware timestamps, measured distance, exchange duration and status. |
| Static distance sweep | Guided distance stations; repeatable sample counts; raw and calibrated results. |
| Orientation and obstruction | Guided orientation/material labels; ranges, diagnostics and optional CIR capture. |
| Range and rate sweep | User-selected distances and request rates; completion rate, actual update interval and measurement age. |
| Four-anchor positioning | Sequential addressed ranging to four anchors; laptop weighted nonlinear least-squares position estimate. |
| Five-node scheduling | One initiator at a time, bounded exchanges and explicit peer IDs; star and sequential all-pairs test schedules. |
| Motion recording | Continuous measurements with event markers and optional reference coordinates. |
| Electronics coexistence | Labelled runs with ESP32 Wi-Fi disabled/enabled and other manually identified electrical conditions. |

- Default two-node measurement rate: 10 attempted exchanges/s; static station duration: 1,000 attempts. Both configurable.
- Channel 5 baseline; Channel 9 selectable with matching settings on all participants. Record the full radio configuration in each run.
- Four-anchor positioning initially assumes a known tag height and surveyed 3D anchor coordinates. Use slant ranges correctly; detect insufficient observations and poor geometry.
- All-pairs five-node testing covers ten unique pairs. Report aggregate rate and each pair’s rate separately.
- Motion without independent ground truth produces continuity, timing and dropout statistics, not claimed dynamic-position accuracy.
- Calibration remains reversible: preserve raw ranges, store calibration parameters separately, and evaluate corrections on separate measurements.

## Permanent logging and interfaces

- Implement a versioned, newline-delimited JSON command/event protocol shared by USB serial and Wi-Fi TCP. Include command acknowledgements, run/node IDs, boot IDs, sequence numbers and device monotonic timestamps.
- Provision Wi-Fi through USB. Support an existing access point; record transport and Wi-Fi state because they affect comparison runs.
- Open log files successfully before starting measurement. Create a unique directory under `esp-uwb-test/logs` for every run, without overwriting earlier runs.
- Save:
  - `manifest.json`: stage, node/board information, firmware/driver versions, radio settings, wiring profile, ground truth, annotations and calibration.
  - `events.jsonl`: received events, command results, failures, disconnects and experiment markers.
  - `measurements.csv`: ranges, diagnostics, timing, validity and ground-truth fields.
  - `positions.csv`: calculated positions and errors when positioning is enabled.
  - `summary.json` and `report.md`: statistics and interpretation limits.
  - Optional binary CIR data with an index linking each capture to its measurement.
- Print every measurement’s distance and selected diagnostics to the terminal by default, alongside failures and stage progress. Permit a compact display mode while retaining full file logging.
- Preserve device and laptop timestamps separately. Host arrival time will not be presented as the exact RF measurement time or precise one-way network latency.
- Flush logs regularly, sync them to disk at least once per second, and finalize cleanly on stop/Ctrl+C. Preserve readable partial runs after interruption; report disk errors and missing sequence numbers.
- Laptop connection is required for durable capture. A bounded ESP32 queue absorbs short delays; disconnects and overflow produce explicit gaps rather than a promise of offline storage.
- Compute signed bias, MAE, RMSE, standard deviation, p95/p99 absolute error, range completion ratio, update intervals and longest dropout. Keep failed attempts in the denominator.
- Log available chip temperature, supply-voltage estimate, clock offset, receive/first-path diagnostics and STS quality when applicable. Unsupported fields remain explicitly unavailable.
- CIR is optional and disabled by default; capture at a separately configured rate and measure its effect on ranging throughput.

## Validation and delivery

- Build both board profiles with ESP-IDF 6.1-rc1 and verify USB/UART configurations.
- Test timestamp wraparound and DS-TWR calculations using known synthetic inputs; test malformed commands, duplicate events, restart detection and bounded queues.
- Test laptop parsing, statistics, calibration and positioning using deterministic datasets with missing ranges, biased observations and poor anchor geometry.
- Include clearly labelled simulated nodes so the terminal wizard, logging and reports can be exercised before hardware is connected.
- Test disconnect/reconnect, Ctrl+C, partial logs and write failures; verify measurements shown in the terminal match saved records.
- Document hardware acceptance sessions: device-ID check, two-node ranging at measured distances, all five radios, four-anchor positioning, and USB/Wi-Fi comparison. Report these as pending until performed on the physical boards.
- Keep firmware, Python tools, dependencies, tests, wiring instructions, VS Code tasks and generated logs inside `esp-uwb-test`. Exclude credentials, logs and build outputs from version control.
- Deliver a beginner-oriented README covering wiring, selecting the board profile, build/flash, node setup, test selection and finding the saved results.

No camera/ToF drivers, PX4 connection, flight control, AoA, offline SD storage or 25-node performance claims are included in this version.
