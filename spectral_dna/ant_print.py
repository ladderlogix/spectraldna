"""
ANT/ANT+ fingerprint module — 2.457 / 2.466 GHz ISM band.

Extracts:
  - Device number (16-bit) and device type (HR, Bike, Power, etc.)
  - Transmission type
  - ANT+ device profile
  - TX interval and jitter
  - Clock drift (ppm) — physical-layer fingerprint
  - RSSI estimate

Capture strategy:
  GFSK demodulation at 4 Msps (1 Mbps symbol rate, same as BLE).
  ANT uses 0xA4 sync byte; two-stage validation (sync + checksum) to
  filter false positives from the short 8-bit sync pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .capture import (
    CaptureResult,
    HackRFCapture,
    fm_demodulate,
)
from .lookups import lookup_ant_device_type

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANT_FREQUENCIES = {
    "default": 2.457e9,   # ANT+ default RF frequency (ch 57)
    "alt":     2.466e9,   # Common alternate (ch 66)
}

SAMPLE_RATE = 4e6     # 4 Msps — 4 samples/symbol for 1 Mbps GFSK
BANDWIDTH = 2e6
GAIN = 40.0

SYMBOL_RATE = 1e6
SAMPLES_PER_SYMBOL = SAMPLE_RATE / SYMBOL_RATE  # 4

ANT_SYNC = 0xA4  # ANT message sync byte

# ANT+ device type codes
DEVICE_TYPES = {
    0x78: "Heart Rate",
    0x79: "Speed & Cadence",
    0x7A: "Cadence",
    0x7B: "Speed",
    0x0B: "Power",
    0x11: "Fitness Equipment",
    0x19: "Multi-Sport Speed & Distance",
    0x0F: "Environment",
    0x12: "Weight Scale",
    0x22: "Geocache",
    0x23: "Shifting",
    0x28: "Suspension",
}

# ANT message IDs
MSG_BROADCAST_DATA = 0x4E
MSG_ACKNOWLEDGED_DATA = 0x4F
MSG_BURST_TRANSFER = 0x50


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class ANTDevice:
    device_number: int = 0          # 16-bit device number
    device_type: int = 0            # 8-bit device type code
    device_type_name: str = ""
    transmission_type: int = 0      # 8-bit
    profile: str = ""               # e.g. "Heart Rate", "Power"
    channel: str = ""               # frequency label
    timestamps: list[float] = field(default_factory=list)
    mean_interval_ms: float = 0.0
    interval_jitter_ms: float = 0.0
    clock_drift_ppm: float = 0.0
    rssi_dbm: float = -100.0


@dataclass
class ANTFingerprint:
    devices: dict[str, ANTDevice] = field(default_factory=dict)
    scan_frequencies: list[str] = field(default_factory=list)
    capture_duration: float = 0.0

    def identifiers(self) -> dict:
        out = {}
        for i, (key, dev) in enumerate(self.devices.items()):
            p = f"ant_dev{i}"
            out[f"{p}_device_number"] = str(dev.device_number)
            out[f"{p}_device_type"] = f"0x{dev.device_type:02X}"
            if dev.device_type_name:
                out[f"{p}_profile"] = dev.device_type_name
            out[f"{p}_transmission_type"] = f"0x{dev.transmission_type:02X}"
        return out

    def rf_fingerprint(self) -> dict:
        out = {}
        for i, (key, dev) in enumerate(self.devices.items()):
            p = f"ant_dev{i}"
            out[f"{p}_mean_interval_ms"] = f"{dev.mean_interval_ms:.2f}"
            out[f"{p}_interval_jitter_ms"] = f"{dev.interval_jitter_ms:.3f}"
            out[f"{p}_clock_drift_ppm"] = f"{dev.clock_drift_ppm:+.2f}"
            out[f"{p}_rssi_dbm"] = f"{dev.rssi_dbm:.1f}"
        return out

    def hash_material(self) -> str:
        parts = []
        for key in sorted(self.devices.keys()):
            dev = self.devices[key]
            parts.append(f"{dev.device_number:04X}:{dev.device_type:02X}")
            parts.append(f"drift:{dev.clock_drift_ppm:.4f}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# ANT message parsing
# ---------------------------------------------------------------------------

def _get_sync_bits() -> np.ndarray:
    """Return ANT sync byte 0xA4 as LSbit-first bit array."""
    val = ANT_SYNC
    bits = []
    for _ in range(8):
        bits.append(val & 1)
        val >>= 1
    return np.array(bits, dtype=np.uint8)


def _bits_to_bytes(bits: np.ndarray) -> bytes:
    """Convert LSbit-first bit array to bytes."""
    n_bytes = len(bits) // 8
    result = bytearray(n_bytes)
    for i in range(n_bytes):
        byte = 0
        for j in range(8):
            byte |= int(bits[i * 8 + j]) << j
        result[i] = byte
    return bytes(result)


def _validate_ant_message(data: bytes) -> dict | None:
    """
    Validate and parse an ANT message.

    Format: Sync(0xA4) + Length + MsgID + Channel + Payload... + Checksum
    Checksum = XOR of all bytes from Sync through last payload byte.
    """
    if len(data) < 5:
        return None

    sync = data[0]
    if sync != ANT_SYNC:
        return None

    msg_len = data[1]
    if msg_len > 13 or msg_len < 1:
        return None

    total_len = 4 + msg_len  # sync + len + msgid + channel + payload + checksum
    if len(data) < total_len:
        return None

    msg_id = data[2]
    channel = data[3]

    # Verify checksum (XOR of all bytes except checksum itself)
    xor = 0
    for b in data[:total_len - 1]:
        xor ^= b
    checksum = data[total_len - 1]
    if xor != checksum:
        return None

    payload = data[4:total_len - 1]

    return {
        "msg_id": msg_id,
        "channel": channel,
        "payload": payload,
        "msg_len": msg_len,
    }


def _parse_broadcast(payload: bytes) -> dict | None:
    """
    Parse ANT+ broadcast data page to extract device identifiers.

    In ANT+ profiles, page 80 (0x50) = Manufacturer's Info,
    page 81 (0x51) = Product Info. Regular data pages carry sensor data
    with the device number encoded in the channel ID (not in payload).
    """
    if len(payload) < 8:
        return None

    page = payload[0]
    return {
        "page": page,
        "data": payload[1:],
    }


# ---------------------------------------------------------------------------
# Demodulation and extraction
# ---------------------------------------------------------------------------

def _extract_ant_packets(
    cap: CaptureResult,
    freq_label: str,
) -> list[tuple[dict, float]]:
    """
    Extract ANT packets from IQ capture using burst detection + GFSK demod.

    Returns list of (parsed_message, timestamp_seconds) tuples.
    """
    if len(cap.samples) == 0:
        return []

    # Burst detection — ANT packets are ~100–500 µs
    # Downsample envelope for speed on large captures
    sps = int(SAMPLES_PER_SYMBOL)
    ds_factor = max(1, sps // 2)  # downsample 2x — still fine for burst edges
    envelope = np.abs(cap.samples[::ds_factor])
    noise_floor = np.median(envelope)
    burst_thresh = noise_floor * 4

    above = envelope > burst_thresh
    changes = np.diff(above.astype(np.int8))
    starts_ds = np.where(changes == 1)[0]
    ends_ds = np.where(changes == -1)[0]

    # Scale back to original sample indices
    starts = starts_ds * ds_factor
    ends = ends_ds * ds_factor

    min_burst = int(SAMPLE_RATE * 80e-6)
    max_burst = int(SAMPLE_RATE * 600e-6)

    # Pair starts/ends efficiently using sorted merge
    bursts = []
    end_idx = 0
    for s in starts:
        # Advance end_idx to first end past this start
        while end_idx < len(ends) and ends[end_idx] <= s:
            end_idx += 1
        if end_idx >= len(ends):
            break
        e = ends[end_idx]
        length = e - s
        if min_burst <= length <= max_burst:
            s_pad = max(0, s - sps * 8)
            e_pad = min(len(cap.samples), e + sps * 8)
            bursts.append((s_pad, e_pad))

    # Cap burst count to avoid hanging on noisy ISM band
    MAX_BURSTS = 500
    if len(bursts) > MAX_BURSTS:
        log.info("  %s: capping %d bursts to %d", freq_label, len(bursts), MAX_BURSTS)
        bursts = bursts[:MAX_BURSTS]

    log.info("  %s: processing %d candidate bursts", freq_label, len(bursts))

    sync_bits = _get_sync_bits()
    # Pre-compute matched filter once outside the loop
    sync_float = sync_bits.astype(np.float32)
    ref_signal = np.repeat(sync_float * 2 - 1, sps)
    ref_len = len(ref_signal)

    packets = []

    for burst_start, burst_end in bursts:
        burst = cap.samples[burst_start:burst_end]
        if len(burst) < 200:
            continue

        freq_dev = fm_demodulate(burst)
        if len(freq_dev) < 40:
            continue

        freq_dev -= np.mean(freq_dev)
        max_dev = np.max(np.abs(freq_dev))
        if max_dev <= 0:
            continue
        freq_dev_norm = freq_dev / max_dev

        if len(freq_dev_norm) <= ref_len:
            continue

        corr = np.abs(np.correlate(freq_dev_norm, ref_signal, mode="valid"))
        if len(corr) == 0:
            continue

        corr_thresh = np.max(corr) * 0.6
        peak_positions = np.where(corr > corr_thresh)[0]
        if len(peak_positions) == 0:
            continue

        # Cluster peaks
        clusters = []
        cluster = [peak_positions[0]]
        for p in peak_positions[1:]:
            if p - cluster[-1] < sps * 10:
                cluster.append(p)
            else:
                clusters.append(cluster)
                cluster = [p]
        clusters.append(cluster)

        for cl in clusters:
            best_peak = cl[np.argmax(corr[cl])]

            # Data starts after sync byte (8 bits * sps samples)
            data_start = best_peak + len(ref_signal)
            if data_start + sps * 8 > len(freq_dev_norm):
                continue

            # Calibrate sampling phase against known sync bits
            ref_region = freq_dev_norm[best_peak:best_peak + len(ref_signal)]
            if len(ref_region) < len(ref_signal):
                continue

            best_phase = 0
            best_score = -1
            for phase in range(sps):
                sampled = ref_region[phase::sps]
                trial = (sampled > 0).astype(np.uint8)
                expected = sync_bits
                if len(trial) >= len(expected):
                    score = int(np.sum(trial[:len(expected)] == expected))
                    if score > best_score:
                        best_score = score
                        best_phase = phase

            # Require at least 7/8 correct sync bits
            if best_score < 7:
                continue

            # Sample data bits — max ANT message is 17 bytes (sync + 1 len + 1 msgid
            # + 1 ch + up to 13 payload + 1 checksum = 17)
            # We already decoded sync, so read remaining 16 bytes = 128 bits
            data_region = freq_dev_norm[data_start + best_phase::sps]
            if len(data_region) < 128:
                continue

            msg_bits = (data_region[:128] > 0).astype(np.uint8)
            msg_bytes = _bits_to_bytes(msg_bits)

            # Prepend sync byte for validation
            full_msg = bytes([ANT_SYNC]) + msg_bytes

            parsed = _validate_ant_message(full_msg)
            if parsed is None:
                continue

            ts = burst_start / SAMPLE_RATE
            packets.append((parsed, ts))

    return packets


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scan(
    sdr: HackRFCapture,
    duration_per_freq: float = 10.0,
    frequencies: dict[str, float] | None = None,
) -> ANTFingerprint:
    """
    Run an ANT/ANT+ scan.

    Captures IQ on each ANT frequency, demodulates GFSK, and extracts
    ANT messages with device identifiers and timing fingerprints.
    """
    if frequencies is None:
        frequencies = ANT_FREQUENCIES

    fp = ANTFingerprint()
    fp.scan_frequencies = list(frequencies.keys())
    total_duration = 0.0

    for freq_label, freq in frequencies.items():
        log.info("ANT scan: %s (%.3f MHz)", freq_label, freq / 1e6)

        cap = sdr.capture(
            duration=duration_per_freq,
            center_freq=freq,
            sample_rate=SAMPLE_RATE,
            bandwidth=BANDWIDTH,
            gain=GAIN,
        )
        total_duration += cap.duration

        if len(cap.samples) == 0:
            log.warning("  Empty capture on %s", freq_label)
            continue

        packets = _extract_ant_packets(cap, freq_label)
        log.info("  %s: extracted %d valid ANT messages", freq_label, len(packets))

        # Group by message source — use channel number as grouping key
        # since device number lives in channel ID assignment, not in every packet
        device_timestamps: dict[int, list[float]] = {}
        device_msgs: dict[int, list[dict]] = {}

        for msg, ts in packets:
            ch = msg["channel"]
            device_timestamps.setdefault(ch, []).append(ts)
            device_msgs.setdefault(ch, []).append(msg)

        # Process broadcast messages to extract device info
        for ch, msgs in device_msgs.items():
            device_number = 0
            device_type_code = 0
            transmission_type = 0

            for msg in msgs:
                if msg["msg_id"] in (MSG_BROADCAST_DATA, MSG_ACKNOWLEDGED_DATA):
                    payload = msg["payload"]
                    parsed = _parse_broadcast(payload)
                    if parsed is None:
                        continue

                    # ANT+ common page 80 (0x50): Manufacturer's Information
                    if parsed["page"] == 0x50 and len(parsed["data"]) >= 7:
                        d = parsed["data"]
                        # bytes 3-4: serial number (lower 16 bits)
                        device_number = d[3] | (d[4] << 8)

                    # ANT+ common page 81 (0x51): Product Information
                    elif parsed["page"] == 0x51 and len(parsed["data"]) >= 7:
                        d = parsed["data"]
                        # byte 3: SW revision supplemental
                        pass

                    # Regular data pages — device type is in channel assignment,
                    # we infer from data page structure
                    elif parsed["page"] == 0x00 and len(parsed["data"]) >= 7:
                        # Many ANT+ profiles put extended device info in data
                        d = parsed["data"]
                        # Heartrate page 0: bytes 4-5 are beat time, byte 6 is count
                        # We tag this as HR if plausible
                        if len(d) >= 7:
                            device_type_code = 0x78  # tentative HR

            # Build unique key
            dev_key = f"{freq_label}:ch{ch}"
            if device_number > 0:
                dev_key = f"{device_number:04X}:{device_type_code:02X}"

            burst_power = np.mean(np.abs(cap.samples) ** 2)
            rssi_est = 10 * np.log10(max(burst_power, 1e-20)) + 30

            looked_up_type = lookup_ant_device_type(device_type_code)
            type_name = looked_up_type if looked_up_type else DEVICE_TYPES.get(device_type_code, f"0x{device_type_code:02X}")
            dev = ANTDevice(
                device_number=device_number,
                device_type=device_type_code,
                device_type_name=type_name,
                transmission_type=transmission_type,
                profile=type_name if type_name != f"0x{device_type_code:02X}" else "Unknown",
                channel=freq_label,
                rssi_dbm=rssi_est,
            )

            # Timing analysis
            ts_list = sorted(device_timestamps.get(ch, []))
            dev.timestamps = ts_list
            if len(ts_list) >= 2:
                intervals_ms = [
                    (ts_list[j + 1] - ts_list[j]) * 1000
                    for j in range(len(ts_list) - 1)
                ]
                dev.mean_interval_ms = float(np.mean(intervals_ms))
                dev.interval_jitter_ms = float(np.std(intervals_ms))

                # Clock drift — ANT+ standard interval is 246.3 ms (4.06 Hz)
                expected_s = 0.2463
                mean_s = dev.mean_interval_ms / 1000
                # Pick nearest standard ANT interval
                std_intervals = [0.2463, 0.4926, 0.5, 1.0, 2.0, 4.0]
                expected_s = min(std_intervals, key=lambda x: abs(x - mean_s))
                if expected_s > 0:
                    intervals_s = np.array(ts_list[1:]) - np.array(ts_list[:-1])
                    drift_ratios = (intervals_s - expected_s) / expected_s
                    dev.clock_drift_ppm = float(np.mean(drift_ratios) * 1e6)

            fp.devices[dev_key] = dev

    fp.capture_duration = total_duration
    log.info("ANT scan complete: %d devices", len(fp.devices))
    return fp


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SpectralDNA ANT/ANT+ fingerprint module")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Seconds per frequency (default: 10)")
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
