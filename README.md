# ESP32-S3 DWM3000EVB test suite

This folder is a standalone ESP-IDF 6.1-rc1 project and laptop test runner for up to five Qorvo DWM3000EVBs. It supports real DW3110 communication, packet tests, four-message double-sided two-way ranging (DS-TWR), guided experiments, four-anchor laptop positioning, durable logs and simulated nodes.

It is bench instrumentation only. It does not connect to PX4, control a drone, use the camera/ToF sensors, implement AoA or claim 25-node performance.

## Before you power anything

Read [the wiring guide](docs/WIRING.md). Confirm the DWM3000EVB header orientation and J1 selection against the supplied Quick Start Guide with power removed. RSTn is open-drain: the ESP32 drives it low but never drives it high.

Default signals are GPIO 7 CLK, 8 MISO, 9 MOSI, 4 CSn, 5 IRQ, 6 RSTn and 2 WAKEUP. All can be overridden in `menuconfig`.

## Software setup

1. Install ESP-IDF **6.1-rc1** and select that environment in the VS Code ESP-IDF extension.
2. Open this `esp-uwb-test` folder as the VS Code workspace.
3. Install the laptop dependency:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Exercise the laptop workflow before hardware:

   ```powershell
   python tools/uwb_test.py simulate --stage static --attempts 20
   python tools/uwb_test.py simulate --stage anchors --nodes 5 --attempts 20
   python -m unittest discover -s tests -v
   ```

## Build and flash

Run commands from this folder in an exported ESP-IDF 6.1-rc1 terminal.

XIAO ESP32-S3, native USB Serial/JTAG:

```powershell
idf.py -B build-xiao -D SDKCONFIG=sdkconfig.xiao -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;profiles/xiao.defaults" build
idf.py -B build-xiao -D SDKCONFIG=sdkconfig.xiao flash monitor
```

ESP32-S3 DevKitC using its USB-to-UART connector:

```powershell
idf.py -B build-devkitc -D SDKCONFIG=sdkconfig.devkitc -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;profiles/devkitc.defaults;profiles/uart.defaults" build
idf.py -B build-devkitc -D SDKCONFIG=sdkconfig.devkitc flash monitor
```

Add `-p COM6` (using your actual port) to flash/monitor if automatic detection is ambiguous. The same actions are available under **Terminal > Run Task** in VS Code.

The firmware fits a 4 MB flash partition layout and does not require PSRAM. It uses a slow SPI initialization clock and a conservative configurable operating clock for jumper leads.

## First radio session

1. Wire and power one ESP32/EVB pair.
2. Flash it, open the monitor, and confirm the boot event.
3. Discover laptop endpoints:

   ```powershell
   python tools/uwb_test.py discover
   ```

4. Start the wizard:

   ```powershell
   python tools/uwb_test.py wizard
   ```

5. Select `hardware`, enter an endpoint such as `serial:COM6`, and confirm the DW3110 ID `0xDECA0302`.
6. Repeat for each of the five radios.
7. Connect two nodes, select `range`, measure the antenna-to-antenna distance, and begin with Channel 5 at 10 attempts/s.

Use `serial:COM6` for USB/UART or `tcp:192.168.1.50:8765` for Wi-Fi. Node identity, channel, antenna delay and Wi-Fi settings persist in NVS. Provision Wi-Fi over USB without saving the password on the laptop:

```powershell
python tools/uwb_test.py provision serial:COM6 --ssid YOUR_ACCESS_POINT
```

## Test stages

| Stage | Result |
|---|---|
| `hardware` | device ID, init, diagnostic support and reset recovery |
| `packet` | numbered packet TX/RX, duplicate, CRC, timeout and failure counters |
| `range` | DS-TWR timestamps, range, exchange duration and diagnostics |
| `static` | guided measured-distance stations, raw and calibrated results |
| `orientation` | labelled antenna-angle runs |
| `nlos` | labelled obstruction/material runs |
| `rate` | request-rate and timing/completion comparison |
| `anchors` | sequential ranges and weighted nonlinear least-squares 2D position at known height |
| `schedule` | sequential five-node/all-pairs schedule |
| `motion` | continuous timing/dropout recording and event context |
| `coexistence` | labelled Wi-Fi/electronics conditions |

The default static test is 1,000 attempts per station at 10 attempted exchanges/s. CIR is disabled by default because it changes throughput.

For repeatable noninteractive runs, copy a JSON file under `configs` and run:

```powershell
python tools/uwb_test.py run configs/static.example.json
```

## Results

The laptop opens the run directory before commanding measurements. Every run gets a non-overwriting folder under `logs/YYYYMMDD-HHMMSS-stage-id` containing:

- `manifest.json`: configuration, participants and annotations
- `events.jsonl`: complete device/host event stream
- `measurements.csv`: ranges, raw values, diagnostics, ground truth and validity
- `positions.csv`: anchor-position solutions and error where truth exists
- `summary.json` and `report.md`: completion and error statistics
- `cir.bin` and `cir_index.csv`: optional indexed CIR captures

Logs flush and sync at least once per second and finalize on Ctrl+C. The ESP32 queue only absorbs short delays; overflow/disconnect/restart is recorded as an explicit gap. Host receipt time remains separate from radio and device time.

Reported statistics include signed bias, MAE, RMSE, standard deviation, p95/p99 absolute error, completion ratio, update intervals, longest dropout and per-pair results. Failed attempts remain in the denominator. Calibration never overwrites raw ranges.

## Source and status

The Qorvo driver snapshot is pinned in `components/dw3xxx/driver.lock.json` and `components/dw3xxx/upstream/REVISION`; its original licences are preserved. The ESP-IDF HAL and test firmware are project code. [Protocol details](docs/PROTOCOL.md) describe command/event fields.

Automated builds and simulated host tests verify software integration. Physical radio acceptance remains pending until you run the sessions in [the hardware checklist](docs/HARDWARE_ACCEPTANCE.md); software-only success must not be reported as measured RF performance.
