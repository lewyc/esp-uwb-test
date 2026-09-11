# DWM3000EVB wiring

Stop and verify the EVB header labels and J1 jumper before applying power. The table below follows the DWM3000EVB Quick Start Guide and the exposed-pin plan for both supported ESP32-S3 boards.

## Signal wiring

| DWM3000EVB signal | ESP32-S3 GPIO | XIAO label | Direction at ESP32 |
|---|---:|---|---|
| SPI CLK | 7 | D8 | output |
| SPI MISO | 8 | D9 | input |
| SPI MOSI | 9 | D10 | output |
| SPI CSn | 4 | D3 | output |
| IRQ | 5 | D4 | input |
| RSTn | 6 | D5 | open-drain output/input |
| WAKEUP | 2 | D1 | output |
| GND | GND | GND | power return |

For loose jumper wires, use the signal names printed beside the headers. In the guide's top-down view (antenna at the top), `CON1` is the right-side SPI header. From top to bottom it is: `NC`, `TCXO_EN` (reserved), `NC`, `GND`, `SPI CLK`, `SPI MISO`, `SPI MOSI`, `SPI CSn`, `WAKEUP`, `IRQ`. `RSTn` is the top pin of the adjacent `CON4` header. Do not mirror this order when viewing the underside.

Do not connect reserved EVB signals. Keep the SPI leads short and route ground beside them where possible. The firmware starts SPI slowly for reset and identification, then uses the configurable operating speed.

## Power and J1

The EVB has two mutually exclusive 3.3 V paths selected by J1:

| J1 silkscreen selection | Bench wiring |
|---|---|
| `3V3 Arduino` | ESP32 `3V3` to EVB `CON2 3V3` |
| `3V3 DC/DC` | ESP32/USB `5V` to EVB `CON2 5V`; the EVB regulator creates 3.3 V |

Never connect both the 3.3 V and 5 V supply paths. Select J1 by its PCB label, not by an assumed upper/lower or pin-number orientation. With power removed, confirm continuity from the selected J1 path to the expected supply pin. Power one ESP32/EVB pair first and measure approximately 3.3 V between `CON2 3V3` and `GND` before connecting signal wires or more nodes.

## Connector orientation check

1. Place the EVB with its printed antenna and connector designators readable.
2. Identify J1 and pin 1 from the silkscreen; do not infer orientation from a photo.
3. Match every wire to the EVB signal name, not just a header position.
4. Check GND-to-GND continuity.
5. Check that 3.3 V is not shorted to GND.
6. Leave RSTn pulled up by the EVB; the ESP32 only drives it low.
7. Power up and run the hardware stage. A DW3110 device ID of `0xDECA0302` is expected.

## Probe failure interpretation

At boot the firmware resets the radio, waits for `RSTn` to return high, and makes three 2 MHz device-ID reads. The expected raw little-endian bytes are `02 03 CA DE` (`0xDECA0302`).

- `reset_stuck_low`: EVB unpowered, RSTn connected incorrectly, reset shorted, or wrong GPIO.
- `probe_miso_held_low`: SPI calls succeeded but every returned bit was zero; check EVB power, MISO/MOSI orientation, CON1 orientation and common ground.
- `probe_miso_open_or_high`: MISO did not appear to be driven; check the MISO jumper, CSn, CLK, EVB power and common ground.
- `probe_unexpected_id`: the radio responded, but the bytes were wrong; shorten wires, recheck swapped SPI signals and use the 2 MHz initialization clock.
- `probe_spi_io_failed`: the ESP-IDF SPI transaction itself failed.

The `radio_boot` JSON also reports all three IDs and idle levels for `RSTn`, `IRQ`, `MISO` and `CSn`. A logic analyser, if available, should show CSn low, an 8-bit zero header on MOSI, and `02 03 CA DE` on MISO.

The XIAO and DevKitC expose these GPIO numbers differently, but firmware profiles use the same defaults. GPIO overrides are available under `idf.py menuconfig > UWB test suite`.

## Console choices

- XIAO: default USB Serial/JTAG console through its native USB connector.
- DevKitC: `profiles/uart.defaults` selects UART0 at 115200 baud for boards whose USB-to-UART connector is being used.
- A DevKitC with native USB connected can instead use the base defaults and USB Serial/JTAG.
