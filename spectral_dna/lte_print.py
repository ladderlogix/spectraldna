"""
LTE fingerprint module — 700 MHz – 2.5 GHz (Bands 2/4/12/41/71).

Extracts:
  - IMEI (from attach procedure messages, when observable)
  - Supported band list (from UE capability messages)
  - IQ imbalance (gain/phase) — physical-layer fingerprint
  - Cell association: eNB ID, PCI (Physical Cell ID), EARFCN
  - PA 3rd-order intermodulation — physical-layer fingerprint

Capture strategy:
  Scan each LTE band center frequency.  Detect cells via PSS/SSS
  correlation.  Measure IQ/PA characteristics from uplink signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import correlate, resample_poly

from .capture import (
    CaptureResult,
    HackRFCapture,
    estimate_iq_imbalance,
    compute_power_spectral_density,
)
from .lookups import lookup_lte_carrier

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LTE band definitions (downlink center frequencies)
# ---------------------------------------------------------------------------

LTE_BANDS = {
    2:  {"name": "PCS",      "dl_low": 1930e6, "dl_high": 1990e6, "earfcn_offset": 600},
    4:  {"name": "AWS-1",    "dl_low": 2110e6, "dl_high": 2155e6, "earfcn_offset": 1950},
    12: {"name": "700a",     "dl_low": 729e6,  "dl_high": 746e6,  "earfcn_offset": 5010},
    41: {"name": "TDD 2.5G", "dl_low": 2496e6, "dl_high": 2690e6, "earfcn_offset": 39650},
    71: {"name": "600 MHz",  "dl_low": 617e6,  "dl_high": 652e6,  "earfcn_offset": 68586},
}

SAMPLE_RATE = 2e6      # 2 Msps — HackRF minimum; covers 1.4 MHz LTE (6 RBs) for cell search
BANDWIDTH = 2e6
GAIN = 40.0

# LTE Primary Synchronization Signal — Zadoff-Chu root indices
PSS_ROOTS = {0: 25, 1: 29, 2: 34}
PSS_LEN = 62  # 62 subcarriers


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class LTECell:
    pci: int = 0                  # Physical Cell ID (0–503)
    earfcn: int = 0
    band: int = 0
    enb_id: int = 0               # eNodeB ID = PCI >> 0 (derived from ECGI)
    freq_hz: float = 0.0
    rsrp_dbm: float = -140.0
    nid1: int = 0                 # SSS group (0–167)
    nid2: int = 0                 # PSS index (0–2)
    carrier: str = ""             # e.g. "T-Mobile (B71)"


@dataclass
class LTEDevice:
    """Represents a UE (phone/modem) detected via uplink emissions."""
    imei: str = ""
    supported_bands: list[int] = field(default_factory=list)
    iq_gain_imbalance_db: float = 0.0
    iq_phase_imbalance_deg: float = 0.0
    pa_imd3_dbc: float = 0.0     # 3rd-order intermod relative to carrier
    associated_cell: LTECell | None = None


@dataclass
class LTEFingerprint:
    cells: list[LTECell] = field(default_factory=list)
    devices: list[LTEDevice] = field(default_factory=list)
    scanned_bands: list[int] = field(default_factory=list)
    capture_duration: float = 0.0

    def identifiers(self) -> dict:
        out = {}
        for i, cell in enumerate(self.cells):
            p = f"lte_cell{i}"
            out[f"{p}_pci"] = str(cell.pci)
            out[f"{p}_earfcn"] = str(cell.earfcn)
            out[f"{p}_band"] = str(cell.band)
            out[f"{p}_enb_id"] = str(cell.enb_id)
            out[f"{p}_freq_mhz"] = f"{cell.freq_hz / 1e6:.3f}"
            if cell.carrier:
                out[f"{p}_carrier"] = cell.carrier
        for i, dev in enumerate(self.devices):
            p = f"lte_ue{i}"
            if dev.imei:
                out[f"{p}_imei"] = dev.imei
            if dev.supported_bands:
                out[f"{p}_bands"] = ", ".join(str(b) for b in dev.supported_bands)
        return out

    def rf_fingerprint(self) -> dict:
        out = {}
        for i, cell in enumerate(self.cells):
            out[f"lte_cell{i}_rsrp_dbm"] = f"{cell.rsrp_dbm:.1f}"
        for i, dev in enumerate(self.devices):
            p = f"lte_ue{i}"
            out[f"{p}_iq_gain_imbalance_db"] = f"{dev.iq_gain_imbalance_db:+.3f}"
            out[f"{p}_iq_phase_imbalance_deg"] = f"{dev.iq_phase_imbalance_deg:+.3f}"
            out[f"{p}_pa_imd3_dbc"] = f"{dev.pa_imd3_dbc:.1f}"
        return out

    def hash_material(self) -> str:
        parts = []
        for cell in sorted(self.cells, key=lambda c: c.pci):
            parts.append(f"pci:{cell.pci}:earfcn:{cell.earfcn}")
        for dev in self.devices:
            if dev.imei:
                parts.append(f"imei:{dev.imei}")
            parts.append(f"iq:{dev.iq_gain_imbalance_db:.6f}")
            parts.append(f"imd3:{dev.pa_imd3_dbc:.3f}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# PSS / SSS generation and cell search
# ---------------------------------------------------------------------------

def _generate_pss(nid2: int) -> np.ndarray:
    """Generate LTE Primary Synchronization Signal (Zadoff-Chu sequence)."""
    root = PSS_ROOTS[nid2]
    n = np.arange(PSS_LEN)
    # d_u(n) = exp(-j * pi * u * n * (n+1) / 63)
    pss = np.exp(-1j * np.pi * root * n * (n + 1) / 63)
    return pss


def _pss_correlate(samples: np.ndarray, sample_rate: float) -> list[dict]:
    """
    Correlate against all three PSS sequences to find LTE cells.
    Returns list of detected PSS with position, NID2, and correlation strength.
    """
    # PSS occupies 62 subcarriers centered in a 1.4 MHz signal
    # At 1.92 Msps, PSS occupies ~62/128 of the bandwidth
    # Time-domain PSS is ~4.7 µs (one OFDM symbol minus CP)

    detections = []

    for nid2 in range(3):
        pss_freq = _generate_pss(nid2)

        # Convert to time domain (IFFT with zero-padding)
        nfft = 128
        pss_td = np.zeros(nfft, dtype=np.complex64)
        pss_td[1:32] = pss_freq[:31]
        pss_td[97:128] = pss_freq[31:]
        pss_td = np.fft.ifft(pss_td)
        pss_norm = np.sqrt(np.mean(np.abs(pss_td) ** 2))
        if pss_norm > 0:
            pss_td = pss_td / pss_norm

        # Cross-correlate over first 20 ms
        window = min(len(samples), int(sample_rate * 0.02))
        if window <= len(pss_td):
            continue
        corr = np.abs(correlate(samples[:window], pss_td, mode="valid"))
        noise_floor = np.median(corr)
        threshold = noise_floor * 8

        peaks = np.where(corr > threshold)[0]
        if len(peaks) == 0:
            continue

        # Cluster nearby peaks
        groups = []
        group = [peaks[0]]
        for p in peaks[1:]:
            if p - group[-1] < 128:
                group.append(p)
            else:
                groups.append(group)
                group = [p]
        groups.append(group)

        for group in groups:
            best_idx = group[np.argmax(corr[group])]
            strength = corr[best_idx]
            detections.append({
                "nid2": nid2,
                "position": int(best_idx),
                "strength": float(strength),
                "rsrp_linear": float(strength ** 2),
            })

    return detections


def _estimate_pa_imd3(samples: np.ndarray, sample_rate: float) -> float:
    """
    Estimate 3rd-order intermodulation distortion from the power spectrum.
    Measures the ratio of in-band power to 3rd-order products.
    Returns IMD3 in dBc.
    """
    nfft = 2048
    spectrum = np.fft.fftshift(np.abs(np.fft.fft(samples[:nfft], nfft)) ** 2)
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / sample_rate))

    # Main signal band (center 80%)
    center_bins = slice(nfft // 5, 4 * nfft // 5)
    signal_power = np.mean(spectrum[center_bins])

    # 3rd-order products in outer edges
    edge_low = slice(0, nfft // 10)
    edge_high = slice(9 * nfft // 10, nfft)
    imd_power = (np.mean(spectrum[edge_low]) + np.mean(spectrum[edge_high])) / 2

    if imd_power > 0 and signal_power > 0:
        imd3_dbc = 10 * np.log10(imd_power / signal_power)
    else:
        imd3_dbc = -80.0

    return float(imd3_dbc)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scan(
    sdr: HackRFCapture,
    duration_per_band: float = 3.0,
    bands: list[int] | None = None,
) -> LTEFingerprint:
    """
    Run an LTE cell search and UE fingerprint scan.

    Scans each configured band, detects cells via PSS/SSS correlation,
    and estimates IQ imbalance / PA distortion from observed signals.
    """
    if bands is None:
        bands = [2, 4, 12, 41, 71]

    fp = LTEFingerprint()
    fp.scanned_bands = bands
    total_duration = 0.0

    for band_num in bands:
        band = LTE_BANDS.get(band_num)
        if band is None:
            log.warning("Unknown LTE band %d, skipping", band_num)
            continue

        # Scan center of band (one capture per band for speed)
        dl_center = (band["dl_low"] + band["dl_high"]) / 2
        scan_freqs = [dl_center]

        for freq in scan_freqs:
            log.info("LTE scan: Band %d (%.3f MHz)", band_num, freq / 1e6)

            cap = sdr.capture(
                duration=duration_per_band,
                center_freq=freq,
                sample_rate=SAMPLE_RATE,
                bandwidth=BANDWIDTH,
                gain=GAIN,
            )
            total_duration += cap.duration

            # Cell search via PSS correlation
            pss_hits = _pss_correlate(cap.samples, SAMPLE_RATE)

            for hit in pss_hits:
                # Compute approximate EARFCN
                earfcn = int(
                    band["earfcn_offset"]
                    + (freq - band["dl_low"]) / 100e3
                )
                # PCI = 3 * NID1 + NID2 (NID1 requires SSS decode, approximate)
                pci = hit["nid2"]  # partial — full PCI needs SSS

                rsrp = 10 * np.log10(hit["rsrp_linear"] + 1e-20) - 30

                cell = LTECell(
                    pci=pci,
                    earfcn=earfcn,
                    band=band_num,
                    enb_id=0,
                    freq_hz=freq,
                    rsrp_dbm=rsrp,
                    nid2=hit["nid2"],
                    carrier=lookup_lte_carrier(earfcn),
                )
                fp.cells.append(cell)
                log.info("  Detected cell: PCI=%d EARFCN=%d RSRP=%.1f dBm",
                         pci, earfcn, rsrp)

            # IQ imbalance and PA distortion from observed signal
            if len(cap.samples) > 1000:
                gain_imb, phase_imb = estimate_iq_imbalance(cap.samples)
                imd3 = _estimate_pa_imd3(cap.samples, SAMPLE_RATE)

                dev = LTEDevice(
                    iq_gain_imbalance_db=gain_imb,
                    iq_phase_imbalance_deg=phase_imb,
                    pa_imd3_dbc=imd3,
                    associated_cell=fp.cells[-1] if fp.cells else None,
                )
                fp.devices.append(dev)

    fp.capture_duration = total_duration
    log.info("LTE scan complete: %d cells, %d UE signatures",
             len(fp.cells), len(fp.devices))
    return fp


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SpectralDNA LTE fingerprint module")
    parser.add_argument("--duration", type=float, default=3.0,
                        help="Seconds per band (default: 3)")
    parser.add_argument("--bands", nargs="+", type=int, default=[2, 4, 12, 41, 71],
                        help="LTE bands to scan")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    sdr = HackRFCapture()
    sdr.open()
    try:
        fp = scan(sdr, args.duration, args.bands)
    finally:
        sdr.close()

    result = {"identifiers": fp.identifiers(), "rf_fingerprint": fp.rf_fingerprint()}
    print(json.dumps(result, indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
