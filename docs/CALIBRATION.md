# Multi-distance calibration

This workflow characterizes software range correction. It does **not** automatically
calibrate the DW3000 TX/RX antenna-delay registers, and it never rewrites raw ranges.

## Equipment and setup

- Two flashed ESP32-S3/DWM3000EVB nodes
- A rigid tape measure, laser distance meter, or surveyed floor marks
- Stands or tape that prevent either antenna from moving during a station
- A way to keep antenna height and orientation unchanged

Measure between repeatable antenna reference points, not PCB edges. Record the
uncertainty of that reference measurement. A rough or guessed separation is not
surveyed ground truth and must be described as such in station notes.

Use at least two well-separated fitting distances for scale plus offset. Prefer
three or more fitting distances across the intended operating range, plus one or
more separate validation distances that are not used for fitting.

## Interactive command

From the project directory:

~~~powershell
python tools/uwb_test.py calibrate serial:COM11 serial:COM12
~~~

Wi-Fi endpoints use the same flow:

~~~powershell
python tools/uwb_test.py calibrate tcp:192.168.1.31:8765 tcp:192.168.1.32:8765
~~~

Useful explicit settings:

~~~powershell
python tools/uwb_test.py calibrate serial:COM11 serial:COM12 --channel 5 --attempts 1000 --rate-hz 10 --timestamp-mode ipatov_adjusted --tx-delay 16385 --rx-delay 16385
~~~

The host creates the run folder before configuring or measuring. It then:

1. Configures both nodes and refuses to proceed unless both report ready and
   confirm the requested channel and timestamp mode.
2. Asks for a fixed number of stations, or 0 to add stations until finished.
3. For each station, asks for measured distance, uncertainty, label, notes,
   fitting/validation role, attempts, and warm-up count.
4. Waits while the nodes are placed, then prints each range and live
   attempted/successful/failed/remaining counts.
5. Stops ranging before asking for the next physical placement.
6. Reviews all completed stations and optionally corrects metadata. Corrections
   are saved separately and never alter events.jsonl.
7. Fits baseline, constant-offset, and—when supported by multiple distances—
   scale-plus-offset models. Held-out validation observations are compared on the
   same raw samples.

Press Ctrl+C during a batch to preserve all samples already synced, mark the
dataset partial, review completed stations, and produce an exploratory report.
Between stations, type finish to stop cleanly. The current serial command loop
does not offer a mid-exchange pause; station boundaries are the safe pause points.

## Outputs

Each logs/<run-id>/ directory contains the normal manifest, event stream and
measurement files plus:

- calibration_stations.json: reviewed station metadata
- calibration_result.json: coefficients, fitting/validation metrics and warnings
- calibration_report.md: human-readable model and filter comparison
- filter_analysis.json: median, Hampel, and diagnostic-availability results

events.jsonl is the immutable received event stream. measurements.csv keeps
raw_range_m, failures, station metadata, timestamp mode, raw timestamps, and
warm-up/exclusion flags. Software-corrected values belong in derived results.

## Interpreting results

Training improvement alone is not evidence that calibration generalizes. Prefer
the model with better independent-validation error and inspect per-station bias,
MAE, RMSE, standard deviation, median absolute error, P95/P99 absolute error and
completion. Runs without a validation station are labelled training_exploratory.

The offline median and Hampel results are comparisons, not new raw measurements.
Quality weighting is reported only where both total and first-path receive power
exist. Missing diagnostics remain unavailable, and no hard RSSI cutoff is applied.
Choose final filter thresholds on development data, then evaluate them on a
separate dataset.

## Timestamp-mode comparison

Run three separate calibration sessions at identical distances, antenna height,
orientation, channel, PHY settings, delays and attempt counts:

1. --timestamp-mode ipatov_adjusted
2. --timestamp-mode standard_adjusted
3. --timestamp-mode raw_unadjusted

Do not move the fixtures between mode runs. Compare independent-validation
statistics and completion rather than training error alone. The selected mode is
stored in firmware NVS, every measurement, and the manifest. It is also encoded
in each ranging frame; nodes configured to different modes reject each other's
exchange instead of combining unlike timestamps.

Qorvo API semantics used by the firmware:

- dwt_readrxtimestamp_ipatov: Ipatov-adjusted RX time
- dwt_readrxtimestamp with DWT_COMPAT_NONE: standard adjusted RX time
- dwt_readrxtimestampunadj: raw/unadjusted RX time
- dwt_readtxtimestamp: TX time adjusted by the configured TX antenna delay

Raw/unadjusted RX timestamps are intentionally not equivalent to adjusted RX
timestamps. Do not interpret their software offset as a replacement antenna-delay
register value.
