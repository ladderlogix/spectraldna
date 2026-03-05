"""
TPMS (Tire Pressure Monitoring System) fingerprint module — 315 MHz OOK.

Extracts:
  - Per-tire sensor IDs (FL / FR / RL / RR)
  - Pressure values (PSI/kPa)
  - Temperature values (°C/°F)
  - Broadcast interval — physical-layer fingerprint

Capture strategy:
  OOK (On-Off Keying) demodulation at 315 MHz.
  Most US-market TPMS sensors use Manchester-coded OOK at ~4–10 kbps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, sosfilt, find_peaks

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
SAMPLE_RATE = 2e6     # 2 Msps — HackRF minimum; plenty for OOK at 4–10 kbps
BANDWIDTH = 2e6
GAIN = 40.0

# Typical TPMS bit rates
TYPICAL_BIT_RATES = [4000, 4800, 5000, 8000, 9600, 10000]

# Known TPMS preamble patterns (common across many manufacturers)
PREAMBLE_PATTERNS = [
    "11111111001",     # Schrader
    "111111110101",    # Continental
    "11111111111100",  # Pacific/Sensata
]

# Tire position labels
TIRE_POSITIONS = ["FL", "FR", "RL", "RR"]


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class TPMSSensor:
    sensor_id: str = ""
    position: str = ""              # FL/FR/RL/RR (inferred from order)
    pressure_psi: float = 0.0
    pressure_kpa: float = 0.0
    temperature_c: float = 0.0
    temperature_f: float = 0.0
    battery_ok: bool = True
    broadcast_interval_s: float = 0.0
    raw_frame_hex: str = ""


@dataclass
class TPMSFingerprint:
    sensors: list[TPMSSensor] = field(default_factory=list)
    capture_duration: float = 0.0
    detected_bit_rate: int = 0

    def identifiers(self) -> dict:
        out = {}
        for i, s in enumerate(self.sensors):
            pos = s.position or TIRE_POSITIONS[i % 4]
            p = f"tpms_{pos}"
            out[f"{p}_sensor_id"] = s.sensor_id
            out[f"{p}_pressure_psi"] = f"{s.pressure_psi:.1f}"
            out[f"{p}_pressure_kpa"] = f"{s.pressure_kpa:.1f}"
            out[f"{p}_temp_c"] = f"{s.temperature_c:.1f}"
            out[f"{p}_temp_f"] = f"{s.temperature_f:.1f}"
        return out

    def rf_fingerprint(self) -> dict:
        out = {}
        for i, s in enumerate(self.sensors):
            pos = s.position or TIRE_POSITIONS[i % 4]
            out[f"tpms_{pos}_interval_s"] = f"{s.broadcast_interval_s:.2f}"
        if self.detected_bit_rate:
            out["tpms_bit_rate_bps"] = str(self.detected_bit_rate)
        return out

    def hash_material(self) -> str:
        parts = []
        for s in sorted(self.sensors, key=lambda x: x.sensor_id):
            parts.append(f"id:{s.sensor_id}")
            parts.append(f"psi:{s.pressure_psi:.2f}")
            parts.append(f"t:{s.temperature_c:.1f}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# OOK demodulation
# ---------------------------------------------------------------------------

def _ook_demodulate(
    samples: np.ndarray, sample_rate: float
) -> tuple[np.ndarray, float]:
    """
    OOK demodulation via envelope detection + adaptive thresholding.
    Returns (bit_stream, estimated_bit_rate).
    """
    # Envelope detection
    envelope = np.abs(samples)

    # Low-pass filter the envelope
    cutoff = 15000 / (sample_rate / 2)  # 15 kHz LPF
    sos = butter(4, min(cutoff, 0.99), btype="low", output="sos")
    envelope = sosfilt(sos, envelope)

    # Adaptive threshold using moving average
    window = int(sample_rate * 0.002)  # 2 ms window
    if window < 1:
        window = 1
    kernel = np.ones(window) / window
    moving_avg = np.convolve(envelope, kernel, mode="same")
    threshold = moving_avg * 1.5

    # Binary decision
    binary = (envelope > threshold).astype(np.uint8)

    # Estimate bit rate from run-length analysis
    changes = np.diff(binary.astype(np.int8))
    edges = np.where(changes != 0)[0]

    if len(edges) < 4:
        return binary, 0

    run_lengths = np.diff(edges)
    min_run = np.min(run_lengths[run_lengths > 2])
    estimated_bit_rate = int(sample_rate / min_run)

    # Find closest standard bit rate
    best_rate = min(TYPICAL_BIT_RATES, key=lambda r: abs(r - estimated_bit_rate))

    return binary, best_rate


def _manchester_decode(binary: np.ndarray, samples_per_bit: int) -> np.ndarray:
    """
    Manchester decode: each bit period is split into two halves.
    Rising edge (0→1) = 1, Falling edge (1→0) = 0.
    """
    bits = []
    half = samples_per_bit // 2
    i = 0
    while i + samples_per_bit <= len(binary):
        first_half = np.mean(binary[i : i + half])
        second_half = np.mean(binary[i + half : i + samples_per_bit])

        if first_half < 0.5 and second_half > 0.5:
            bits.append(1)
        elif first_half > 0.5 and second_half < 0.5:
            bits.append(0)
        # else: invalid transition, skip

        i += samples_per_bit

    return np.array(bits, dtype=np.uint8)


def _find_preamble(bits: np.ndarray) -> int:
    """Find the start of a TPMS frame by looking for known preamble patterns."""
    bit_str = "".join(str(b) for b in bits)

    for pattern in PREAMBLE_PATTERNS:
        idx = bit_str.find(pattern)
        if idx >= 0:
            return idx + len(pattern)

    # Fallback: look for long run of 1s followed by a 0
    for i in range(len(bits) - 8):
        if all(bits[i : i + 8] == 1) and bits[i + 8] == 0:
            return i + 9

    return -1


def _parse_tpms_frame(bits: np.ndarray) -> dict | None:
    """
    Parse a generic TPMS frame.
    Common format (varies by manufacturer):
      [Sensor ID: 28-32 bits] [Flags: 4-8 bits] [Pressure: 8 bits] [Temp: 8 bits] [CRC: 8 bits]
    """
    if len(bits) < 40:
        return None

    # Extract sensor ID (first 32 bits)
    sensor_id_bits = bits[:32]
    sensor_id = 0
    for b in sensor_id_bits:
        sensor_id = (sensor_id << 1) | int(b)

    # Flags (next 8 bits)
    flag_bits = bits[32:40] if len(bits) > 40 else np.zeros(8, dtype=np.uint8)
    battery_ok = bool(flag_bits[0]) if len(flag_bits) > 0 else True

    # Pressure (next 8 bits) — typically in 0.25 kPa or 0.363 PSI increments
    pressure_raw = 0
    if len(bits) >= 48:
        for b in bits[40:48]:
            pressure_raw = (pressure_raw << 1) | int(b)

    # Temperature (next 8 bits) — typically offset by -40°C or -50°C
    temp_raw = 0
    if len(bits) >= 56:
        for b in bits[48:56]:
            temp_raw = (temp_raw << 1) | int(b)

    # Convert to physical units (Schrader-style encoding)
    pressure_kpa = pressure_raw * 0.25
    pressure_psi = pressure_kpa * 0.145038
    temperature_c = temp_raw - 40.0
    temperature_f = temperature_c * 9 / 5 + 32

    # Raw frame hex
    frame_bytes = bytearray()
    for i in range(0, min(len(bits), 72), 8):
        byte = 0
        for j in range(min(8, len(bits) - i)):
            byte = (byte << 1) | int(bits[i + j])
        frame_bytes.append(byte)

    return {
        "sensor_id": f"{sensor_id:08X}",
        "pressure_psi": pressure_psi,
        "pressure_kpa": pressure_kpa,
        "temperature_c": temperature_c,
        "temperature_f": temperature_f,
        "battery_ok": battery_ok,
        "raw_hex": frame_bytes.hex().upper(),
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scan(
    sdr: HackRFCapture,
    duration: float = 30.0,
) -> TPMSFingerprint:
    """
    Capture and decode TPMS signals at 315 MHz.

    TPMS sensors broadcast infrequently (every 30-60 s), so longer capture
    durations are recommended.  For a parked vehicle, sensors may broadcast
    more frequently when pressure changes are detected.
    """
    log.info("TPMS scan: 315 MHz, %d s capture", int(duration))

    fp = TPMSFingerprint()

    cap = sdr.capture(
        duration=duration,
        center_freq=CENTER_FREQ,
        sample_rate=SAMPLE_RATE,
        bandwidth=BANDWIDTH,
        gain=GAIN,
    )
    fp.capture_duration = cap.duration

    if len(cap.samples) == 0:
        log.info("No TPMS capture data (frequency out of range?)")
        return fp

    # OOK demodulation
    binary, bit_rate = _ook_demodulate(cap.samples, SAMPLE_RATE)
    fp.detected_bit_rate = bit_rate
    log.info("Estimated bit rate: %d bps", bit_rate)

    if bit_rate == 0:
        log.info("No TPMS signals detected")
        return fp

    samples_per_bit = int(SAMPLE_RATE / bit_rate)

    # Manchester decode
    bits = _manchester_decode(binary, samples_per_bit)
    log.info("Decoded %d Manchester bits", len(bits))

    # Find and parse frames
    seen_ids: dict[str, list[float]] = {}  # sensor_id -> list of timestamps
    pos = 0

    while pos < len(bits) - 40:
        frame_start = _find_preamble(bits[pos:])
        if frame_start < 0:
            break
        pos += frame_start

        parsed = _parse_tpms_frame(bits[pos:])
        if parsed is None:
            pos += 1
            continue

        sid = parsed["sensor_id"]
        approx_time = pos * (1 / bit_rate)

        if sid not in seen_ids:
            seen_ids[sid] = []
            sensor = TPMSSensor(
                sensor_id=sid,
                position=TIRE_POSITIONS[len(fp.sensors) % 4],
                pressure_psi=parsed["pressure_psi"],
                pressure_kpa=parsed["pressure_kpa"],
                temperature_c=parsed["temperature_c"],
                temperature_f=parsed["temperature_f"],
                battery_ok=parsed["battery_ok"],
                raw_frame_hex=parsed["raw_hex"],
            )
            fp.sensors.append(sensor)

        seen_ids[sid].append(approx_time)
        pos += 56  # skip past this frame

    # Calculate broadcast intervals
    for sensor in fp.sensors:
        timestamps = seen_ids.get(sensor.sensor_id, [])
        if len(timestamps) >= 2:
            intervals = np.diff(sorted(timestamps))
            sensor.broadcast_interval_s = float(np.mean(intervals))

    log.info("TPMS scan complete: %d sensors", len(fp.sensors))
    return fp


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SpectralDNA TPMS fingerprint module")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Capture duration in seconds (default: 30)")
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
