# ctrlX CORE hardware: X2, X3, COREplus X3, X5, X7

Facts for planning and testing apps, in our own words, from the Bosch Rexroth operating instructions (retrieved
2026-11-04), plus what we measured on our own devices. For exact wording, drawings, wiring, safety and certification
read the manuals; they win over this page.

## Sources

| Document | Part number, edition | Type codes |
|---|---|---|
| ctrlX CORE X2 X3 Controls, Operating Instructions | R911405645, edition 08 (2025-03) | COREX-C-X2, COREX-C-X3 |
| ctrlX COREplus X3 Controls, Operating Instructions | R911420136, edition 06 (2025-09) | COREX-M-X3 |
| ctrlX COREplus X5 and X7 Controls, Operating Instructions | R911419122, edition 05 (2025-09) | COREX-M-X5, COREX-M-X7 |

* Catalog (all documents, all languages):
  <https://docs.automation.boschrexroth.com/iirds/cdp-metadata.boschrexroth.de~iiDC~Product-ctrlX-CORE/>
* X5/X7 manual online:
  <https://docs.automation.boschrexroth.com/doc/1566683380/ctrlx-coreplus-x5-and-x7-controls-operating-instructions/latest/en/>
* The catalog is rendered by JavaScript: a plain fetch returns only the words "Bosch Rexroth". Open it in a browser or
  headless Chromium (the kit's UI test venv). Each row has a "Download as PDF" button.
* `pdf/` holds local copies of the three manuals on the build VM. It is git-ignored: the manuals are all rights reserved,
  including reproduction and distribution, so they are not committed. On a fresh clone, download them again.
  Text of a manual: `python3 tools/pdf_check.py <file.pdf> --text`.

## Models

| | CORE X2 | CORE X3 | COREplus X3 | COREplus X5 | COREplus X7 |
|---|---|---|---|---|---|
| Snap architecture | arm64 | arm64 | arm64 | amd64 | amd64 |
| Processor | Zynq UltraScale+, 4 × ARM A53 | same | same | Intel Atom x6425RE, 4 cores | Intel Core i7-1185GRE, 4 cores |
| RAM | 2 GB | 2 GB | 2 GB | 8 GB | 16 GB |
| Internal flash | 4 GB eMMC | 4 GB eMMC | 4 GB eMMC | 16 GB | 32 GB |
| NVRAM (as printed) | 128 KB | 128 KB | 1 KB | 2 MB | 2 MB |
| Onboard Ethernet, 1 Gbit | 2 | 3 | 3 | 5 | 5 |
| USB-C, host only | not enabled | 1, USB 2.0 | 1, USB 2.0 | 3, USB 3.1 | 3, USB 3.1 |
| USB supply per port | - | 5 V, 0.5 A | 5 V, 0.5 A | 5 V, 0.9 A | 5 V, 0.9 A |
| Micro SD slot | SDXC, up to 2 TB | SDXC, up to 2 TB | SDXC, up to 2 TB | SDHC, up to 32 GB | SDHC, up to 32 GB |
| DisplayPort | - | - | - | 1 | 1 |
| Extension slots | none | none | 2 | 3 | 3 |

* Extension options: Multi-Ethernet master/slave 4 × RJ45, 80 GB SSD, AI accelerator (Hailo-8, 26 TOPS). The COREplus X3's
  slot 1 also takes Multi-Ethernet slave 2 × RJ45.
* The X5 and X7 share one manual and the same ports; they differ in processor, RAM and flash.
* The manuals allow only the SD cards sold as accessories; other cards are not guaranteed to work.

## Ethernet ports

Linux interface names are the printed names (measured on the X3 and X7).

| Port | Speed | X2 | X3, COREplus X3 | X5, X7 |
|---|---|---|---|---|
| XF10 | 1 Gbit | HMI + engineering | HMI + engineering | HMI + engineering |
| XF11, XF12 | 1 Gbit | - | - | HMI + engineering |
| XF50 | 1 Gbit | EtherCAT master | EtherCAT master | configurable |
| XF51 | 1 Gbit | - | configurable (TSN-capable on the X3) | - |
| XF52 | 1 Gbit | - | - | configurable |
| XF23, XF24, XF60-XF63 | 10/100 Mbit | - | extension modules, configurable (COREplus only) | XF60-XF63: extension modules, configurable |

## USB and media

* USB storage: **FAT16, FAT32 and EXT4 only**. Maximum cable length 3 m.
* Too much current switches a USB port off until the next power cycle.
* X3: low, full and high speed. X5/X7: up to 5 Gb/s; no DisplayPort or HDMI over USB-C, Thunderbolt, USB 3.2, USB4 or USB PD.

## Measured on our devices (2026-11-04)

`x3` and `x7` are the names in `devices.toml`. Our X3 is a plain CORE X3, not the plus variant (user, 2026-11-04).
Our X7 reports itself as "ctrlX COREplus X7". We have no X5 yet.

| | `x3` | `x7` |
|---|---|---|
| `uname -m`, kernel | aarch64, 6.12.40-ctrlx-59.2 | x86_64, 6.12.40-ctrlx-56 |
| Logical CPUs, threads per core | 4, 1 (max 1.2 GHz) | 4, 1 |
| CPU features | asimd, aes | sse4_2, aes, avx2, avx512f |
| CPUs for system services (`system.slice`) | 0-1 | 0-1 |
| Kernel `nohz_full`, `rcu_nocbs` | 2-3 | 2-3 |
| RAM: total, used, available | 1993, 541, 1451 MB | 15791, 3361, 12430 MB |
| Internal disk | mmcblk0, 3.6 GB | nvme0n1, 39.7 GB (manual: 32 GB) |
| `/writable`: size, free | 2.8 GB, 2.0 GB | 35 GB, 30 GB |
| Ethernet interfaces | XF10, XF50, XF51 | XF10, XF11, XF12, XF50, XF52 |
| `net.core.rmem_max` | 212992 | 212992 |
| XF10 driver, MTU now, max | macb, 1500, 10222 | igc, 1500, 9216 |
| XF10 interrupts | one, on CPU 0 | four queues, on CPUs 0-1 |
| Receiving a 5 MP GigE Vision frame, CPU (40 µs / no packet delay) | 92 / 38 ms | 9 / 6 ms |
| Same image processing | ~10 x slower | see `apps/ctrlx-barcode-reader/KNOWN-ISSUES.md` 22 |
| Other apps running | few | Docker, Grafana, InfluxDB, Node-RED, WebIQ, UMC, PLC, Motion, EtherCAT master |

## What this means for apps

* **The X3 is the tight device.** An app that fits its 2 GB RAM and 2 GB free flash fits everywhere; the reverse doesn't hold.
* **Apps get 2 of the 4 cores**, on the X3 and X7 (CPUs 0-1; 2-3 are kept for real time). Not measured on an X5.
* **Performance doesn't carry over between models.** A figure from the X7 says nothing about an X5 or X3, so name the
  model with every figure. Our X7 also runs many other apps: take a baseline before comparing it with the X3.
* **The X5 is not a small X7.** Port and USB results carry over; speed and CPU features don't. Read `/proc/cpuinfo` when
  one is available.
* **CPU features differ.** Native wheels can fail on a CPU below their baseline, as on the COREvirtual (`docs/ctrlx/gotchas.md` #98).
* **GigE devices such as cameras** need a 1 Gbit port. XF23, XF24 and XF60-XF63 are 10/100 Mbit. On the X2 and X3, XF50 is
  labelled as the EtherCAT master, which leaves XF10 and XF51 on an X3.
* **Removable-media features** must say which file systems work (no exFAT, no NTFS). The X3's USB 2.0 port limits how fast
  an app can store images.
* **Flash wears out.** The wear value is under Settings → Information → Resources → Sensors; the manuals say to back up above
  80 %, and that external memory prolongs its life. This backs the kit's "no cyclic disk writes" rule.
* **Extensions are optional.** Don't assume an SSD, an AI module or extra Ethernet ports.
* **Fan:** the X7 always needs its fan, the X5 above 50 °C. A missing or failed fan can shut the device down.
