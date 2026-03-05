# SpectralDNA

**Passive RF emission fingerprinting tool for HackRF One Pro.**

SpectralDNA captures and analyzes RF emissions across 8 wireless protocols to produce a composite spectral fingerprint — a unique signature derived from the physical-layer characteristics of every transmitter within range.

## Protocols

| Protocol | Band | What It Extracts |
|----------|------|-----------------|
| Wi-Fi | 2.4 / 5 GHz | MACs, SSIDs, CFO per transmitter |
| BLE | 2.4 GHz | MACs, manufacturer data, clock drift |
| LTE | 700 MHz – 2.5 GHz | PCI, EARFCN, carrier ID, IQ imbalance |
| 5G NR | 3.3 – 4.2 GHz | NR Cell ID, SSB timing drift, beams |
| TPMS | 315 MHz | Sensor IDs, pressure, temperature |
| RKE | 315 MHz | Rolling/fixed codes, PA rise/fall profile |
| ANT/ANT+ | 2.4 GHz | Device numbers, type codes, clock drift |
| GPS/GNSS | L1/L5 | Satellite PRNs, band power, interference |

## Quick Start

```bash
# With radioconda (Windows)
export PATH="$HOME/radioconda/Library/bin:$HOME/radioconda:$HOME/radioconda/Scripts:$PATH"

# Full scan
python -m spectral_dna -t "Lab Test" -o fingerprint.json --html fingerprint.html

# Specific protocols only
python -m spectral_dna -t "Quick" -p wifi -p ble --wifi-duration 1 --ble-duration 2

# Save environment snapshot
python -m spectral_dna -t "Office" --snapshot office_baseline.json

# Compare against baseline
python -m spectral_dna -t "Office Later" --compare office_baseline.json

# Enroll a subject
python -m spectral_dna -t "Alice" --enroll Alice

# Check if subject is present
python -m spectral_dna --check Alice

# List enrolled subjects
python -m spectral_dna --list-subjects
```

## Features

### Data Enrichment
Offline lookup tables provide manufacturer names from MAC OUIs, BLE company IDs, GATT service UUIDs, LTE carrier identification from EARFCN, 5G NR carrier identification from frequency, and ANT+ device type names. MAC randomization detection flags privacy-rotated addresses.

### Environment Snapshots
Save a baseline scan and compare future scans against it. Devices are classified as PRESENT (matched with RF similarity score), ABSENT (in baseline but not current), or NEW (not in baseline). Includes environment summary with 2.4 GHz congestion score, MAC randomization ratio, and active carrier diversity.

### Subject Profiles
Associate scans with people. Enroll a subject from a scan, repeat to strengthen their profile (devices seen in >50% of scans become "stable"), then check if their devices are present in any future scan.

## Requirements

- HackRF One Pro (or HackRF One)
- Python 3.10+
- numpy, scipy, rich, click, scapy

```bash
pip install -r requirements.txt
```

## Architecture

```
spectral_dna/
  __main__.py        CLI (Click)
  spectral_dna.py    Orchestrator
  capture.py         HackRF capture engine (hackrf_transfer + SoapySDR)
  lookups.py         Offline OUI / BLE / LTE / 5G / ANT lookup tables
  environment.py     Snapshots, comparison, presence detection
  subjects.py        Subject profile management
  renderer.py        Terminal (Rich), JSON, HTML output
  wifi_print.py      Wi-Fi 802.11 fingerprinting
  ble_print.py       BLE advertising fingerprinting
  lte_print.py       LTE cell search + UE fingerprinting
  fiveg_print.py     5G NR SSB detection
  tpms_print.py      TPMS OOK demodulation
  rke_print.py       RKE ASK burst analysis
  ant_print.py       ANT/ANT+ GFSK demodulation
  gnss_print.py      GPS/GNSS band power analysis
```

## Output

- **Terminal**: Dark-themed Rich console with color-coded protocol sections
- **JSON**: Machine-readable fingerprint with all identifiers and RF signatures
- **HTML**: Self-contained styled report matching the terminal aesthetic
- **Composite Hash**: SHA-256 over all collected values — changes if any transmitter changes

## How It Works

Each protocol module captures raw IQ samples from the HackRF, applies protocol-specific demodulation (OFDM for Wi-Fi, GFSK for BLE/ANT, PSS correlation for LTE/5G, OOK/ASK for TPMS/RKE), and extracts both protocol-layer identifiers (MACs, cell IDs, sensor IDs) and physical-layer fingerprints (CFO, clock drift, IQ imbalance, PA characteristics).

Physical-layer fingerprints are hardware-intrinsic — they depend on manufacturing variations in oscillators, mixers, DACs, and power amplifiers. These cannot be spoofed without replacing the transmitter hardware.

## Documentation

See [USAGE_GUIDE.md](USAGE_GUIDE.md) for detailed protocol descriptions, CLI reference, duration guidelines, hardware notes, and troubleshooting.

## License

Research / academic use.
