# UWB test progress log

This document records observed bench-test results for the ESP32-S3 and DWM3000EVB setup. It distinguishes radio initialization from ranging performance: a successful hardware stage confirms communication with the DW3110, but does not yet demonstrate packet exchange or distance-measurement accuracy.

## Current status

| Area | Status | Evidence |
|---|---|---|
| ESP32-S3 DevKitC firmware boot | Passed after recovery fix | Flash/monitor output and hardware run |
| XIAO ESP32-S3 firmware boot | Passed | Flash/monitor output and hardware run |
| DW3110 SPI probe | Passed on both boards | Device ID `0xDECA0302` |
| Hardware-stage endpoint discovery | Passed | Run `20260918-142236-hardware-58071f1e` |
| Two-node packet exchange | Passed in a 1,000-packet run | `1,000/1,000` received; no duplicates |
| DS-TWR distance measurement | Completed, but accuracy is poor | `1,000/1,000` successful at a rough 0.7 m |
| Static-distance accuracy | Two rough 0.7 m results captured | Surveyed multi-distance calibration and repeatability remain pending |

## Firmware flash/monitor observations

### Initial failures

- An early radio boot reported `status: "probe_failed"` with `probe_raw_id: 0`.
- A subsequent firmware version entered a reboot loop and produced a `LoadProhibited` panic in `dwt_xfer3xxx()` while `dwt_checkidlerc()` was called during initialization.
- The initialization sequence was corrected so `dwt_initialise()` completes before the idle-RC check. The XIAO HAL was also updated for its alternate pins and CS-based wake-up.

### Successful DevKitC boot

- `status`: `ok`
- Board: `esp32s3_devkitc`
- Probe clock: 2 MHz
- Probe attempts: 1
- Raw device ID: `3737780994` decimal = `0xDECA0302`
- Pins: SPI CLK 7, MISO 8, MOSI 9, CS 4, IRQ 5, RSTn 6, WAKEUP 2
- Pin levels after boot: reset 1, IRQ 1, MISO idle 1, CS idle 1
- This confirms the expected DW3110 response over the DevKitC wiring.

### Successful XIAO ESP32-S3 boot

- `status`: `ok`
- Board: `xiao_esp32s3`
- Probe clock: 2 MHz
- Probe attempts: 1
- Raw device ID: `3737780994` decimal = `0xDECA0302`
- Pins: SPI CLK 7 (`D8`), MISO 8 (`D9`), MOSI 9 (`D10`), CS 44 (`D7`), IRQ 1 (`D0`), RSTn 2 (`D1`)
- WAKEUP: `-1` / physically unconnected; firmware uses the CS-based wake-up path
- Pin levels after boot: reset 1, IRQ 1, MISO idle 1, CS idle 1
- This confirms the expected DW3110 response over the XIAO wiring.

## Hardware-stage run

Run directory: [`20260918-142236-hardware-58071f1e`](../logs/20260918-142236-hardware-58071f1e/)

Run timestamp: `2026-09-18 14:22:36` Singapore time (`2026-09-18T06:22:36Z` in the manifest)

### Run configuration

- Stage: `hardware`
- Endpoints: `serial:COM11` and `serial:COM12`
- Transport: USB for both endpoints
- Configured nodes: 2
- UWB channel: 5
- SPI operating speed: 8 MHz
- Preamble: 128 symbols, code 9
- Data rate: 6.8 Mbps
- STS: off
- CIR capture: disabled (`0 Hz`)
- Requested attempts: 1,000, although the hardware stage itself performs no ranging attempts

### Node results

| Node | Board | Radio | Device ID | Temperature | Voltage | Heap free | Result |
|---:|---|---|---|---:|---:|---:|---|
| 1 / COM11 | ESP32-S3 DevKitC | DW3110 | `0xDECA0302` | 30.4 °C | 3.201 V | 215,308 bytes | `radio_ready: true` |
| 2 / COM12 | XIAO ESP32-S3 | DW3110 | `0xDECA0302` | 33.55 °C | 3.326 V | 215,844 bytes | `radio_ready: true` |

### Run outcome

- Stage status: `complete`
- Hardware acknowledgements: 8
- Device resets/restarts detected by the host: 2
- Device-reported dropped events: 0
- TX packets: 0
- RX packets: 0
- RX errors: 0
- CRC errors: 0
- RX timeouts: 0
- Duplicate packets: 0
- Range attempts: 0
- Successful ranges: 0
- Completion ratio: 0
- Position estimates: 0

The run therefore verifies endpoint discovery, firmware metadata exchange, SPI/radio readiness, pin profiles and expected device identification. It does not provide evidence about UWB link reliability, range, latency, distance accuracy or two-node DS-TWR performance.

