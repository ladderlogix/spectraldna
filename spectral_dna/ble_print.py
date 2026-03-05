"""
BLE (Bluetooth Low Energy) fingerprint module — 2.402 / 2.426 / 2.480 GHz.

Extracts:
  - Advertising payloads (ADV_IND, ADV_NONCONN_IND, SCAN_RSP)
  - Manufacturer-specific data (e.g. 0x004C = Apple, 0x0006 = Microsoft)
  - TX power level
  - Advertising interval timing and jitter
  - Clock drift in ppm — physical-layer fingerprint

Capture:
  GFSK demodulation at 2 Msps on each advertising channel (37, 38, 39).
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass, field

import numpy as np

from .capture import (
    CaptureResult,
    HackRFCapture,
    fm_demodulate,
    clock_recover_mm,
)
from .lookups import lookup_oui, lookup_ble_company, lookup_ble_service, is_mac_randomized

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADV_CHANNELS = {
    37: 2.402e9,
    38: 2.426e9,
    39: 2.480e9,
}

SAMPLE_RATE = 4e6     # 4 Msps — 4 samples per BLE symbol for reliable decoding
BANDWIDTH = 2e6
GAIN = 40.0

# BLE physical layer
BLE_ACCESS_ADDR_ADV = 0x8E89BED6  # advertising access address
BLE_PREAMBLE = 0xAA
SYMBOL_RATE = 1e6  # 1 Msym/s
SAMPLES_PER_SYMBOL = SAMPLE_RATE / SYMBOL_RATE  # 4

# Known manufacturer IDs
MANUFACTURER_NAMES = {
    0x004C: "Apple",
    0x0006: "Microsoft",
    0x00E0: "Google",
    0x0075: "Samsung",
    0x000F: "Broadcom",
    0x0059: "Nordic Semiconductor",
    0x0131: "Tile",
    0x02FF: "Fitbit",
    0x038F: "Garmin",
}

# Apple Continuity protocol types (first byte of manufacturer data after 0x004C)
APPLE_CONTINUITY_TYPES = {
    0x02: "iBeacon",
    0x05: "AirDrop",
    0x07: "AirPods",
    0x09: "AirPlay Target",
    0x0A: "AirPlay Source",
    0x0C: "Handoff",
    0x0F: "Nearby Action",
    0x10: "Nearby Info",
    0x12: "FindMy (AirTag)",
    0x14: "FindMy",
}

# BLE AD types
AD_TYPE_FLAGS = 0x01
AD_TYPE_INCOMPLETE_16UUID = 0x02
AD_TYPE_COMPLETE_16UUID = 0x03
AD_TYPE_SHORT_NAME = 0x08
AD_TYPE_COMPLETE_NAME = 0x09
AD_TYPE_TX_POWER = 0x0A
AD_TYPE_MANUFACTURER = 0xFF


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class BLEDevice:
    mac: str
    adv_type: str = ""
    name: str = ""
    manufacturer_id: int | None = None
    manufacturer_name: str = ""
    manufacturer_data: str = ""
    apple_type: str = ""               # Apple Continuity sub-type (e.g. "FindMy (AirTag)")
    tx_power: int | None = None
    uuids: list[str] = field(default_factory=list)
    adv_intervals_ms: list[float] = field(default_factory=list)
    mean_adv_interval_ms: float = 0.0
    adv_jitter_ms: float = 0.0
    clock_drift_ppm: float = 0.0
    rssi_dbm: float = -100.0
    channel: int = 0


@dataclass
class BLEFingerprint:
    devices: dict[str, BLEDevice] = field(default_factory=dict)
    scan_channels: list[int] = field(default_factory=list)
    capture_duration: float = 0.0

    def identifiers(self) -> dict:
        out = {}
        for i, (mac, dev) in enumerate(self.devices.items()):
            p = f"ble_dev{i}"
            out[f"{p}_mac"] = mac
            oui_mfr = lookup_oui(mac)
            if oui_mfr:
                out[f"{p}_oui_manufacturer"] = oui_mfr
            out[f"{p}_randomized_mac"] = str(is_mac_randomized(mac))
            if dev.name:
                out[f"{p}_name"] = dev.name
            if dev.manufacturer_name:
                out[f"{p}_manufacturer"] = dev.manufacturer_name
            if dev.apple_type:
                out[f"{p}_apple_type"] = dev.apple_type
            if dev.manufacturer_data:
                out[f"{p}_mfr_data"] = dev.manufacturer_data
            if dev.tx_power is not None:
                out[f"{p}_tx_power_dbm"] = str(dev.tx_power)
            if dev.uuids:
                out[f"{p}_uuids"] = ", ".join(dev.uuids)
                # Resolve UUID names
                service_names = []
                for u in dev.uuids:
                    try:
                        uuid_int = int(u, 16)
                        name = lookup_ble_service(uuid_int)
                        if name:
                            service_names.append(name)
                    except ValueError:
                        pass
                if service_names:
                    out[f"{p}_service_names"] = ", ".join(service_names)
            out[f"{p}_adv_type"] = dev.adv_type
        return out

    def rf_fingerprint(self) -> dict:
        out = {}
        for i, (mac, dev) in enumerate(self.devices.items()):
            p = f"ble_dev{i}"
            out[f"{p}_mean_adv_interval_ms"] = f"{dev.mean_adv_interval_ms:.2f}"
            out[f"{p}_adv_jitter_ms"] = f"{dev.adv_jitter_ms:.3f}"
            out[f"{p}_clock_drift_ppm"] = f"{dev.clock_drift_ppm:+.2f}"
            out[f"{p}_rssi_dbm"] = f"{dev.rssi_dbm:.1f}"
        return out

    def hash_material(self) -> str:
        parts = []
        for mac, dev in sorted(self.devices.items()):
            parts.append(mac)
            if dev.manufacturer_id is not None:
                parts.append(f"mfr:{dev.manufacturer_id:04X}")
            parts.append(f"drift:{dev.clock_drift_ppm:.4f}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# GFSK demodulation + BLE packet extraction
# ---------------------------------------------------------------------------

def _dewhiten(data: bytes, channel: int) -> bytes:
    """BLE data whitening/dewhitening using LFSR with channel seed."""
    lfsr = (channel & 0x3F) | 0x40  # 7-bit LFSR, MSB always 1
    out = bytearray(len(data))
    for i, byte in enumerate(data):
        out_byte = 0
        for bit_pos in range(8):
            # XOR data bit with LFSR bit 0
            data_bit = (byte >> bit_pos) & 1
            lfsr_bit = lfsr & 1
            out_byte |= (data_bit ^ lfsr_bit) << bit_pos
            # Clock LFSR: new bit = bit[0] XOR bit[4]
            new_bit = (lfsr & 1) ^ ((lfsr >> 4) & 1)
            lfsr = (lfsr >> 1) | (new_bit << 6)
        out[i] = out_byte
    return bytes(out)


def _get_aa_bits() -> np.ndarray:
    """Return the BLE advertising access address as LSbit-first bit array."""
    aa = BLE_ACCESS_ADDR_ADV
    aa_bits_list = []
    for _ in range(32):
        aa_bits_list.append(aa & 1)
        aa >>= 1
    return np.array(aa_bits_list, dtype=np.uint8)


def _find_access_address(bits: np.ndarray) -> list[int]:
    """Find all positions of the BLE advertising access address in bit stream."""
    aa_bits = _get_aa_bits()
    positions = []
    for i in range(len(bits) - 32):
        if np.array_equal(bits[i : i + 32], aa_bits):
            positions.append(i + 32)  # point past access address
    return positions


def _find_access_address_soft(bits: np.ndarray, max_errors: int = 2) -> list[int]:
    """Find access address allowing up to max_errors bit mismatches."""
    aa_bits = _get_aa_bits()
    positions = []
    for i in range(len(bits) - 32):
        errors = int(np.sum(bits[i : i + 32] != aa_bits))
        if errors <= max_errors:
            positions.append((i + 32, errors))
    return positions


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


def _parse_adv_pdu(pdu_bytes: bytes, channel: int) -> dict | None:
    """Parse a BLE advertising channel PDU."""
    if len(pdu_bytes) < 2:
        return None

    # Dewhiten
    pdu_bytes = _dewhiten(pdu_bytes, channel)

    header = pdu_bytes[0]
    pdu_type = header & 0x0F
    tx_add = (header >> 6) & 1  # 1 = random address
    length = pdu_bytes[1] & 0x3F

    if length < 6 or length + 2 > len(pdu_bytes):
        return None

    pdu_type_names = {
        0: "ADV_IND",
        1: "ADV_DIRECT_IND",
        2: "ADV_NONCONN_IND",
        3: "SCAN_REQ",
        4: "SCAN_RSP",
        6: "ADV_SCAN_IND",
    }

    adva = pdu_bytes[2:8]
    mac = ":".join(f"{b:02X}" for b in reversed(adva))

    result = {
        "pdu_type": pdu_type,
        "pdu_type_name": pdu_type_names.get(pdu_type, f"UNKNOWN({pdu_type})"),
        "tx_add": tx_add,
        "mac": mac,
        "name": "",
        "manufacturer_id": None,
        "manufacturer_data": b"",
        "tx_power": None,
        "uuids": [],
    }

    # Parse AD structures from advertising data
    ad_data = pdu_bytes[8 : 2 + length]
    pos = 0
    while pos < len(ad_data):
        if pos + 1 >= len(ad_data):
            break
        ad_len = ad_data[pos]
        if ad_len == 0:
            break
        if pos + 1 + ad_len > len(ad_data):
            break
        ad_type = ad_data[pos + 1]
        ad_value = ad_data[pos + 2 : pos + 1 + ad_len]

        if ad_type in (AD_TYPE_SHORT_NAME, AD_TYPE_COMPLETE_NAME):
            try:
                result["name"] = ad_value.decode("utf-8", errors="ignore")
            except Exception:
                pass

        elif ad_type == AD_TYPE_TX_POWER and len(ad_value) >= 1:
            result["tx_power"] = struct.unpack("b", ad_value[:1])[0]

        elif ad_type == AD_TYPE_MANUFACTURER and len(ad_value) >= 2:
            mfr_id = struct.unpack_from("<H", ad_value, 0)[0]
            result["manufacturer_id"] = mfr_id
            result["manufacturer_data"] = ad_value[2:]

        elif ad_type in (AD_TYPE_INCOMPLETE_16UUID, AD_TYPE_COMPLETE_16UUID):
            for j in range(0, len(ad_value) - 1, 2):
                uuid16 = struct.unpack_from("<H", ad_value, j)[0]
                result["uuids"].append(f"0x{uuid16:04X}")

        pos += 1 + ad_len

    return result


def _estimate_clock_drift(adv_timestamps: list[float], expected_interval_s: float) -> float:
    """
    Estimate clock drift in ppm from advertising interval measurements.
    Compares measured intervals against expected BLE advertising interval.
    """
    if len(adv_timestamps) < 3:
        return 0.0
    intervals = np.diff(adv_timestamps)
    if expected_interval_s <= 0:
        return 0.0
    drift_ratios = (intervals - expected_interval_s) / expected_interval_s
    drift_ppm = float(np.mean(drift_ratios) * 1e6)
    return drift_ppm


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scan(
    sdr: HackRFCapture,
    duration_per_channel: float = 5.0,
    channels: dict[int, float] | None = None,
) -> BLEFingerprint:
    """
    Run a BLE advertising scan.

    Captures IQ on each advertising channel, performs GFSK demodulation,
    and extracts advertising PDUs.
    """
    if channels is None:
        channels = ADV_CHANNELS

    fp = BLEFingerprint()
    fp.scan_channels = list(channels.keys())
    total_duration = 0.0

    for ch_num, freq in channels.items():
        log.info("BLE scan: channel %d (%.3f MHz)", ch_num, freq / 1e6)

        cap = sdr.capture(
            duration=duration_per_channel,
            center_freq=freq,
            sample_rate=SAMPLE_RATE,
            bandwidth=BANDWIDTH,
            gain=GAIN,
        )
        total_duration += cap.duration

        # --- Burst detection first (fast) ---
        # BLE advertising packets are ~100-400 µs long
        envelope = np.abs(cap.samples)
        noise_floor = np.median(envelope)
        burst_thresh = noise_floor * 4

        # Find burst start/end using energy threshold
        above = envelope > burst_thresh
        changes = np.diff(above.astype(np.int8))
        starts = np.where(changes == 1)[0]
        ends = np.where(changes == -1)[0]

        # Pair starts with ends
        bursts = []
        min_burst = int(SAMPLE_RATE * 80e-6)   # BLE packet min ~80 µs
        max_burst = int(SAMPLE_RATE * 500e-6)   # max ~500 µs
        for s in starts:
            matching = ends[ends > s]
            if len(matching) == 0:
                continue
            e = matching[0]
            length = e - s
            if min_burst <= length <= max_burst:
                # Add some margin
                s_pad = max(0, s - int(SAMPLES_PER_SYMBOL * 8))
                e_pad = min(len(cap.samples), e + int(SAMPLES_PER_SYMBOL * 8))
                bursts.append((s_pad, e_pad))

        log.info("Channel %d: detected %d candidate bursts", ch_num, len(bursts))

        pkt_timestamps: dict[str, list[float]] = {}

        # --- Demodulate only detected bursts (fast) ---
        for burst_start, burst_end in bursts:
            burst = cap.samples[burst_start:burst_end]
            if len(burst) < 200:
                continue

            # FM demodulate this burst only
            freq_dev = fm_demodulate(burst)
            if len(freq_dev) < 40:
                continue

            # Remove DC offset (critical for HackRF crystal offset)
            freq_dev -= np.mean(freq_dev)

            # Normalize
            max_dev = np.max(np.abs(freq_dev))
            if max_dev <= 0:
                continue
            freq_dev_norm = freq_dev / max_dev

            sps = int(SAMPLES_PER_SYMBOL)
            aa_bits = _get_aa_bits()

            # Build a matched filter from preamble (0xAA) + access address
            # Preamble: 10101010 LSbit-first = 01010101
            preamble_bits = np.array([0, 1, 0, 1, 0, 1, 0, 1], dtype=np.float32)
            aa_float = aa_bits.astype(np.float32)
            ref_bits = np.concatenate([preamble_bits, aa_float])
            # Convert to ±1 and upsample to match sample rate
            ref_signal = np.repeat(ref_bits * 2 - 1, sps)

            # Correlate FM-demod output against known preamble+AA pattern
            if len(freq_dev_norm) <= len(ref_signal):
                continue
            corr = np.abs(np.correlate(freq_dev_norm, ref_signal, mode="valid"))
            if len(corr) == 0:
                continue

            # Find correlation peaks
            corr_thresh = np.max(corr) * 0.6
            peak_positions = np.where(corr > corr_thresh)[0]
            if len(peak_positions) == 0:
                continue

            # Cluster nearby peaks and keep best
            clusters = []
            cluster = [peak_positions[0]]
            for p in peak_positions[1:]:
                if p - cluster[-1] < sps * 20:
                    cluster.append(p)
                else:
                    clusters.append(cluster)
                    cluster = [p]
            clusters.append(cluster)

            for cluster in clusters:
                best_peak = cluster[np.argmax(corr[cluster])]

                # The peak marks the start of preamble; data starts after
                # preamble (8 bits) + access address (32 bits) = 40 bits = 40*sps samples
                data_start = best_peak + len(ref_signal)
                if data_start + sps * 16 > len(freq_dev_norm):
                    continue

                # Use the preamble+AA region to calibrate optimal sampling phase
                ref_region = freq_dev_norm[best_peak : best_peak + len(ref_signal)]
                if len(ref_region) < len(ref_signal):
                    continue

                # Try each sampling phase, score against known preamble+AA bits
                best_phase = 0
                best_score = -1
                for phase in range(sps):
                    sampled = ref_region[phase::sps]
                    trial = (sampled > 0).astype(np.uint8)
                    expected = np.concatenate([preamble_bits, aa_float]).astype(np.uint8)
                    if len(trial) >= len(expected):
                        score = int(np.sum(trial[:len(expected)] == expected))
                        if score > best_score:
                            best_score = score
                            best_phase = phase

                # Require at least 36/40 correct bits in preamble+AA for reliability
                if best_score < 36:
                    continue

                # Sample data bits at calibrated phase
                data_region = freq_dev_norm[data_start + best_phase :: sps]
                if len(data_region) < 16:
                    continue

                pdu_bits = (data_region[:312] > 0).astype(np.uint8)
                pdu_bytes = _bits_to_bytes(pdu_bits)

                parsed = _parse_adv_pdu(pdu_bytes, ch_num)
                if parsed is None:
                    continue

                mac = parsed["mac"]
                ts = burst_start / SAMPLE_RATE

                if mac not in fp.devices:
                    burst_power = np.mean(np.abs(burst) ** 2)
                    rssi_est = 10 * np.log10(max(burst_power, 1e-20)) + 30
                    fp.devices[mac] = BLEDevice(mac=mac, channel=ch_num, rssi_dbm=rssi_est)
                dev = fp.devices[mac]

                dev.adv_type = parsed["pdu_type_name"]
                if parsed["name"]:
                    dev.name = parsed["name"]
                if parsed["manufacturer_id"] is not None:
                    dev.manufacturer_id = parsed["manufacturer_id"]
                    looked_up = lookup_ble_company(parsed["manufacturer_id"])
                    dev.manufacturer_name = looked_up if looked_up else f"0x{parsed['manufacturer_id']:04X}"
                    mfr_data = parsed["manufacturer_data"]
                    dev.manufacturer_data = mfr_data.hex()
                    if parsed["manufacturer_id"] == 0x004C and len(mfr_data) >= 1:
                        apple_type_byte = mfr_data[0]
                        dev.apple_type = APPLE_CONTINUITY_TYPES.get(
                            apple_type_byte, f"Unknown(0x{apple_type_byte:02X})"
                        )
                if parsed["tx_power"] is not None:
                    dev.tx_power = parsed["tx_power"]
                for uuid in parsed["uuids"]:
                    if uuid not in dev.uuids:
                        dev.uuids.append(uuid)

                pkt_timestamps.setdefault(mac, []).append(ts)

        log.info("Channel %d: parsed %d unique MACs", ch_num, len(pkt_timestamps))

        # --- Timing analysis ---
        for mac, timestamps in pkt_timestamps.items():
            if mac not in fp.devices or len(timestamps) < 2:
                continue
            dev = fp.devices[mac]
            timestamps.sort()
            intervals_ms = [
                (timestamps[i + 1] - timestamps[i]) * 1000
                for i in range(len(timestamps) - 1)
            ]
            dev.adv_intervals_ms = intervals_ms
            dev.mean_adv_interval_ms = float(np.mean(intervals_ms))
            dev.adv_jitter_ms = float(np.std(intervals_ms))

            # Clock drift from expected interval (most BLE uses 100ms or 1000ms)
            # Try to infer expected interval by rounding mean to nearest standard
            std_intervals_s = [0.02, 0.1, 0.2, 0.5, 1.0, 2.0]
            mean_s = dev.mean_adv_interval_ms / 1000
            expected_s = min(std_intervals_s, key=lambda x: abs(x - mean_s))
            dev.clock_drift_ppm = _estimate_clock_drift(timestamps, expected_s)

    fp.capture_duration = total_duration
    log.info("BLE scan complete: %d devices", len(fp.devices))
    return fp


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SpectralDNA BLE fingerprint module")
    parser.add_argument("--duration", type=float, default=5.0,
                        help="Seconds per channel (default: 5)")
    parser.add_argument("--channels", nargs="+", type=int, default=[37, 38, 39],
                        help="Advertising channels to scan")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    channels = {ch: ADV_CHANNELS[ch] for ch in args.channels if ch in ADV_CHANNELS}

    sdr = HackRFCapture()
    sdr.open()
    try:
        fp = scan(sdr, args.duration, channels)
    finally:
        sdr.close()

    result = {"identifiers": fp.identifiers(), "rf_fingerprint": fp.rf_fingerprint()}
    print(json.dumps(result, indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
