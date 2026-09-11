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

Do not connect reserved EVB signals. Keep the SPI leads short and route ground beside them where possible. The firmware starts SPI slowly for reset and identification, then uses the configurable operating speed.

## Power and J1

The EVB has two mutually exclusive 3.3 V paths selected by J1:

| J1 position | Guide description | Bench use |
|---|---|---|
| Pins 2–3, upper position | Arduino/header 3V3 path | Use only when the ESP32 board supplies a suitable regulated 3.3 V rail |
| Pins 1–2, lower position | EVB onboard 3.3 V DC/DC path | Use only with the EVB input supply arranged exactly as documented |

Never join both 3.3 V sources. With power removed, confirm continuity from the selected J1 path to the expected supply pin. Power one ESP32/EVB pair first and check the 3.3 V rail before connecting USB or more nodes.

## Connector orientation check

1. Place the EVB with its printed antenna and connector designators readable.
2. Identify J1 and pin 1 from the silkscreen; do not infer orientation from a photo.
3. Match every wire to the EVB signal name, not just a header position.
4. Check GND-to-GND continuity.
5. Check that 3.3 V is not shorted to GND.
6. Leave RSTn pulled up by the EVB; the ESP32 only drives it low.
7. Power up and run the hardware stage. A DW3110 device ID of `0xDECA0302` is expected.

The XIAO and DevKitC expose these GPIO numbers differently, but firmware profiles use the same defaults. GPIO overrides are available under `idf.py menuconfig > UWB test suite`.

## Console choices

- XIAO: default USB Serial/JTAG console through its native USB connector.
- DevKitC: `profiles/uart.defaults` selects UART0 at 115200 baud for boards whose USB-to-UART connector is being used.
- A DevKitC with native USB connected can instead use the base defaults and USB Serial/JTAG.

