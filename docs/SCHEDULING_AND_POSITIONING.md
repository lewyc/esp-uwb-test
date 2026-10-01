# Scheduling and positioning follow-up

## Timing that is already recorded

Each measurement keeps firmware-reported exchange_ms and a separate host arrival
time. The summary calculates mean, P95 and longest host-observed update intervals.
These values answer different questions:

- exchange_ms: duration of the radio state-machine exchange on the initiating node
- update interval: end-to-end interval including host command, USB/Wi-Fi transport,
  acknowledgements, Python scheduling and logging

Do not describe the host-observed update rate as RF airtime capacity.

## Device-side scheduling path

The current bench mode deliberately sends one host command per range. Before
flight-like motion tests, add a nonblocking firmware schedule state machine rather
than a long blocking command:

1. A versioned start_schedule command sets peer list, bounded attempt count,
   minimum interval and schedule ID.
2. The radio task remains the sole DW3110 owner and advances at most one exchange
   per task iteration.
3. stop cancels immediately; every attempted exchange still emits a success or
   failure measurement.
4. Schedule start, completion, overrun, queue overflow and cancellation events are
   logged with the same run ID.
5. The host starts a bounded schedule once, then only receives/logs events.
6. Star scheduling is implemented before all-pairs/TDMA; collision behavior is
   tested on the bench before any moving-platform test.

This design can later be driven by an onboard ESP32 or companion computer without
PX4 flight control. It is intentionally not implemented as an uninterruptible loop
in this release. Host-driven mode remains the reference bench implementation.

## Position solver modes

The host retains weighted nonlinear least squares as the comparison baseline.
Set robust_positioning to true in an anchors configuration to enable Huber IRLS;
huber_delta_m controls the residual transition. Per-anchor sigma_m remains the
base range uncertainty, so a future quality model can supply uncertainty derived
from held-out calibration data. Robust fitting can reduce the effect of an
outlier, but cannot fix poor anchor geometry or replace independent ground truth.
