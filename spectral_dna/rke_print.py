"""
RKE (Remote Keyless Entry) fingerprint module — 315 MHz ASK.

Extracts:
  - Rolling code sequences (encrypted counter values)
  - Modulation pattern (bit timing, encoding scheme)
  - Power ramp profile — physical-layer fingerprint:
    - Rise time
    - Overshoot percentage
    - Settling time

Capture strategy:
  ASK demodulation at 315 MHz.  RKE fobs transmit brief bursts when
  a button is pressed; this module captures passively and analyzes
  any transmissions observed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import find_peaks

from .capture import (
    CaptureResult,
    HackRFCapture,
    envelope_detect,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CENTER_FREQ = 315e6
SAMPLE_RATE = 2e6     # HackRF minimum 2 Msps
BANDWIDTH = 2e6
GAIN = 40.0

# Common RKE bit rates
TYPICAL_BIT_RATES = [1000, 2000, 2500, 3000, 4000, 5000, 10000]


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class RKEPowerProfile:
    """Power amplifier ramp characteristics."""
    rise_time_us: float = 0.0       # 10% → 90% rise time
    overshoot_pct: float = 0.0      # overshoot as % of steady-state
    fall_time_us: float = 0.0       # 90% → 10% fall time
    settling_time_us: float = 0.0   # time to ±5% of steady-state
    steady_state_dbm: float = -100.0


@dataclass
class RKETransmission:
    """A single RKE transmission burst."""
    timestamp_s: float = 0.0
    rolling_code_hex: str = ""
    fixed_code_hex: str = ""
    bit_count: int = 0
    bit_rate_bps: int = 0
    encoding: str = ""              # PWM, Manchester, etc.
    power_profile: RKEPowerProfile = field(default_factory=RKEPowerProfile)
    raw_bits: str = ""


@dataclass
class RKEFingerprint:
    transmissions: list[RKETransmission] = field(default_factory=list)
    capture_duration: float = 0.0

    def identifiers(self) -> dict:
        out = {}
        for i, tx in enumerate(self.transmissions):
            p = f"rke_tx{i}"
            if tx.rolling_code_hex:
                out[f"{p}_rolling_code"] = tx.rolling_code_hex
            if tx.fixed_code_hex:
                out[f"{p}_fixed_code"] = tx.fixed_code_hex
            out[f"{p}_bit_count"] = str(tx.bit_count)
            out[f"{p}_encoding"] = tx.encoding
        return out

    def rf_fingerprint(self) -> dict:
        out = {}
        for i, tx in enumerate(self.transmissions):
            p = f"rke_tx{i}"
            pp = tx.power_profile
            out[f"{p}_rise_time_us"] = f"{pp.rise_time_us:.2f}"
            out[f"{p}_overshoot_pct"] = f"{pp.overshoot_pct:.1f}"
            out[f"{p}_fall_time_us"] = f"{pp.fall_time_us:.2f}"
            out[f"{p}_settling_time_us"] = f"{pp.settling_time_us:.2f}"
            out[f"{p}_power_dbm"] = f"{pp.steady_state_dbm:.1f}"
            out[f"{p}_bit_rate_bps"] = str(tx.bit_rate_bps)
        return out

    def hash_material(self) -> str:
        parts = []
        for tx in self.transmissions:
            if tx.rolling_code_hex:
                parts.append(f"rc:{tx.rolling_code_hex}")
            pp = tx.power_profile
            parts.append(f"rise:{pp.rise_time_us:.4f}")
            parts.append(f"overshoot:{pp.overshoot_pct:.2f}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# ASK demodulation and burst detection
# ---------------------------------------------------------------------------

def _detect_bursts(
    samples: np.ndarray, sample_rate: float
) -> list[tuple[int, int]]:
    """
    Detect RKE transmission bursts in captured IQ.
    Returns list of (start_sample, end_sample) for each burst.
    """
    envelope = np.abs(samples)

    # Smoothing filter
    window = int(sample_rate * 100e-6)  # 100 µs window
    if window < 1:
        window = 1
    kernel = np.ones(window) / window
    smoothed = np.convolve(envelope, kernel, mode="same")

    # Threshold — bursts should be well above noise
    noise_floor = np.median(smoothed)
    threshold = noise_floor * 8

    # Find burst regions
    above = smoothed > threshold
    changes = np.diff(above.astype(np.int8))
    starts = np.where(changes == 1)[0]
    ends = np.where(changes == -1)[0]

    # Pair starts and ends
    bursts = []
    for s in starts:
        matching_ends = ends[ends > s]
        if len(matching_ends) > 0:
            e = matching_ends[0]
            duration_ms = (e - s) / sample_rate * 1000
            if 5 < duration_ms < 500:  # RKE bursts are typically 10-200 ms
                bursts.append((int(s), int(e)))

    return bursts


def _analyze_power_profile(
    envelope: np.ndarray, sample_rate: float
) -> RKEPowerProfile:
    """
    Analyze the power amplifier ramp-up/ramp-down profile of a burst.
    Measures rise time, overshoot, fall time, and settling time.
    """
    if len(envelope) < 10:
        return RKEPowerProfile()

    # Smooth for profile analysis
    window = max(int(sample_rate * 5e-6), 3)  # 5 µs smoothing
    kernel = np.ones(window) / window
    smooth = np.convolve(envelope, kernel, mode="same")

    peak = np.max(smooth)
    if peak < 1e-6:
        return RKEPowerProfile()

    # Steady-state estimate (middle 60% of burst)
    mid_start = len(smooth) // 5
    mid_end = 4 * len(smooth) // 5
    steady_state = np.mean(smooth[mid_start:mid_end])
    steady_state_dbm = 10 * np.log10(max(steady_state ** 2, 1e-20)) + 30

    sample_period_us = 1e6 / sample_rate

    # Rise time: 10% to 90% on leading edge
    level_10 = steady_state * 0.1
    level_90 = steady_state * 0.9
    rise_start_idx = 0
    for i in range(len(smooth)):
        if smooth[i] > level_10:
            rise_start_idx = i
            break
    rise_end_idx = rise_start_idx
    for i in range(rise_start_idx, len(smooth)):
        if smooth[i] > level_90:
            rise_end_idx = i
            break
    rise_time_us = (rise_end_idx - rise_start_idx) * sample_period_us

    # Overshoot
    overshoot_pct = 0.0
    if steady_state > 0 and peak > steady_state:
        overshoot_pct = (peak - steady_state) / steady_state * 100

    # Fall time: 90% to 10% on trailing edge
    fall_start_idx = len(smooth) - 1
    for i in range(len(smooth) - 1, 0, -1):
        if smooth[i] > level_90:
            fall_start_idx = i
            break
    fall_end_idx = fall_start_idx
    for i in range(fall_start_idx, len(smooth) - 1):
        if smooth[i] < level_10:
            fall_end_idx = i
            break
    fall_time_us = (fall_end_idx - fall_start_idx) * sample_period_us

    # Settling time: time for signal to stay within ±5% of steady-state
    settling_band = steady_state * 0.05
    settling_idx = rise_end_idx
    for i in range(rise_end_idx, mid_end):
        if abs(smooth[i] - steady_state) > settling_band:
            settling_idx = i
    settling_time_us = (settling_idx - rise_start_idx) * sample_period_us

    return RKEPowerProfile(
        rise_time_us=rise_time_us,
        overshoot_pct=overshoot_pct,
        fall_time_us=fall_time_us,
        settling_time_us=settling_time_us,
        steady_state_dbm=steady_state_dbm,
    )


def _decode_burst(
    samples: np.ndarray, sample_rate: float
) -> tuple[str, str, int, int, str]:
    """
    Decode an RKE burst to extract code bits.

    Returns: (rolling_code_hex, fixed_code_hex, bit_count, bit_rate, encoding)
    """
    envelope = np.abs(samples)

    # Adaptive threshold
    threshold = np.mean(envelope) * 0.7
    binary = (envelope > threshold).astype(np.uint8)

    # Measure shortest pulse to determine bit rate
    changes = np.diff(binary.astype(np.int8))
    edges = np.where(changes != 0)[0]

    if len(edges) < 6:
        return "", "", 0, 0, "unknown"

    run_lengths = np.diff(edges)
    valid_runs = run_lengths[run_lengths > 2]
    if len(valid_runs) == 0:
        return "", "", 0, 0, "unknown"

    min_run = np.min(valid_runs)
    bit_rate = int(sample_rate / min_run)
    best_rate = min(TYPICAL_BIT_RATES, key=lambda r: abs(r - bit_rate))
    samples_per_bit = int(sample_rate / best_rate)

    # Determine encoding by analyzing pulse width distribution
    run_hist = np.histogram(valid_runs, bins=5)[0]

    # Check for PWM encoding (two distinct pulse widths)
    unique_widths = len(set(round(r / min_run) for r in valid_runs if r > 2))
    encoding = "PWM" if unique_widths <= 3 else "Manchester"

    # Sample at bit centers
    bits = []
    for i in range(0, len(binary) - samples_per_bit, samples_per_bit):
        center = i + samples_per_bit // 2
        if center < len(binary):
            bits.append(int(binary[center]))

    if not bits:
        return "", "", 0, best_rate, encoding

    # Convert to hex
    # Many RKE formats: [preamble] [fixed code: 28 bits] [rolling code: 32 bits]
    bit_str = "".join(str(b) for b in bits)
    bit_count = len(bits)

    # Attempt to split into fixed and rolling portions
    # Common KeeLoq format: 66 bits total (34 encrypted + 28 serial + 4 button)
    fixed_hex = ""
    rolling_hex = ""

    if bit_count >= 60:
        # Rolling code (first 32 bits after preamble)
        rc_bits = bits[:32]
        rc_val = 0
        for b in rc_bits:
            rc_val = (rc_val << 1) | b
        rolling_hex = f"{rc_val:08X}"

        # Fixed code (next 28 bits)
        fc_bits = bits[32:60]
        fc_val = 0
        for b in fc_bits:
            fc_val = (fc_val << 1) | b
        fixed_hex = f"{fc_val:07X}"
    elif bit_count > 0:
        # Unknown format — dump all as rolling code
        val = 0
        for b in bits[:min(64, bit_count)]:
            val = (val << 1) | b
        rolling_hex = f"{val:0{min(16, bit_count // 4)}X}"

    return rolling_hex, fixed_hex, bit_count, best_rate, encoding


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scan(
    sdr: HackRFCapture,
    duration: float = 60.0,
) -> RKEFingerprint:
    """
    Capture and analyze RKE transmissions at 315 MHz.

    RKE signals are event-driven (button press), so captures may contain
    zero transmissions.  Longer capture durations increase the chance
    of observing a key fob transmission.
    """
    log.info("RKE scan: 315 MHz, %d s capture", int(duration))

    fp = RKEFingerprint()

    cap = sdr.capture(
        duration=duration,
        center_freq=CENTER_FREQ,
        sample_rate=SAMPLE_RATE,
        bandwidth=BANDWIDTH,
        gain=GAIN,
    )
    fp.capture_duration = cap.duration

    if len(cap.samples) == 0:
        log.info("No RKE capture data (frequency out of range?)")
        return fp

    # Detect bursts
    bursts = _detect_bursts(cap.samples, SAMPLE_RATE)
    log.info("Detected %d RKE bursts", len(bursts))

    for start, end in bursts:
        burst_samples = cap.samples[start:end]
        burst_envelope = np.abs(burst_samples)
        timestamp = start / SAMPLE_RATE

        # Power profile analysis
        power_profile = _analyze_power_profile(burst_envelope, SAMPLE_RATE)

        # Decode
        rolling, fixed, bit_count, bit_rate, encoding = _decode_burst(
            burst_samples, SAMPLE_RATE
        )

        tx = RKETransmission(
            timestamp_s=timestamp,
            rolling_code_hex=rolling,
            fixed_code_hex=fixed,
            bit_count=bit_count,
            bit_rate_bps=bit_rate,
            encoding=encoding,
            power_profile=power_profile,
        )
        fp.transmissions.append(tx)

    log.info("RKE scan complete: %d transmissions", len(fp.transmissions))
    return fp


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SpectralDNA RKE fingerprint module")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="Capture duration in seconds (default: 60)")
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