## Packet-link run

Run directory: [`20260918-143310-packet-16479a6a`](../logs/20260918-143310-packet-16479a6a/)

Run timestamp: `2026-09-18 14:33:10` Singapore time (`2026-09-18T06:33:10Z` in the manifest)

### Configuration

- Stage: `packet`; endpoints: `serial:COM11` (DevKitC, node 1) and `serial:COM12` (XIAO ESP32-S3, node 2)
- USB transport on both endpoints; Wi-Fi disabled
- 1,000 requested packets at a configured rate of 10 Hz
- Channel 5; 128-symbol preamble, code 9; 6.8 Mbps; STS off; CIR disabled
- Both radios reported `0xDECA0302` and `radio_ready: true`

### Results

| Metric | Result |
|---|---:|
| Packets transmitted | 1,000 |
| TX accepted | 1,000 / 1,000 (100%) |
| Packets received | 1,000 / 1,000 (100%) |
| Duplicates | 0 |
| Packet exchange duration | 0.876 ms mean; 0.883 ms p95 |
| Host-observed TX interval | 157.2 ms mean; 193.0 ms p95; 281.8 ms maximum |
| Host-observed event rate | 6.37 packets/s |

Interpretation:

- This is a successful basic two-node packet-delivery run: every transmitted packet generated one corresponding receive event, with no duplicate events recorded.
- The run supports link continuity at the tested bench setup and radio configuration. It does not establish performance at longer distance, with obstruction, during motion, or with multiple simultaneous nodes.
- The configured 10 Hz rate was not achieved in the host-observed event stream: 1,000 packets took about 157.1 s (`~6.37 Hz`). The ~0.88 ms radio exchange itself is short; the slower interval is dominated by the current scheduling/serial workflow and should not be treated as RF airtime capacity.
- The summary does not contain independent CRC-error or timeout events. Therefore, “100% received” should be reported as observed packet-event delivery, not as proof that every possible RF failure mode was exercised.

## Two-node ranging run

Run directory: [`20260918-144134-range-ec2bfc8d`](../logs/20260918-144134-range-ec2bfc8d/)

Run timestamp: `2026-09-18 14:41:34` Singapore time (`2026-09-18T06:41:34Z` in the manifest)

### Configuration

- Stage: `range`; node 1 was the ESP32-S3 DevKitC on `COM11`, node 2 was the XIAO ESP32-S3 on `COM12`; USB transport on both
- The manifest recorded `true_distance_m=1.0 m`, but the physical antenna-to-antenna separation was later corrected by the operator to a rough estimate of `0.7 m`. The statistics below are recomputed using `0.7 m`; the distance was not surveyed.
- 1,000 DS-TWR attempts at a configured request rate of 10 Hz; channel 5
- 128-symbol preamble, code 9; 6.8 Mbps; STS off; CIR disabled; no calibration offset applied
- Both radios reported `0xDECA0302` and `radio_ready: true`

### Results

| Metric | Result |
|---|---:|
| Successful ranges | 1,000 / 1,000 (100% completion) |
| Mean measured range | 0.984 m |
| Signed bias | +0.284 m (+28.4 cm) |
| Standard deviation | 0.229 m |
| MAE / RMSE | 0.300 m / 0.365 m |
| Median measured range | 0.969 m |
| Observed range span | 0.402–1.779 m |
| P95 / P99 absolute error | 0.698 m / 0.860 m |
| Samples within 10 / 20 / 50 cm | 19.5% / 38.9% / 82.2% |
| DS-TWR exchange duration | 13.166 ms mean; 13.176 ms p95 |
| Host-observed update interval | 193.5 ms mean; 207.2 ms p95 |
| Longest observed gap | 295.9 ms |
| Mean RX / first-path power | -76.8 / -77.7 dBm |

Interpretation:

- Radio communication and the DS-TWR state machine were operational for all 1,000 attempts. There were no failed range exchanges in this run, so completion/reliability at this rough 0.7 m bench setup was excellent.
- Using the corrected rough 0.7 m reference, accuracy was not acceptable for treating an individual raw measurement as a position constraint. The mean was approximately 28 cm too long, with approximately 30 cm MAE and 37 cm RMSE.
- The error contains both a substantial systematic component and large variability. A calibration offset may reduce the approximately 28 cm mean bias, but it will not by itself remove the approximately 23 cm standard deviation or the large outliers.
- The configured 10 Hz request rate produced approximately 5.17 host-observed measurements/s (`1 / 0.1935 s`). The 13.2 ms DS-TWR exchange is not the same as the end-to-end update interval; host scheduling, command acknowledgement and USB logging add most of the interval.
- The first 100 samples averaged `0.891 m` versus `0.987 m` for the final 100. This run should therefore not yet be treated as stationary repeatability data; verify the physical setup, antenna orientation, distance reference and startup/transient behavior in repeated runs.
- CIR capture was disabled and STS was off. The run is therefore a baseline for the current configuration, not a comparison of filtering, NLOS rejection, CIR features or secure ranging.

