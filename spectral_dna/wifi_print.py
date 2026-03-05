"""
Wi-Fi (802.11) fingerprint module — 2.4 / 5 GHz.

Extracts:
  - MAC addresses (BSSID, source, destination)
  - Probed SSIDs from probe-request frames
  - Supported rates and HT/VHT capability flags
  - Carrier Frequency Offset (CFO) — physical-layer fingerprint

Capture strategy:
  Channel-hop across 2.4 GHz channels 1/6/11 and 5 GHz UNII bands.
  Each dwell uses 20 MHz bandwidth at 20 Msps for full OFDM capture.
"""

from __future__ import annotations

import hashlib
import logging
import struct
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .capture import (
    CaptureResult,
    HackRFCapture,
    estimate_cfo,
    compute_power_spectral_density,
)
from .lookups import lookup_oui, is_mac_randomized

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHANNELS_24GHZ = {
    1: 2.412e9, 6: 2.437e9, 11: 2.462e9,
}
CHANNELS_5GHZ = {
    36: 5.180e9, 44: 5.220e9, 149: 5.745e9, 157: 5.785e9,
}

SAMPLE_RATE = 20e6    # 20 Msps — full 802.11 OFDM bandwidth
BANDWIDTH = 20e6
GAIN = 40.0

# 802.11 frame control types
FC_TYPE_MGMT = 0
FC_SUBTYPE_PROBE_REQ = 4
FC_SUBTYPE_PROBE_RESP = 5
FC_SUBTYPE_BEACON = 8

# Short Training Field periodicity in samples (for CFO estimation)
STF_PERIOD_SAMPLES = 16

# OFDM symbol preamble detection threshold (normalized energy)
DETECTION_THRESHOLD = 0.02


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class WiFiDevice:
    mac: str
    probed_ssids: list[str] = field(default_factory=list)
    supported_rates: list[float] = field(default_factory=list)
    ht_capable: bool = False
    vht_capable: bool = False
    he_capable: bool = False
    cfo_hz: float = 0.0
    channel: int = 0
    rssi_dbm: float = -100.0
    manufacturer: str = ""
    is_randomized: bool = False


