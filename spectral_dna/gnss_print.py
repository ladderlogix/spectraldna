"""
GPS/GNSS fingerprint module — L1 (1575.42 MHz) and L5 (1176.45 MHz).

Extracts:
  - Visible satellite PRNs via GPS C/A Gold code correlation
  - Per-satellite C/N0 (carrier-to-noise ratio)
  - Per-satellite Doppler shift
  - L1 and L5 band power
  - Interference metric

Capture strategy:
  L1: 2 Msps — FFT-based parallel code-phase search across Doppler bins
      using all 32 GPS C/A Gold codes.
  L5: 2 Msps — energy detection only (L5 codes are 10x longer, cannot
      correlate at this sample rate).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .capture import (
    CaptureResult,
    HackRFCapture,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

L1_FREQ = 1575.42e6   # GPS L1 center frequency
L5_FREQ = 1176.45e6   # GPS L5 center frequency

SAMPLE_RATE = 2e6      # 2 Msps — HackRF minimum
BANDWIDTH = 2e6
GAIN = 40.0

CA_CODE_LEN = 1023    # GPS C/A code length in chips
CA_CHIP_RATE = 1.023e6  # chips per second
CA_CODE_PERIOD = 1e-3  # 1 ms

# G2 tap pairs for PRN 1-32 (from GPS ICD)
G2_TAPS = {
    1: (2, 6), 2: (3, 7), 3: (4, 8), 4: (5, 9), 5: (1, 9),
    6: (2, 10), 7: (1, 8), 8: (2, 9), 9: (3, 10), 10: (2, 3),
    11: (3, 4), 12: (5, 6), 13: (6, 7), 14: (7, 8), 15: (8, 9),
    16: (9, 10), 17: (1, 4), 18: (2, 5), 19: (3, 6), 20: (4, 7),
    21: (5, 8), 22: (6, 9), 23: (1, 3), 24: (4, 6), 25: (5, 7),
    26: (6, 8), 27: (7, 9), 28: (8, 10), 29: (1, 6), 30: (2, 7),
    31: (3, 8), 32: (4, 9),
}

# Doppler search range
DOPPLER_MIN = -5000    # Hz
DOPPLER_MAX = 5000     # Hz
DOPPLER_STEP = 500     # Hz

# Detection threshold: C/N0 above this means satellite is visible
CN0_THRESHOLD = 25.0   # dB-Hz


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class SatelliteInfo:
    prn: int = 0
    cn0_db_hz: float = 0.0
    doppler_hz: float = 0.0
    code_phase: int = 0


@dataclass
class GNSSFingerprint:
    satellites: list[SatelliteInfo] = field(default_factory=list)
    satellite_count: int = 0
    l1_band_power_dbm: float = -120.0
    l5_band_power_dbm: float = -120.0
    l5_present: bool = False
    interference_metric: float = 0.0
    capture_duration: float = 0.0

    def identifiers(self) -> dict:
        out = {}
        prns = [s.prn for s in self.satellites]
        out["gnss_satellite_count"] = str(self.satellite_count)
        out["gnss_visible_prns"] = ", ".join(str(p) for p in sorted(prns)) if prns else "none"
        for i, sat in enumerate(sorted(self.satellites, key=lambda s: s.prn)):
            out[f"gnss_prn{sat.prn}_cn0_db_hz"] = f"{sat.cn0_db_hz:.1f}"
        return out

    def rf_fingerprint(self) -> dict:
        out = {}
        out["gnss_l1_band_power_dbm"] = f"{self.l1_band_power_dbm:.1f}"
        out["gnss_l5_band_power_dbm"] = f"{self.l5_band_power_dbm:.1f}"
        out["gnss_l5_present"] = str(self.l5_present)
        out["gnss_interference_metric"] = f"{self.interference_metric:.2f}"
        for sat in sorted(self.satellites, key=lambda s: s.prn):
            out[f"gnss_prn{sat.prn}_doppler_hz"] = f"{sat.doppler_hz:.0f}"
        return out

    def hash_material(self) -> str:
        parts = []
        parts.append(f"sats:{self.satellite_count}")
        for sat in sorted(self.satellites, key=lambda s: s.prn):
            parts.append(f"PRN{sat.prn}:{sat.cn0_db_hz:.1f}:{sat.doppler_hz:.0f}")
        parts.append(f"L1:{self.l1_band_power_dbm:.1f}")
        parts.append(f"L5:{self.l5_band_power_dbm:.1f}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# GPS C/A Gold code generation
# ---------------------------------------------------------------------------

def _generate_ca_code(prn: int) -> np.ndarray:
    """
    Generate GPS C/A Gold code for a given PRN (1-32).

    Uses two 10-bit LFSRs (G1 and G2) with standard tap pairs.
    Returns array of 1023 chips as +1/-1.
    """
    if prn not in G2_TAPS:
        raise ValueError(f"Invalid PRN: {prn}")

    tap1, tap2 = G2_TAPS[prn]

    # Use plain Python lists for LFSR — much faster than np.roll per iteration
    g1 = [1] * 10
    g2 = [1] * 10

    code = np.zeros(CA_CODE_LEN, dtype=np.int8)

    for i in range(CA_CODE_LEN):
        code[i] = g1[9] ^ g2[tap1 - 1] ^ g2[tap2 - 1]

        # Clock G1: feedback = bit10 XOR bit3
        g1_fb = g1[9] ^ g1[2]
        g1 = [g1_fb] + g1[:9]

        # Clock G2: feedback = bit10 XOR bit9 XOR bit8 XOR bit6 XOR bit3 XOR bit2
        g2_fb = g2[9] ^ g2[8] ^ g2[7] ^ g2[5] ^ g2[2] ^ g2[1]
        g2 = [g2_fb] + g2[:9]

    # Convert 0/1 to +1/-1
    return 1 - 2 * code.astype(np.float32)


def _upsample_code(code: np.ndarray, sample_rate: float) -> np.ndarray:
    """Upsample C/A code from chip rate to sample rate."""
    samples_per_chip = sample_rate / CA_CHIP_RATE
    n_samples = int(CA_CODE_LEN * samples_per_chip)
    indices = np.floor(np.arange(n_samples) * CA_CHIP_RATE / sample_rate).astype(int)
    indices = np.clip(indices, 0, CA_CODE_LEN - 1)
    return code[indices]


# ---------------------------------------------------------------------------
# Acquisition — FFT-based parallel code-phase search
# ---------------------------------------------------------------------------

def _acquire_satellite(
    samples_1ms: np.ndarray,
    code_fft_conj: np.ndarray,
    doppler_hz: float,
    t_array: np.ndarray,
) -> tuple[float, int]:
    """
    Perform FFT-based acquisition for one Doppler bin.

    Multiplies input by carrier replica to remove Doppler, then correlates
    with C/A code using FFT convolution.

    Returns (peak_metric, code_phase).
    t_array is pre-computed np.arange(n)/sample_rate.
    """
    carrier = np.exp(-1j * 2 * np.pi * doppler_hz * t_array)
    stripped = samples_1ms * carrier

    stripped_fft = np.fft.fft(stripped, n=len(code_fft_conj))
    corr_mag = np.abs(np.fft.ifft(stripped_fft * code_fft_conj))

    peak_idx = np.argmax(corr_mag)
    peak_val = corr_mag[peak_idx]

    # Noise estimate: mean excluding peak region
    mask = np.ones(len(corr_mag), dtype=bool)
    excl_start = max(0, peak_idx - 10)
    excl_end = min(len(corr_mag), peak_idx + 10)
    mask[excl_start:excl_end] = False
    noise = np.mean(corr_mag[mask]) if np.any(mask) else 1e-20

    metric = peak_val / max(noise, 1e-20)
    return float(metric), int(peak_idx)


def _search_l1(cap: CaptureResult) -> list[SatelliteInfo]:
    """
    Search for GPS satellites on L1 using all 32 C/A codes.

    Performs FFT-based parallel code-phase search across Doppler bins.
    """
    if len(cap.samples) == 0:
        return []

    sample_rate = cap.sample_rate
    samples_per_code = int(sample_rate * CA_CODE_PERIOD)

    if len(cap.samples) < samples_per_code:
        log.warning("L1 capture too short for 1ms code period")
        return []

    # Use first 1ms of data (coherent integration over 1 code period)
    # Average multiple code periods for better SNR
    n_periods = min(int(len(cap.samples) / samples_per_code), 10)
    log.info("L1 search: using %d coherent periods (%d samples/period)",
             n_periods, samples_per_code)

    # Non-coherent averaging of correlation results
    doppler_bins = np.arange(DOPPLER_MIN, DOPPLER_MAX + 1, DOPPLER_STEP)

    # Pre-compute time array once (shared across all PRNs and Doppler bins)
    t_array = np.arange(samples_per_code) / sample_rate

    # Pre-slice data segments once
    segments = []
    for period in range(n_periods):
        start = period * samples_per_code
        end = start + samples_per_code
        if end > len(cap.samples):
            break
        segments.append(cap.samples[start:end])
    n_actual = len(segments)

    satellites = []

    for prn in range(1, 33):
        if prn % 8 == 1:
            log.info("  Searching PRNs %d-%d...", prn, min(prn + 7, 32))

        code = _generate_ca_code(prn)
        code_up = _upsample_code(code, sample_rate)

        # Zero-pad code to match sample length
        code_padded = np.zeros(samples_per_code, dtype=np.float32)
        code_padded[:len(code_up)] = code_up[:samples_per_code]
        code_fft_conj = np.conj(np.fft.fft(code_padded))

        best_metric = 0.0
        best_doppler = 0.0
        best_phase = 0

        for doppler in doppler_bins:
            # Average over multiple code periods
            total_metric = 0.0
            for segment in segments:
                metric, phase = _acquire_satellite(
                    segment, code_fft_conj, doppler, t_array,
                )
                total_metric += metric

            avg_metric = total_metric / max(n_actual, 1)

            if avg_metric > best_metric:
                best_metric = avg_metric
                best_doppler = doppler
                best_phase = phase

        # Convert metric to approximate C/N0
        # C/N0 ~ 10*log10(metric^2 * bandwidth / integration_time)
        if best_metric > 1.0:
            cn0 = 10 * np.log10(max(best_metric ** 2, 1e-20)) + 10 * np.log10(1000)
        else:
            cn0 = 0.0

        if cn0 >= CN0_THRESHOLD:
            log.info("  PRN %2d: C/N0=%.1f dB-Hz, Doppler=%+.0f Hz, phase=%d",
                     prn, cn0, best_doppler, best_phase)
            satellites.append(SatelliteInfo(
                prn=prn,
                cn0_db_hz=cn0,
                doppler_hz=best_doppler,
                code_phase=best_phase,
            ))

    return satellites


def _measure_band_power(cap: CaptureResult) -> float:
    """Compute total band power in dBm from IQ samples."""
    if len(cap.samples) == 0:
        return -120.0
    power = np.mean(np.abs(cap.samples) ** 2)
    return float(10 * np.log10(max(power, 1e-20)) + 30)


def _compute_interference_metric(cap: CaptureResult) -> float:
    """
    Compute interference metric from spectral flatness.

    A clean GPS band should have roughly flat noise floor.
    Strong narrowband interference produces spectral peaks.
    Returns ratio of peak PSD to median PSD (higher = more interference).
    """
    if len(cap.samples) < 1024:
        return 0.0

    psd = np.abs(np.fft.fft(cap.samples[:4096])) ** 2
    median_psd = np.median(psd)
    if median_psd <= 0:
        return 0.0
    return float(np.max(psd) / median_psd)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scan(
    sdr: HackRFCapture,
    duration: float = 2.0,
) -> GNSSFingerprint:
    """
    Run a GPS/GNSS scan.

    Captures L1 band for C/A code correlation and L5 band for energy
    detection. Returns satellite visibility, signal metrics, and
    interference assessment.
    """
    fp = GNSSFingerprint()
    total_duration = 0.0

    # ── L1 band (1575.42 MHz) — C/A code correlation ─────────────
    log.info("GNSS scan: L1 (%.2f MHz)", L1_FREQ / 1e6)
    cap_l1 = sdr.capture(
        duration=duration,
        center_freq=L1_FREQ,
        sample_rate=SAMPLE_RATE,
        bandwidth=BANDWIDTH,
        gain=GAIN,
    )
    total_duration += cap_l1.duration

    if len(cap_l1.samples) > 0:
        fp.l1_band_power_dbm = _measure_band_power(cap_l1)
        fp.interference_metric = _compute_interference_metric(cap_l1)
        log.info("  L1 band power: %.1f dBm, interference: %.2f",
                 fp.l1_band_power_dbm, fp.interference_metric)

        satellites = _search_l1(cap_l1)
        fp.satellites = satellites
        fp.satellite_count = len(satellites)
        log.info("  L1: %d satellites acquired", fp.satellite_count)
    else:
        log.warning("  Empty L1 capture")

    # ── L5 band (1176.45 MHz) — energy detection only ────────────
    log.info("GNSS scan: L5 (%.2f MHz)", L5_FREQ / 1e6)
    cap_l5 = sdr.capture(
        duration=duration,
        center_freq=L5_FREQ,
        sample_rate=SAMPLE_RATE,
        bandwidth=BANDWIDTH,
        gain=GAIN,
    )
    total_duration += cap_l5.duration

    if len(cap_l5.samples) > 0:
        fp.l5_band_power_dbm = _measure_band_power(cap_l5)
        # L5 is present if band power is significantly above thermal noise
        # Typical thermal noise at 2 MHz BW ~ -111 dBm
        fp.l5_present = fp.l5_band_power_dbm > -105.0
        log.info("  L5 band power: %.1f dBm, present: %s",
                 fp.l5_band_power_dbm, fp.l5_present)
    else:
        log.warning("  Empty L5 capture")

    fp.capture_duration = total_duration
    log.info("GNSS scan complete: %d satellites, L1=%.1f dBm, L5=%.1f dBm",
             fp.satellite_count, fp.l1_band_power_dbm, fp.l5_band_power_dbm)
    return fp


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SpectralDNA GPS/GNSS fingerprint module")
    parser.add_argument("--duration", type=float, default=2.0,
                        help="Seconds per band (default: 2)")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    sdr = HackRFCapture()
    sdr.open()
    try:
        fp = scan(sdr, args.duration)
    finally:
        sdr.close()

    result = {"identifiers": fp.identifiers(), "rf_fingerprint": fp.rf_fingerprint()}
    print(json.dumps(result, indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
