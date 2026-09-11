# Hardware acceptance sessions

All sessions below are pending until performed with the physical boards. Record the operator, antenna-to-antenna ground truth, supply arrangement, environment and photos in run annotations.

## A. One-node electrical and identity check

- [ ] J1 and connector orientation independently checked
- [ ] 3.3 V rail checked before attaching the radio
- [ ] XIAO profile boots and reports `0xDECA0302`
- [ ] DevKitC UART profile boots and reports `0xDECA0302`
- [ ] reset command recovers the radio repeatedly
- [ ] IRQ counter advances during packet reception
- [ ] invalid or disconnected SPI produces a clear failure

## B. Two-node DS-TWR acceptance

- [ ] all five EVBs individually serve as initiator and responder
- [ ] 0.5 m, 1 m, 2 m and 5 m stations measured antenna-to-antenna
- [ ] 1,000 attempts per station at 10 attempts/s
- [ ] raw timestamps, failures and diagnostics present in saved files
- [ ] Channel 5 baseline complete
- [ ] Channel 9 comparison complete with both nodes matched
- [ ] calibration fitted on one dataset and evaluated on a separate dataset

## C. Orientation, obstruction, range and rate

- [ ] 0°, 90° and 180° antenna orientation runs
- [ ] clear line of sight, person, wall/glass and corner labels tested where safe
- [ ] range extended in measured steps until completion becomes unacceptable
- [ ] 1, 5, 10 and 20 attempted exchanges/s compared
- [ ] optional CIR run compared against CIR-disabled throughput

## D. Four-anchor positioning

- [ ] anchor antenna phase centres surveyed in 3D
- [ ] tag antenna height recorded
- [ ] central and edge points tested
- [ ] poor/collinear geometry deliberately tested and rejected
- [ ] position results compared only where independent ground truth exists

## E. Five-node scheduling and transport

- [ ] star schedule completed
- [ ] all ten unique pairs completed and reported separately
- [ ] USB baseline compared with Wi-Fi TCP using the same radio conditions
- [ ] Wi-Fi enabled/disabled coexistence runs completed
- [ ] cable disconnect/reconnect creates explicit logged gaps
- [ ] Ctrl+C leaves readable partial logs

Acceptance thresholds are deliberately not hard-coded. Establish distributions first, then set project thresholds using SAFMC geometry, update-rate needs and observed failure modes.