@dataclass
class WiFiFingerprint:
    devices: dict[str, WiFiDevice] = field(default_factory=dict)
    scan_channels: list[int] = field(default_factory=list)
    capture_duration: float = 0.0

    def identifiers(self) -> dict:
        """Return all identity-bearing values."""
        out = {}
        for i, (mac, dev) in enumerate(self.devices.items()):
            prefix = f"wifi_dev{i}"
            out[f"{prefix}_mac"] = mac
            if dev.manufacturer:
                out[f"{prefix}_oui_manufacturer"] = dev.manufacturer
            out[f"{prefix}_randomized_mac"] = str(dev.is_randomized)
            if dev.probed_ssids:
                out[f"{prefix}_probed_ssids"] = ", ".join(dev.probed_ssids)
            if dev.supported_rates:
                out[f"{prefix}_rates"] = ", ".join(f"{r}" for r in dev.supported_rates)
            caps = []
            if dev.ht_capable:
                caps.append("HT")
            if dev.vht_capable:
                caps.append("VHT")
            if dev.he_capable:
                caps.append("HE")
            if caps:
                out[f"{prefix}_capabilities"] = " / ".join(caps)
            out[f"{prefix}_channel"] = str(dev.channel)
        return out

    def rf_fingerprint(self) -> dict:
        """Return physical-layer fingerprint values."""
        out = {}
        for i, (mac, dev) in enumerate(self.devices.items()):
            prefix = f"wifi_dev{i}"
            out[f"{prefix}_cfo_hz"] = f"{dev.cfo_hz:+.2f}"
            out[f"{prefix}_rssi_dbm"] = f"{dev.rssi_dbm:.1f}"
        return out

    def hash_material(self) -> str:
        parts = []
        for mac, dev in sorted(self.devices.items()):
            parts.append(mac)
            parts.extend(sorted(dev.probed_ssids))
            parts.append(f"{dev.cfo_hz:.4f}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# 802.11 frame parsing from raw IQ
# ---------------------------------------------------------------------------

def _detect_packets(samples: np.ndarray, sample_rate: float):
    """
    Energy-based packet detection using vectorized operations.
    Yields (start_index, end_index) of detected bursts.
    """
    block_size = int(sample_rate * 4e-6)  # ~4 µs blocks (one OFDM symbol)
    energy = np.abs(samples) ** 2

    n_blocks = len(energy) // block_size
    if n_blocks == 0:
        return

    # Vectorized block energy computation
    truncated = energy[: n_blocks * block_size].reshape(n_blocks, block_size)
    block_energy = truncated.mean(axis=1)

    noise_floor = np.median(block_energy)
    threshold = noise_floor * 10  # 10 dB above noise

    above = block_energy > threshold
    changes = np.diff(above.astype(np.int8))
    starts = np.where(changes == 1)[0]
    ends = np.where(changes == -1)[0]

    min_blocks = 12  # ~48 µs minimum (short OFDM frame)

    for s in starts:
        matching = ends[ends > s]
        if len(matching) == 0:
            continue
        e = matching[0]
        if (e - s) >= min_blocks:
            yield s * block_size, e * block_size


def _estimate_packet_cfo(
    samples: np.ndarray, pkt_start: int, sample_rate: float
) -> float:
    """Estimate CFO from the short training field of an 802.11 OFDM preamble."""
    # STF is 8 µs = 160 samples at 20 Msps, repeating every 16 samples
    stf_len = int(sample_rate * 8e-6)
    stf = samples[pkt_start : pkt_start + stf_len]
    if len(stf) < 32:
        return 0.0
    return estimate_cfo(stf, STF_PERIOD_SAMPLES, sample_rate)


def _parse_80211_header(payload: np.ndarray) -> dict | None:
    """
    Attempt to demodulate and parse the first bytes of an 802.11 frame.

    In practice, full OFDM demod (FFT, channel estimation, Viterbi, etc.)
    is needed.  Here we implement a simplified correlator that works on
    strong signals with minimal multipath.
    """
    # This would require full OFDM demodulation chain:
    #   1. Coarse/fine CFO correction
    #   2. Symbol timing
    #   3. FFT per OFDM symbol
    #   4. Channel estimation from LTF
    #   5. Equalization
    #   6. De-interleaving + Viterbi decode
    #
    # For the spectral fingerprinting use case, we extract CFO and power
    # characteristics from the preamble, which are the primary physical-
    # layer identifiers.  Protocol-layer fields (MAC, SSIDs) are extracted
    # when a pcap monitor-mode capture is available.
    return None


def _parse_pcap_frame(raw_bytes: bytes) -> dict | None:
    """
    Parse an 802.11 management frame from raw bytes.
    Used when frames are available via monitor-mode pcap.
    """
    if len(raw_bytes) < 24:
        return None

    fc = struct.unpack_from("<H", raw_bytes, 0)[0]
    fc_type = (fc >> 2) & 0x03
    fc_subtype = (fc >> 4) & 0x0F

    if fc_type != FC_TYPE_MGMT:
        return None

    # Address fields
    addr1 = raw_bytes[4:10].hex(":")
    addr2 = raw_bytes[10:16].hex(":")
    addr3 = raw_bytes[16:22].hex(":")

    result = {
        "type": fc_type,
        "subtype": fc_subtype,
        "dst": addr1,
        "src": addr2,
        "bssid": addr3,
    }

    # Parse tagged parameters for probe requests / beacons
    if fc_subtype in (FC_SUBTYPE_PROBE_REQ, FC_SUBTYPE_BEACON, FC_SUBTYPE_PROBE_RESP):
        body_offset = 24
        if fc_subtype in (FC_SUBTYPE_BEACON, FC_SUBTYPE_PROBE_RESP):
            body_offset = 36  # skip fixed parameters (timestamp + beacon interval + cap)

        result["ssids"] = []
        result["supported_rates"] = []
        result["ht_capable"] = False
        result["vht_capable"] = False
        result["he_capable"] = False

        pos = body_offset
        while pos + 2 <= len(raw_bytes):
            tag_id = raw_bytes[pos]
            tag_len = raw_bytes[pos + 1]
            tag_data = raw_bytes[pos + 2 : pos + 2 + tag_len]
            pos += 2 + tag_len

            if tag_id == 0 and tag_len > 0:  # SSID
                try:
                    ssid = tag_data.decode("utf-8", errors="ignore")
                    if ssid:
                        result["ssids"].append(ssid)
                except Exception:
                    pass
            elif tag_id == 1:  # Supported Rates
                for b in tag_data:
                    rate = (b & 0x7F) * 0.5
                    result["supported_rates"].append(rate)
            elif tag_id == 50:  # Extended Supported Rates
                for b in tag_data:
                    rate = (b & 0x7F) * 0.5
                    result["supported_rates"].append(rate)
            elif tag_id == 45:  # HT Capabilities
                result["ht_capable"] = True
            elif tag_id == 191:  # VHT Capabilities
                result["vht_capable"] = True
            elif tag_id == 255:  # Extension element
                if tag_len > 0 and tag_data[0] == 35:  # HE Capabilities
                    result["he_capable"] = True

    return result


# ---------------------------------------------------------------------------
# Scapy-based passive monitor capture (alternative to raw IQ decode)
# ---------------------------------------------------------------------------

def _scapy_passive_scan(interface: str, duration: float) -> list[dict]:
    """
    Capture 802.11 probe requests / beacons via scapy on a monitor-mode
    interface.  Returns list of parsed frame dicts.
    """
    try:
        from scapy.all import sniff, Dot11, Dot11ProbeReq, Dot11Elt, Dot11Beacon
    except ImportError:
        log.warning("scapy not available; skipping passive Wi-Fi scan")
        return []

    frames = []

    def _handler(pkt):
        if not pkt.haslayer(Dot11):
            return
        dot11 = pkt.getlayer(Dot11)
        info = {
            "src": dot11.addr2 or "00:00:00:00:00:00",
            "dst": dot11.addr1 or "ff:ff:ff:ff:ff:ff",
            "bssid": dot11.addr3 or "00:00:00:00:00:00",
            "ssids": [],
            "supported_rates": [],
            "ht_capable": False,
            "vht_capable": False,
            "he_capable": False,
            "subtype": dot11.subtype,
        }
        elt = pkt.getlayer(Dot11Elt)
        while elt:
            if elt.ID == 0 and elt.info:
                try:
                    ssid = elt.info.decode("utf-8", errors="ignore")
                    if ssid:
                        info["ssids"].append(ssid)
                except Exception:
                    pass
            elif elt.ID == 1:
                for b in elt.info:
                    info["supported_rates"].append((b & 0x7F) * 0.5)
            elif elt.ID == 45:
                info["ht_capable"] = True
            elif elt.ID == 191:
                info["vht_capable"] = True
            elt = elt.payload.getlayer(Dot11Elt) if elt.payload else None
        frames.append(info)

    try:
        sniff(iface=interface, prn=_handler, timeout=duration, store=False)
    except Exception as exc:
        log.warning("scapy sniff failed: %s", exc)

    return frames


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def scan(
    sdr: HackRFCapture,
    duration_per_channel: float = 2.0,
    channels: dict[int, float] | None = None,
    monitor_iface: str | None = None,
) -> WiFiFingerprint:
    """
    Run a Wi-Fi fingerprint scan.

    Parameters
    ----------
    sdr : HackRFCapture
        Opened HackRF capture engine.
    duration_per_channel : float
        Seconds to dwell on each channel.
    channels : dict
        Channel number -> center frequency mapping.  Defaults to 2.4 GHz 1/6/11.
    monitor_iface : str or None
        If provided, also run a scapy passive capture on this monitor-mode
        interface to extract protocol-layer identifiers.

    Returns
    -------
    WiFiFingerprint
    """
    if channels is None:
        channels = CHANNELS_24GHZ

    fp = WiFiFingerprint()
    fp.scan_channels = list(channels.keys())

    total_duration = 0.0

    for ch_num, freq in channels.items():
        log.info("Wi-Fi scan: channel %d (%.3f MHz)", ch_num, freq / 1e6)

        cap = sdr.capture(
            duration=duration_per_channel,
            center_freq=freq,
            sample_rate=SAMPLE_RATE,
            bandwidth=BANDWIDTH,
            gain=GAIN,
        )
        total_duration += cap.duration

        # ----- Physical-layer analysis -----
        # Collect CFO measurements from all detected packets
        cfo_measurements = []
        for pkt_start, pkt_end in _detect_packets(cap.samples, SAMPLE_RATE):
            # Require minimum packet length (~50 µs = 1000 samples at 20 Msps)
            if (pkt_end - pkt_start) < 1000:
                continue
            cfo = _estimate_packet_cfo(cap.samples, pkt_start, SAMPLE_RATE)
            pkt_power = 10 * np.log10(
                np.mean(np.abs(cap.samples[pkt_start:pkt_end]) ** 2) + 1e-20
            )
            cfo_measurements.append((cfo, pkt_power))

        log.info("Channel %d: %d valid packets detected", ch_num, len(cfo_measurements))

        if not cfo_measurements:
            continue

        # Cluster CFO values into distinct transmitters
        # Different transmitter crystals produce CFOs separated by ~1-20 kHz
        # Use 5 kHz bins to group packets from the same transmitter
        cfo_arr = np.array([m[0] for m in cfo_measurements])
        power_arr = np.array([m[1] for m in cfo_measurements])
        CFO_BIN_WIDTH = 5000.0  # Hz

        # Sort by CFO and group into clusters
        order = np.argsort(cfo_arr)
        clusters: list[list[int]] = []
        current_cluster = [order[0]]

        for i in range(1, len(order)):
            if abs(cfo_arr[order[i]] - cfo_arr[order[i - 1]]) < CFO_BIN_WIDTH:
                current_cluster.append(order[i])
            else:
                clusters.append(current_cluster)
                current_cluster = [order[i]]
        clusters.append(current_cluster)

        # Each cluster is one transmitter
        for cluster_indices in clusters:
            cluster_cfos = cfo_arr[cluster_indices]
            cluster_powers = power_arr[cluster_indices]
            mean_cfo = float(np.mean(cluster_cfos))
            max_power = float(np.max(cluster_powers))
            pkt_count = len(cluster_indices)

            # Require at least 2 packets for a credible device
            if pkt_count < 2:
                continue

            # Generate pseudo-MAC from CFO signature
            pseudo_mac = hashlib.md5(
                f"cfo:{mean_cfo:.0f}:ch:{ch_num}".encode()
            ).hexdigest()[:12]
            pseudo_mac = ":".join(
                pseudo_mac[i : i + 2] for i in range(0, 12, 2)
            )
            fp.devices[pseudo_mac] = WiFiDevice(
                mac=pseudo_mac,
                cfo_hz=mean_cfo,
                channel=ch_num,
                rssi_dbm=max_power,
                manufacturer=lookup_oui(pseudo_mac),
                is_randomized=is_mac_randomized(pseudo_mac),
            )

    # ----- Scapy passive capture for protocol fields -----
    if monitor_iface:
        scan_dur = duration_per_channel * len(channels)
        frames = _scapy_passive_scan(monitor_iface, scan_dur)
        for frm in frames:
            mac = frm["src"]
            if mac not in fp.devices:
                fp.devices[mac] = WiFiDevice(
                    mac=mac,
                    manufacturer=lookup_oui(mac),
                    is_randomized=is_mac_randomized(mac),
                )
            dev = fp.devices[mac]
            for ssid in frm.get("ssids", []):
                if ssid and ssid not in dev.probed_ssids:
                    dev.probed_ssids.append(ssid)
            for rate in frm.get("supported_rates", []):
                if rate not in dev.supported_rates:
                    dev.supported_rates.append(rate)
            dev.ht_capable = dev.ht_capable or frm.get("ht_capable", False)
            dev.vht_capable = dev.vht_capable or frm.get("vht_capable", False)
            dev.he_capable = dev.he_capable or frm.get("he_capable", False)

    fp.capture_duration = total_duration
    log.info("Wi-Fi scan complete: %d devices", len(fp.devices))
    return fp


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SpectralDNA Wi-Fi fingerprint module")
    parser.add_argument("--duration", type=float, default=2.0,
                        help="Seconds per channel (default: 2)")
    parser.add_argument("--bands", choices=["2.4", "5", "both"], default="2.4",
                        help="Frequency bands to scan")
    parser.add_argument("--monitor-iface", type=str, default=None,
                        help="Monitor-mode interface for scapy capture")
    parser.add_argument("--output", type=str, default=None,
                        help="Write JSON output to file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    channels = {}
    if args.bands in ("2.4", "both"):
        channels.update(CHANNELS_24GHZ)
    if args.bands in ("5", "both"):
        channels.update(CHANNELS_5GHZ)

    sdr = HackRFCapture()
    sdr.open()
    try:
        fp = scan(sdr, args.duration, channels, args.monitor_iface)
    finally:
        sdr.close()

    result = {"identifiers": fp.identifiers(), "rf_fingerprint": fp.rf_fingerprint()}
    print(json.dumps(result, indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
