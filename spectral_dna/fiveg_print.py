"""
5G NR fingerprint module — 3.7 GHz n77/n78.

Extracts:
  - NR Cell ID (from SSB detection)
  - SSB burst timing drift — physical-layer fingerprint
  - Beam index and RSRP per beam

Capture strategy:
  Tune to n77/n78 band, capture at 30.72 Msps (or downsampled),
  detect SS/PBCH blocks via PSS correlation, measure timing and power.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import correlate

from .capture import (
    CaptureResult,
    HackRFCapture,
    estimate_iq_imbalance,
)
from .lookups import lookup_5g_carrier

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 5G NR band definitions
# ---------------------------------------------------------------------------

NR_BANDS = {
    "n77": {"low": 3.3e9, "high": 4.2e9, "scs": 30e3, "name": "C-Band"},
    "n78": {"low": 3.3e9, "high": 3.8e9, "scs": 30e3, "name": "C-Band (EU)"},
}

# Scan frequencies — major US C-Band CBRS/mid-band deployments
SCAN_FREQS_N77 = [3.55e9, 3.60e9, 3.65e9, 3.70e9, 3.80e9, 3.90e9, 4.00e9]

# HackRF max sample rate is 20 Msps; NR SSB spans ~7.2 MHz in freq domain
# We capture at 20 Msps which covers SSB detection
SAMPLE_RATE = 20e6
BANDWIDTH = 20e6
GAIN = 40.0

# NR PSS: m-sequence based, length 127
NR_PSS_LEN = 127
NR_SSB_SCS = 30e3  # subcarrier spacing for SSB
NR_SSB_SYMBOLS = 4  # SSB is 4 OFDM symbols


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class NRBeam:
    beam_index: int = 0
    ssb_index: int = 0
    rsrp_dbm: float = -140.0
    timing_offset_us: float = 0.0


@dataclass
class NRCell:
    nr_cell_id: int = 0
    nid1: int = 0
    nid2: int = 0
    freq_hz: float = 0.0
    band: str = ""
    beams: list[NRBeam] = field(default_factory=list)
    ssb_period_ms: float = 20.0
    ssb_timing_drift_us: float = 0.0     # physical-layer fingerprint
    rsrp_dbm: float = -140.0
    carrier: str = ""                    # e.g. "T-Mobile (n41)"


@dataclass
class FiveGFingerprint:
    cells: list[NRCell] = field(default_factory=list)
    scanned_freqs: list[float] = field(default_factory=list)
    capture_duration: float = 0.0

    def identifiers(self) -> dict:
        out = {}
        for i, cell in enumerate(self.cells):
            p = f"5g_cell{i}"
            out[f"{p}_nr_cell_id"] = str(cell.nr_cell_id)
            out[f"{p}_nid1"] = str(cell.nid1)
            out[f"{p}_nid2"] = str(cell.nid2)
            out[f"{p}_freq_mhz"] = f"{cell.freq_hz / 1e6:.1f}"
            out[f"{p}_band"] = cell.band
            if cell.carrier:
                out[f"{p}_carrier"] = cell.carrier
            for j, beam in enumerate(cell.beams):
                out[f"{p}_beam{j}_index"] = str(beam.beam_index)
                out[f"{p}_beam{j}_rsrp_dbm"] = f"{beam.rsrp_dbm:.1f}"
        return out

    def rf_fingerprint(self) -> dict:
        out = {}
        for i, cell in enumerate(self.cells):
            p = f"5g_cell{i}"
            out[f"{p}_ssb_timing_drift_us"] = f"{cell.ssb_timing_drift_us:+.4f}"
            out[f"{p}_rsrp_dbm"] = f"{cell.rsrp_dbm:.1f}"
            for j, beam in enumerate(cell.beams):
                out[f"{p}_beam{j}_timing_offset_us"] = f"{beam.timing_offset_us:+.4f}"
        return out

    def hash_material(self) -> str:
        parts = []
        for cell in sorted(self.cells, key=lambda c: c.nr_cell_id):
            parts.append(f"nrcid:{cell.nr_cell_id}")
            parts.append(f"drift:{cell.ssb_timing_drift_us:.6f}")
            for beam in cell.beams:
                parts.append(f"beam:{beam.beam_index}:{beam.rsrp_dbm:.2f}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# NR PSS generation and detection
# ---------------------------------------------------------------------------

def _generate_nr_pss(nid2: int) -> np.ndarray:
    """
    Generate 5G NR Primary Synchronization Signal.
    NR PSS is based on m-sequences of length 127.
    """
    # m-sequence generator polynomial: x^7 + x^4 + 1
    # Initial state depends on NID2
    x = np.zeros(127, dtype=np.int8)
    # Initialize shift register
    init_states = {0: [1, 1, 1, 0, 1, 1, 0], 1: [1, 1, 1, 0, 1, 1, 0], 2: [1, 1, 1, 0, 1, 1, 0]}
    reg = [0] * 7
    reg[6] = 1

    for n in range(127):
        x[n] = reg[0]
        new_bit = (reg[0] + reg[4]) % 2  # x^7 + x^4 + 1
        reg = [new_bit] + reg[:-1]

    # Modulate: d(n) = 1 - 2*x((n + 43*NID2) mod 127)
    pss = np.array([
        1 - 2 * x[(n + 43 * nid2) % 127] for n in range(127)
    ], dtype=np.complex64)

    return pss


def _nr_pss_detect(samples: np.ndarray, sample_rate: float) -> list[dict]:
    """
    Detect NR PSS in captured IQ samples.
    Returns list of detections with NID2, position, and strength.
    """
    detections = []

    for nid2 in range(3):
        pss_freq = _generate_nr_pss(nid2)

        # Map PSS to time domain via IFFT (occupies 127 subcarriers at 30 kHz SCS)
        nfft = 256  # 256-point FFT for 30 kHz SCS at ~7.68 Msps
        # Scale NFFT for our sample rate
        nfft = int(sample_rate / NR_SSB_SCS)
        nfft = max(256, min(nfft, 2048))  # clamp to reasonable range

        pss_td = np.zeros(nfft, dtype=np.complex64)
        half = NR_PSS_LEN // 2  # 63
        # Map PSS subcarriers symmetrically around DC
        pss_td[1 : half + 1] = pss_freq[half : half + half]
        pss_td[nfft - half :] = pss_freq[:half]
        pss_td = np.fft.ifft(pss_td)
        pss_td = pss_td / np.sqrt(np.mean(np.abs(pss_td) ** 2))

        # Correlate over first 20 ms (one SSB burst period)
        window = min(len(samples), int(sample_rate * 0.02))
        if window <= len(pss_td):
            continue
        corr = np.abs(correlate(samples[:window], pss_td, mode="valid"))

        noise_floor = np.median(corr)
        threshold = noise_floor * 6

        peaks = np.where(corr > threshold)[0]
        if len(peaks) == 0:
            continue

        # Cluster peaks
        groups = []
        group = [peaks[0]]
        for p in peaks[1:]:
            if p - group[-1] < nfft:
                group.append(p)
            else:
                groups.append(group)
                group = [p]
        groups.append(group)

        for group in groups:
            best_idx = group[np.argmax(corr[group])]
            detections.append({
                "nid2": nid2,
                "position": int(best_idx),
                "strength": float(corr[best_idx]),
                "time_us": float(best_idx / sample_rate * 1e6),
            })

    return detections


def _measure_ssb_timing_drift(
    detections: list[dict], sample_rate: float, expected_period_ms: float = 20.0
) -> float:
    """
    Measure timing drift between consecutive SSB bursts.
    Returns drift in microseconds.
    """
    if len(detections) < 2:
        return 0.0

    # Group by NID2 and measure inter-burst intervals
    by_nid2: dict[int, list[float]] = {}
    for d in detections:
        by_nid2.setdefault(d["nid2"], []).append(d["time_us"])

    drifts = []
    expected_us = expected_period_ms * 1000

    for nid2, times in by_nid2.items():
        times.sort()
        for i in range(len(times) - 1):
            interval = times[i + 1] - times[i]
            # Only consider intervals close to expected SSB period
            if abs(interval - expected_us) < expected_us * 0.5:
                drifts.append(interval - expected_us)

    return float(np.mean(drifts)) if drifts else 0.0


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scan(
    sdr: HackRFCapture,
    duration_per_freq: float = 2.0,
    scan_freqs: list[float] | None = None,
) -> FiveGFingerprint:
    """
    Run a 5G NR cell search and beam measurement.

    Scans C-Band frequencies, detects SSB via PSS correlation,
    measures beam RSRP and timing drift.
    """
    if scan_freqs is None:
        scan_freqs = SCAN_FREQS_N77

    fp = FiveGFingerprint()
    fp.scanned_freqs = scan_freqs
    total_duration = 0.0

    for freq in scan_freqs:
        log.info("5G NR scan: %.1f MHz", freq / 1e6)

        # Determine band
        band_name = "n77"
        for bname, bdef in NR_BANDS.items():
            if bdef["low"] <= freq <= bdef["high"]:
                band_name = bname
                break

        cap = sdr.capture(
            duration=duration_per_freq,
            center_freq=freq,
            sample_rate=SAMPLE_RATE,
            bandwidth=BANDWIDTH,
            gain=GAIN,
        )
        total_duration += cap.duration

        # PSS detection
        detections = _nr_pss_detect(cap.samples, SAMPLE_RATE)
        log.info("  Found %d PSS detections", len(detections))

        if not detections:
            continue

        # Group detections by NID2
        by_nid2: dict[int, list[dict]] = {}
        for d in detections:
            by_nid2.setdefault(d["nid2"], []).append(d)

        for nid2, dets in by_nid2.items():
            # Best detection
            best = max(dets, key=lambda d: d["strength"])
            rsrp = 10 * np.log10(best["strength"] ** 2 + 1e-20) - 30

            # SSB timing drift
            timing_drift = _measure_ssb_timing_drift(dets, SAMPLE_RATE)

            cell = NRCell(
                nr_cell_id=nid2,  # partial — full NR cell ID requires SSS + PBCH
                nid2=nid2,
                freq_hz=freq,
                band=band_name,
                rsrp_dbm=rsrp,
                ssb_timing_drift_us=timing_drift,
                carrier=lookup_5g_carrier(freq / 1e6),
            )

            # Create beam entries from individual detections
            for j, det in enumerate(sorted(dets, key=lambda d: d["time_us"])):
                beam = NRBeam(
                    beam_index=j,
                    ssb_index=j % 8,  # up to 8 SSB beams in sub-6 GHz
                    rsrp_dbm=10 * np.log10(det["strength"] ** 2 + 1e-20) - 30,
                    timing_offset_us=det["time_us"] - best["time_us"],
                )
                cell.beams.append(beam)

            fp.cells.append(cell)

    fp.capture_duration = total_duration
    log.info("5G scan complete: %d cells", len(fp.cells))
    return fp


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SpectralDNA 5G NR fingerprint module")
    parser.add_argument("--duration", type=float, default=2.0,
                        help="Seconds per frequency (default: 2)")
    parser.add_argument("--freqs", nargs="+", type=float, default=None,
                        help="Frequencies in MHz to scan")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    freqs = [f * 1e6 for f in args.freqs] if args.freqs else SCAN_FREQS_N77

    sdr = HackRFCapture()
    sdr.open()
    try:
        fp = scan(sdr, args.duration, freqs)
    finally:
        sdr.close()

    result = {"identifiers": fp.identifiers(), "rf_fingerprint": fp.rf_fingerprint()}
    print(json.dumps(result, indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
