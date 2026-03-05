# SpectralDNA Usage Guide

**RF Emission Fingerprinting Tool for HackRF One Pro**

SpectralDNA passively captures RF emissions across seven wireless protocols and
produces a composite spectral fingerprint — a unique signature derived from the
physical-layer characteristics of every transmitter within range.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Quick Start](#quick-start)
4. [CLI Reference](#cli-reference)
5. [Protocol Modules](#protocol-modules)
6. [Output Formats](#output-formats)
7. [Running Individual Modules](#running-individual-modules)
8. [Understanding the Output](#understanding-the-output)
9. [Hardware Notes](#hardware-notes)
10. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Hardware

- **HackRF One Pro** (or HackRF One) connected via USB
- Antenna appropriate for the bands you want to scan (wideband antenna recommended)

### Software

- **Windows 10/11** (tested), Linux, or macOS
- **Python 3.10+**
- **radioconda** (recommended) — includes hackrf_transfer, SoapySDR, and all
  required RF libraries. Download from https://github.com/ryanvolz/radioconda
- OR: hackrf tools + SoapySDR installed separately

### Drivers

On Windows, the HackRF must be accessible via WinUSB or libusb. If using
radioconda, the `hackrf_transfer` CLI is used by default and typically works
out of the box. Install Zadig (https://zadig.akeo.ie) if you need to switch
the USB driver to WinUSB.

---

## Installation

### Option A: Run directly (recommended)

No installation needed. From the `spectralDNA` directory:

```bash
# Activate radioconda (or ensure hackrf_transfer is on PATH)
export PATH="$HOME/radioconda/Library/bin:$HOME/radioconda:$HOME/radioconda/Scripts:$PATH"

# Run with radioconda Python
~/radioconda/python.exe -m spectral_dna --help
```

### Option B: pip install

```bash
cd spectralDNA
pip install -e .

# Then run as:
spectral-dna --help
# or:
python -m spectral_dna --help
```

### Dependencies

Listed in `requirements.txt`:

| Package   | Purpose                                   |
|-----------|-------------------------------------------|
| numpy     | Array processing, IQ sample manipulation  |
| scipy     | Signal processing (filters, correlation)  |
| rich      | Dark-themed terminal rendering            |
| click     | CLI argument parsing                      |
| scapy     | Wi-Fi frame dissection (optional)         |
| Jinja2    | HTML template rendering                   |

---

## Quick Start

### Full scan (all protocols)

```bash
python -m spectral_dna --target "Lab Test 1" -o fingerprint.json --html fingerprint.html
```

This will:
1. Open the HackRF
2. Scan all 7 protocols sequentially (Wi-Fi, BLE, LTE, 5G, UWB, TPMS, RKE)
3. Display a color-coded terminal readout
4. Export results to JSON and HTML
5. Print the composite SHA-256 SpectralDNA hash

### Scan specific protocols only

```bash
python -m spectral_dna -t "Quick BLE+WiFi" -p wifi -p ble -o quick.json
```

### Fast scan with shorter durations

```bash
python -m spectral_dna -t "Speed Run" \
  --wifi-duration 1.0 \
  --ble-duration 2.0 \
  --lte-duration 1.0 \
  --fiveg-duration 1.0 \
  --uwb-duration 1.0 \
  --tpms-duration 5.0 \
  --rke-duration 3.0 \
  -o fast.json --html fast.html
```

### Verbose output (debug logging)

```bash
python -m spectral_dna -t "Debug" -v
```

---

## CLI Reference

```
Usage: python -m spectral_dna [OPTIONS]

Options:
  -t, --target TEXT              Label for this capture session (e.g. person
                                 or location name).  [default: UNKNOWN]
  -o, --output TEXT              Export fingerprint as JSON to this path.
  --html TEXT                    Export fingerprint as styled HTML to this path.
  -p, --protocols [wifi|ble|lte|5g|uwb|tpms|rke]
                                 Protocol modules to run. Omit to run all.
                                 Can be specified multiple times.
  --wifi-duration FLOAT          Seconds per Wi-Fi channel.  [default: 2.0]
  --ble-duration FLOAT           Seconds per BLE advertising channel.  [default: 5.0]
  --lte-duration FLOAT           Seconds per LTE band.  [default: 3.0]
  --fiveg-duration FLOAT         Seconds per 5G NR frequency.  [default: 2.0]
  --uwb-duration FLOAT           Seconds per UWB channel.  [default: 3.0]
  --tpms-duration FLOAT          TPMS capture duration (seconds).  [default: 30.0]
  --rke-duration FLOAT           RKE capture duration (seconds).  [default: 60.0]
  --wifi-monitor-iface TEXT      Monitor-mode Wi-Fi interface for scapy
                                 passive capture (optional, Linux only).
  -v, --verbose                  Enable verbose (DEBUG) logging.
  -h, --help                     Show this help message and exit.
```

### Duration Guidelines

| Protocol | Default | Minimum Useful | Notes                                    |
|----------|---------|----------------|------------------------------------------|
| Wi-Fi    | 2.0 s   | 1.0 s          | Per channel (7 channels scanned)         |
| BLE      | 5.0 s   | 2.0 s          | Per advertising channel (3 channels)     |
| LTE      | 3.0 s   | 1.0 s          | Per band (5 bands: B2/B4/B12/B41/B71)    |
| 5G NR    | 2.0 s   | 1.0 s          | Per frequency (7 C-Band frequencies)     |
| UWB      | 3.0 s   | 1.0 s          | Per channel (Ch 5, Ch 9)                 |
| TPMS     | 30.0 s  | 5.0 s          | Single capture at 315 MHz                |
| RKE      | 60.0 s  | 3.0 s          | Single capture at 315 MHz                |

TPMS and RKE sensors broadcast infrequently, so longer durations increase the
chance of catching a transmission. For quick tests, 5 s and 3 s are fine.

---

## Protocol Modules

### Wi-Fi (2.4 GHz / 5 GHz)

| Detail         | Value                                         |
|----------------|-----------------------------------------------|
| Frequencies    | 2.412, 2.437, 2.462 GHz (ch 1/6/11)          |
|                | 5.180, 5.220, 5.745, 5.785 GHz (ch 36/44/149/157) |
| Sample Rate    | 20 Msps                                       |
| Bandwidth      | 20 MHz                                        |

**Identifiers extracted:**
- MAC address (BSSID / source)
- Probed SSIDs (from probe requests)
- Supported rates, HT/VHT capability flags

**RF fingerprint values:**
- Carrier Frequency Offset (CFO) in Hz — unique per transmitter oscillator
- CFO standard deviation
- Number of observed packets

**How it works:** Captures raw IQ at 20 Msps, detects 802.11 packet bursts via
energy thresholding, estimates CFO from OFDM short training field
autocorrelation, and clusters transmitters by CFO signature (5 kHz bins).

---

### BLE (Bluetooth Low Energy)

| Detail         | Value                                         |
|----------------|-----------------------------------------------|
| Frequencies    | 2.402, 2.426, 2.480 GHz (advertising ch 37/38/39) |
| Sample Rate    | 4 Msps                                        |
| Bandwidth      | 2 MHz                                         |

**Identifiers extracted:**
- MAC address (may be randomized)
- Advertisement type (ADV_IND, ADV_NONCONN_IND, SCAN_RSP, etc.)
- Device name (if broadcast)
- Manufacturer ID and data (e.g. Apple 0x004C, Microsoft 0x0006)
- Apple Continuity sub-type (FindMy/AirTag, AirPods, Handoff, etc.)
- TX power level
- 16-bit service UUIDs

**RF fingerprint values:**
- Mean advertising interval (ms)
- Advertising jitter (interval std dev)
- Clock drift (ppm) — unique per device crystal
- RSSI estimate (dBm)

**How it works:** GFSK demodulation via FM discriminator, matched-filter
detection using the BLE advertising access address (0x8E89BED6), data
dewhitening with channel-seeded LFSR, PDU parsing, and AD structure extraction.

---

### LTE (4G)

| Detail         | Value                                         |
|----------------|-----------------------------------------------|
| Bands          | B2 (PCS), B4 (AWS-1), B12 (700a), B41 (TDD 2.5G), B71 (600 MHz) |
| Sample Rate    | 2 Msps                                        |
| Bandwidth      | 2 MHz                                         |

**Identifiers extracted:**
- Physical Cell ID (PCI)
- EARFCN (channel number)
- Band number
- eNodeB ID (when derivable)

**RF fingerprint values:**
- RSRP (Reference Signal Received Power) per cell
- IQ gain imbalance (dB) — unique per transmitter DAC/mixer
- IQ phase imbalance (degrees)
- PA 3rd-order intermodulation (IMD3 in dBc)

**How it works:** Detects cells via PSS (Primary Synchronization Signal)
correlation using Zadoff-Chu sequences for each NID2 (0, 1, 2). Measures
IQ imbalance from I/Q power ratio and cross-correlation. Estimates PA IMD3
from power spectral density edge-to-center ratio.

---

### 5G NR (New Radio)

| Detail         | Value                                         |
|----------------|-----------------------------------------------|
| Bands          | n77/n78 (C-Band: 3.3 – 4.2 GHz)              |
| Frequencies    | 3.55, 3.60, 3.65, 3.70, 3.80, 3.90, 4.00 GHz |
| Sample Rate    | 20 Msps                                       |
| Bandwidth      | 20 MHz                                        |

**Identifiers extracted:**
- NR Cell ID (partial, from NID2)
- NID1, NID2
- Frequency and band
- Beam indices and per-beam RSRP

**RF fingerprint values:**
- SSB (SS/PBCH Block) timing drift (microseconds) — physical-layer fingerprint
  unique to each base station oscillator
- Per-beam RSRP and timing offsets

**How it works:** Generates NR PSS (m-sequence based, length 127) for each NID2,
maps to time domain via IFFT, cross-correlates with captured IQ. Clusters
detections into cells, measures inter-SSB timing drift against expected 20 ms
period.

---

### UWB (Ultra-Wideband 802.15.4z)

| Detail         | Value                                         |
|----------------|-----------------------------------------------|
| Channels       | Ch 5 (6489.6 MHz), Ch 9 (7987.2 MHz)         |
| Sample Rate    | 20 Msps                                       |

**Note:** The HackRF One Pro has a maximum frequency of 6 GHz. Both UWB
channels exceed this limit, so **UWB capture is not possible** with HackRF
hardware. The module will run but return empty results. A dedicated UWB
receiver (e.g. Qorvo DW3000) would be needed for UWB fingerprinting.

**Identifiers (when available):** Preamble code index, STS parameters
**RF fingerprint (when available):** Pulse shape width, PSD profile

---

### TPMS (Tire Pressure Monitoring System)

| Detail         | Value                                         |
|----------------|-----------------------------------------------|
| Frequency      | 315 MHz                                       |
| Sample Rate    | 2 Msps                                        |
| Modulation     | OOK (On-Off Keying), Manchester coded          |

**Identifiers extracted:**
- Sensor ID (per tire: FL/FR/RL/RR)
- Pressure (PSI and kPa)
- Temperature (C and F)

**RF fingerprint values:**
- Broadcast interval (seconds)
- Detected bit rate (bps)

**How it works:** OOK demodulation via envelope detection and adaptive
thresholding. Manchester decoding of bit stream. Searches for known TPMS
preamble patterns (Schrader, Continental, Sensata). Parses sensor frames to
extract ID, pressure, and temperature.

**Best results:** Scan near vehicles. Sensors broadcast every 30-60 seconds.
Longer capture durations significantly improve detection. Sensors transmit more
frequently when tire pressure changes.

---

### RKE (Remote Keyless Entry)

| Detail         | Value                                         |
|----------------|-----------------------------------------------|
| Frequency      | 315 MHz                                       |
| Sample Rate    | 2 Msps                                        |
| Modulation     | ASK (Amplitude Shift Keying)                   |

**Identifiers extracted:**
- Rolling code (encrypted counter, hex)
- Fixed code (serial number, hex)
- Bit count and encoding type (PWM/Manchester)

**RF fingerprint values:**
- Power amplifier rise time (microseconds)
- Overshoot percentage
- Fall time (microseconds)
- Settling time (microseconds)
- Steady-state power (dBm)
- Bit rate (bps)

**How it works:** Detects ASK transmission bursts via envelope thresholding.
Analyzes the power amplifier ramp profile (rise/fall/overshoot/settling) as a
physical-layer fingerprint unique to each key fob's transmitter. Decodes bit
stream assuming KeeLoq-style format (34 encrypted + 28 serial + 4 button bits).

**Best results:** Requires someone to press a key fob button during the capture
window. Default 60-second capture gives a reasonable window.

---

## Output Formats

### Terminal Output

The terminal displays a dark-themed readout with color-coded sections:

| Color        | Protocol | Hex Code |
|--------------|----------|----------|
| Purple       | Wi-Fi    | #5500FF  |
| Red          | BLE      | #E63946  |
| Teal         | LTE      | #2A9D8F  |
| Dark Blue    | 5G NR    | #264653  |
| Orange       | UWB      | #F4845F  |
| Violet       | TPMS     | #7209B7  |
| Blue         | RKE      | #4361EE  |

Value styling:
- **Blue** (#58A6FF) — Protocol identifiers (MACs, SSIDs, cell IDs, sensor IDs)
- **Orange** (#FFA657) — Physical-layer fingerprint values (CFO, drift, IQ imbalance)
- **Green** (#7EE787) — Composite hash
- Gray — Keys and regular values

### JSON Export (`--output` / `-o`)

```json
{
  "spectral_dna": {
    "version": "1.0.0",
    "target": "Lab Test 1",
    "timestamp": "2026-03-04T15:30:00+00:00",
    "composite_hash": "abcdef1234...",
    "protocols": {
      "wifi": {
        "identifiers": { ... },
        "rf_fingerprint": { ... },
        "hash_material": "..."
      },
      "ble": { ... },
      "lte": { ... },
      ...
    }
  }
}
```

### HTML Export (`--html`)

Generates a self-contained styled HTML file matching the terminal color scheme.
Opens in any browser — useful for sharing results or archiving.

### Composite Hash

The SHA-256 composite hash is computed over all identifier and RF fingerprint
key-value pairs, sorted alphabetically. This produces a single 64-character
hex string that uniquely represents the entire spectral environment captured.

If any transmitter changes (new device appears, existing device's CFO drifts,
different cell tower detected), the composite hash changes.

---

## Running Individual Modules

Each protocol module can be run standalone for focused testing:

```bash
# BLE only
python -m spectral_dna.ble_print --duration 10 --channels 37 38 39 --output ble.json

# Wi-Fi only
python -m spectral_dna.wifi_print --duration 5 --output wifi.json

# LTE only
python -m spectral_dna.lte_print --duration 3 --bands 2 4 12 --output lte.json

# 5G NR only
python -m spectral_dna.fiveg_print --duration 2 --output 5g.json

# TPMS only
python -m spectral_dna.tpms_print --duration 30 --output tpms.json

# RKE only
python -m spectral_dna.rke_print --duration 60 --output rke.json
```

Each standalone module outputs JSON with `identifiers` and `rf_fingerprint`
sections.

---

## Understanding the Output

### What makes a good fingerprint?

The fingerprint quality depends on what's in the RF environment:

| Scenario           | Expected Results                                |
|--------------------|-------------------------------------------------|
| Crowded office     | Many Wi-Fi, BLE devices; multiple LTE/5G cells  |
| Parking lot        | TPMS sensors from nearby vehicles; RKE possible  |
| Isolated chamber   | Only the target subject's personal devices       |
| Rural/quiet area   | Fewer devices but cleaner, more distinctive signatures |

### Key fingerprint metrics

**CFO (Carrier Frequency Offset):** Every transmitter has a slightly different
crystal oscillator frequency. The CFO measured from Wi-Fi packets is a
hardware-level signature that persists across sessions and cannot be spoofed
without replacing the oscillator.

**IQ Imbalance:** Manufacturing variations in a transmitter's mixer and DAC
create measurable gain and phase imbalances between I and Q channels. These
are stable, device-specific signatures.

**Clock Drift (BLE):** BLE devices advertise on a timer driven by their
crystal oscillator. The drift in ppm is characteristic of each device.

**PA Power Profile (RKE):** Each key fob's power amplifier has a unique
rise/fall/overshoot profile determined by its analog circuitry.

**SSB Timing Drift (5G):** Base station oscillator drift creates micro-timing
offsets in SS/PBCH block transmission that fingerprint each cell site.

---

## Hardware Notes

### HackRF One Pro Specifications

| Parameter       | Value                     |
|-----------------|---------------------------|
| Frequency Range | 1 MHz – 6 GHz             |
| Sample Rate     | 2 – 20 Msps               |
| Resolution      | 8-bit I/Q                  |
| Bandwidth       | Up to 20 MHz               |
| Interface       | USB 2.0 High Speed         |

### Capture Backend

SpectralDNA uses **hackrf_transfer** (CLI) as the primary capture backend.
This invokes the HackRF firmware directly and writes raw int8 IQ data to a
temporary file, which is then converted to complex64 numpy arrays.

If hackrf_transfer is not available, it falls back to **SoapySDR** Python
bindings. On Windows, SoapySDR may have driver conflicts — hackrf_transfer is
more reliable.

### Frequency Limitations

- **UWB channels** (6.49 / 7.99 GHz) exceed the HackRF's 6 GHz maximum.
  The module runs gracefully but returns empty results.
- **Sub-1 MHz** frequencies are below HackRF's minimum. Not relevant for
  current protocol modules.

### Antenna Recommendations

- **Wideband antenna** (e.g. ANT500): Good general coverage, reduced gain
- **2.4 GHz directional**: Best for Wi-Fi and BLE focused work
- **315 MHz whip**: Optimal for TPMS and RKE
- **Cellular antenna**: Best for LTE and 5G NR

---

## Troubleshooting

### "No HackRF backend available"

1. Check that the HackRF is plugged in: `hackrf_info`
2. On Windows, ensure WinUSB driver is installed (use Zadig)
3. Verify hackrf_transfer is on PATH: `which hackrf_transfer`
4. If using radioconda, set PATH first:
   ```bash
   export PATH="$HOME/radioconda/Library/bin:$PATH"
   ```

### "Frequency outside HackRF range"

The requested frequency exceeds 1 MHz – 6 GHz. This is expected for UWB.
The module returns empty results gracefully.

### No devices detected (BLE/Wi-Fi)

- Ensure the antenna is connected
- Increase capture duration (`--ble-duration 10`)
- Verify RF activity exists on those frequencies (use SDR# or gqrx to check)
- In shielded environments, there may genuinely be no signals

### No TPMS/RKE signals

- TPMS: Must be near vehicles. Sensors broadcast every 30-60 seconds.
  Use `--tpms-duration 120` for better chances.
- RKE: Requires an active key fob press during capture. Point the antenna
  at the fob and press a button during the capture window.

### Scan is slow

The total scan time is approximately:
```
wifi:  duration x 7 channels  (default: 14 s)
ble:   duration x 3 channels  (default: 15 s)
lte:   duration x 5 bands     (default: 15 s)
5g:    duration x 7 freqs     (default: 14 s)
uwb:   duration x 2 channels  (default:  6 s)
tpms:  single capture         (default: 30 s)
rke:   single capture         (default: 60 s)
```
**Total default: ~2.5 minutes.** Reduce durations for faster scans, or use
`-p` to scan only specific protocols.

### Import errors

Ensure you're using the correct Python environment:
```bash
# Check which python
which python
# Should show radioconda path, or your venv

# Install missing deps
pip install numpy scipy rich click
```

---

## Architecture Overview

```
spectral_dna/
  __main__.py        Click CLI entry point
  spectral_dna.py    Orchestrator — runs modules, aggregates results
  capture.py         HackRF capture engine (hackrf_transfer + SoapySDR)
  renderer.py        Terminal (Rich), JSON, and HTML output
  wifi_print.py      Wi-Fi 802.11 fingerprint module
  ble_print.py       BLE advertising fingerprint module
  lte_print.py       LTE cell search and UE fingerprint module
  fiveg_print.py     5G NR SSB detection and beam measurement
  uwb_print.py       UWB 802.15.4z preamble analysis
  tpms_print.py      TPMS OOK demodulation and frame parsing
  rke_print.py       RKE ASK burst analysis and code extraction
```

Each `*_print.py` module exposes:
- `scan(sdr, ...)` → returns a fingerprint dataclass
- `main()` → standalone CLI entry point
- Fingerprint dataclass with `.identifiers()`, `.rf_fingerprint()`, `.hash_material()`