### Current conclusion

The two boards can exchange packets reliably and complete DS-TWR consistently over USB-controlled bench tests. However, the present raw ranging output at a rough 0.7 m separation is too biased and noisy for direct use in high-precision localization or formation control. Before integrating it into a swarm estimator, repeat the test with a surveyed antenna-to-antenna distance, several distances, fixed orientation, warm-up time and raw-data filtering/calibration comparisons. Preserve this uncalibrated run and its corrected-distance interpretation as the baseline.

## Static-distance run: rough 0.7 m

Run directory: [`20260918-153302-static-728f8320`](../logs/20260918-153302-static-728f8320/)

Run timestamp: `2026-09-18 15:33:02` Singapore time (`2026-09-18T07:33:02Z` in the manifest)

### Configuration

- Stage: `static`; one station at a user-entered rough distance estimate of `0.7 m` (not surveyed)
- Node 1: ESP32-S3 DevKitC on `COM11`; node 2: XIAO ESP32-S3 on `COM12`; USB transport on both
- 1,000 DS-TWR attempts at a configured request rate of 10 Hz; channel 5
- 128-symbol preamble, code 9; 6.8 Mbps; antenna delay `16385`; STS off; CIR disabled
- Both radios reported `0xDECA0302` and `radio_ready: true`

### Results

| Metric | Result |
|---|---:|
| Successful ranges | 1,000 / 1,000 (100% completion) |
| Mean measured range | 0.971 m |
| Median measured range | 0.950 m |
| Signed bias | +0.271 m (+27.1 cm) |
| Standard deviation | 0.131 m |
| MAE / RMSE | 0.275 m / 0.300 m |
| Observed range span | 0.453–1.866 m |
| P95 / P99 absolute error | 0.530 m / 0.657 m |
| Samples within 10 / 20 / 50 cm | 3.5% / 20.4% / 93.0% |
| DS-TWR exchange duration | 13.165 ms mean; 13.174 ms p95 |
| Host-observed update interval | 194.4 ms mean; 215.3 ms p95 |
| Longest observed gap | 288.4 ms |
| Mean RX / first-path power | -77.2 / -80.9 dBm |

### Interpretation

- The radio link and DS-TWR exchange remained reliable: all 1,000 attempts produced valid measurements.
- Assuming the true antenna-to-antenna distance really was 0.7 m, the range estimate has a large positive systematic error. The mean was approximately 0.27 m too long, and the median was approximately 0.25 m too long.
- The random spread was lower than in the earlier range run (0.131 m versus 0.229 m standard deviation), but the overall accuracy was better in RMSE terms because the positive bias was slightly smaller.
- The first 100 samples averaged approximately 1.127 m; the final 100 averaged approximately 0.927 m. Discarding the first 100 samples would still leave approximately +0.253 m bias and 0.272 m RMSE, so warm-up alone does not explain the result.
- Because the station distance was described as “roughly” 0.7 m rather than surveyed antenna-to-antenna, the result cannot yet distinguish antenna-delay/setup error from firmware/radio bias. Measuring board edges or enclosure centers instead of antenna phase centers could produce a large apparent bias.
- CIR capture was disabled and STS was off, so this remains an uncalibrated baseline.

### Comparison with the two rough 0.7 m runs

| Run / physical reference | Completion | Mean range | Bias | MAE | RMSE | Range standard deviation |
|---:|---:|---:|---:|---:|---:|---:|
| Range run, rough 0.7 m | 100% | 0.984 m | +0.284 m | 0.300 m | 0.365 m | 0.229 m |
| Static run, rough 0.7 m | 100% | 0.971 m | +0.271 m | 0.275 m | 0.300 m | 0.131 m |

The two runs now show a similar positive bias of approximately 27–28 cm, although the spread differs. This is consistent with a possible systematic antenna-delay or physical-reference error, but the distance is only a rough estimate. The next calibration run must use surveyed antenna reference points and multiple distances under unchanged mounting and orientation.

## Next uncompleted tests

1. Repeat the two-node range stage at several surveyed distances with fixed antenna orientation and a warm-up period.
2. Compare raw, median/trimmed-mean and calibrated ranges while preserving all individual samples and diagnostic fields.
3. Exercise obstruction, orientation, request-rate and longer-distance cases; record explicit CRC and timeout counters where available.
4. Only then proceed to four-anchor positioning and multi-node scheduling tests.
