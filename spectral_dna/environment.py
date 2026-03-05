"""
SpectralDNA environment snapshots, presence detection, and RF environment summary.

Provides:
  - Device extraction from fingerprint data
  - Snapshot save/load (JSON persistence)
  - Scan-to-snapshot comparison for presence detection
  - RF environment summary statistics
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich import box

from .lookups import is_mac_randomized

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DeviceSignature:
    """A single device extracted from a scan."""
    protocol: str           # "wifi", "ble", "lte", etc.
    primary_id: str         # MAC, sensor_id, device_number, PCI — the match key
    display_name: str       # Human-readable label
    rf_signature: dict = field(default_factory=dict)   # CFO, clock_drift, etc.
    metadata: dict = field(default_factory=dict)        # All other identifiers


@dataclass
class EnvironmentSnapshot:
    """A saved environment baseline."""
    label: str
    timestamp: str
    devices: list[DeviceSignature] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


@dataclass
class PresenceResult:
    """Result of comparing a current scan against a baseline."""
    matched: list[tuple[DeviceSignature, DeviceSignature, float]] = field(default_factory=list)
    missing: list[DeviceSignature] = field(default_factory=list)
    new_devices: list[DeviceSignature] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Device extraction from fingerprint data
# ---------------------------------------------------------------------------

def _extract_wifi_devices(identifiers: dict, rf_fp: dict) -> list[DeviceSignature]:
    """Extract WiFi devices from fingerprint identifiers and RF data."""
    devices = []
    # Find all device indices
    indices = set()
    for key in identifiers:
        if key.startswith("wifi_dev") and "_mac" in key:
            idx = key.replace("wifi_dev", "").split("_")[0]
            try:
                indices.add(int(idx))
            except ValueError:
                pass

    for i in sorted(indices):
        p = f"wifi_dev{i}"
        mac = identifiers.get(f"{p}_mac", "")
        if not mac:
            continue
        mfr = identifiers.get(f"{p}_oui_manufacturer", "")
        display = f"{mfr} ({mac})" if mfr else mac
        rf_sig = {}
        for key in rf_fp:
            if key.startswith(p):
                rf_sig[key.replace(f"{p}_", "")] = rf_fp[key]
        meta = {}
        for key in identifiers:
            if key.startswith(p) and key != f"{p}_mac":
                meta[key.replace(f"{p}_", "")] = identifiers[key]
        devices.append(DeviceSignature(
            protocol="wifi", primary_id=mac, display_name=display,
            rf_signature=rf_sig, metadata=meta,
        ))
    return devices


def _extract_ble_devices(identifiers: dict, rf_fp: dict) -> list[DeviceSignature]:
    """Extract BLE devices from fingerprint data."""
    devices = []
    indices = set()
    for key in identifiers:
        if key.startswith("ble_dev") and "_mac" in key:
            idx = key.replace("ble_dev", "").split("_")[0]
            try:
                indices.add(int(idx))
            except ValueError:
                pass

    for i in sorted(indices):
        p = f"ble_dev{i}"
        mac = identifiers.get(f"{p}_mac", "")
        if not mac:
            continue
        name = identifiers.get(f"{p}_name", "")
        mfr = identifiers.get(f"{p}_manufacturer", "")
        display = name or mfr or mac
        rf_sig = {}
        for key in rf_fp:
            if key.startswith(p):
                rf_sig[key.replace(f"{p}_", "")] = rf_fp[key]
        meta = {}
        for key in identifiers:
            if key.startswith(p) and key != f"{p}_mac":
                meta[key.replace(f"{p}_", "")] = identifiers[key]
        devices.append(DeviceSignature(
            protocol="ble", primary_id=mac, display_name=display,
            rf_signature=rf_sig, metadata=meta,
        ))
    return devices


def _extract_lte_cells(identifiers: dict, rf_fp: dict) -> list[DeviceSignature]:
    """Extract LTE cells from fingerprint data."""
    devices = []
    indices = set()
    for key in identifiers:
        if key.startswith("lte_cell") and "_pci" in key:
            idx = key.replace("lte_cell", "").split("_")[0]
            try:
                indices.add(int(idx))
            except ValueError:
                pass

    for i in sorted(indices):
        p = f"lte_cell{i}"
        pci = identifiers.get(f"{p}_pci", "")
        earfcn = identifiers.get(f"{p}_earfcn", "")
        carrier = identifiers.get(f"{p}_carrier", "")
        primary_id = f"PCI{pci}:EARFCN{earfcn}"
        display = f"{carrier} PCI={pci}" if carrier else f"PCI={pci} EARFCN={earfcn}"
        rf_sig = {}
        for key in rf_fp:
            if key.startswith(p):
                rf_sig[key.replace(f"{p}_", "")] = rf_fp[key]
        meta = {}
        for key in identifiers:
            if key.startswith(p):
                meta[key.replace(f"{p}_", "")] = identifiers[key]
        devices.append(DeviceSignature(
            protocol="lte", primary_id=primary_id, display_name=display,
            rf_signature=rf_sig, metadata=meta,
        ))
    return devices


def _extract_5g_cells(identifiers: dict, rf_fp: dict) -> list[DeviceSignature]:
    """Extract 5G NR cells from fingerprint data."""
    devices = []
    indices = set()
    for key in identifiers:
        if key.startswith("5g_cell") and "_nr_cell_id" in key:
            idx = key.replace("5g_cell", "").split("_")[0]
            try:
                indices.add(int(idx))
            except ValueError:
                pass

    for i in sorted(indices):
        p = f"5g_cell{i}"
        cell_id = identifiers.get(f"{p}_nr_cell_id", "")
        freq = identifiers.get(f"{p}_freq_mhz", "")
        carrier = identifiers.get(f"{p}_carrier", "")
        primary_id = f"NRCID{cell_id}:F{freq}"
        display = f"{carrier} NRCID={cell_id}" if carrier else f"NRCID={cell_id} {freq}MHz"
        rf_sig = {}
        for key in rf_fp:
            if key.startswith(p):
                rf_sig[key.replace(f"{p}_", "")] = rf_fp[key]
        meta = {}
        for key in identifiers:
            if key.startswith(p):
                meta[key.replace(f"{p}_", "")] = identifiers[key]
        devices.append(DeviceSignature(
            protocol="5g", primary_id=primary_id, display_name=display,
            rf_signature=rf_sig, metadata=meta,
        ))
    return devices


def _extract_generic_devices(
    protocol: str, identifiers: dict, rf_fp: dict,
    id_suffix: str, prefix_pattern: str,
) -> list[DeviceSignature]:
    """Generic extractor for TPMS, RKE, ANT, GNSS."""
    devices = []
    indices = set()
    for key in identifiers:
        if key.startswith(prefix_pattern) and id_suffix in key:
            idx = key.replace(prefix_pattern, "").split("_")[0]
            try:
                indices.add(int(idx))
            except ValueError:
                pass

    for i in sorted(indices):
        p = f"{prefix_pattern}{i}"
        primary_id = identifiers.get(f"{p}_{id_suffix}", f"unknown_{i}")
        display = identifiers.get(f"{p}_profile", "") or primary_id
        rf_sig = {}
        for key in rf_fp:
            if key.startswith(p):
                rf_sig[key.replace(f"{p}_", "")] = rf_fp[key]
        meta = {}
        for key in identifiers:
            if key.startswith(p):
                meta[key.replace(f"{p}_", "")] = identifiers[key]
        devices.append(DeviceSignature(
            protocol=protocol, primary_id=str(primary_id), display_name=str(display),
            rf_signature=rf_sig, metadata=meta,
        ))
    return devices


def extract_devices(fingerprint_data: dict) -> list[DeviceSignature]:
    """
    Walk all protocol results and extract every unique device into DeviceSignature.
    """
    all_devices: list[DeviceSignature] = []

    for proto_key, proto_data in fingerprint_data.items():
        idents = proto_data.get("identifiers", {})
        rf_fp = proto_data.get("rf_fingerprint", {})

        if proto_key == "wifi":
            all_devices.extend(_extract_wifi_devices(idents, rf_fp))
        elif proto_key == "ble":
            all_devices.extend(_extract_ble_devices(idents, rf_fp))
        elif proto_key == "lte":
            all_devices.extend(_extract_lte_cells(idents, rf_fp))
        elif proto_key == "5g":
            all_devices.extend(_extract_5g_cells(idents, rf_fp))
        elif proto_key == "tpms":
            all_devices.extend(_extract_generic_devices(
                "tpms", idents, rf_fp, "sensor_id", "tpms_sensor",
            ))
        elif proto_key == "rke":
            all_devices.extend(_extract_generic_devices(
                "rke", idents, rf_fp, "fixed_code", "rke_signal",
            ))
        elif proto_key == "ant":
            all_devices.extend(_extract_generic_devices(
                "ant", idents, rf_fp, "device_number", "ant_dev",
            ))
        elif proto_key == "gnss":
            # GNSS is passive — no individual device IDs, extract as single entry
            if idents:
                all_devices.append(DeviceSignature(
                    protocol="gnss",
                    primary_id="gnss_environment",
                    display_name="GNSS Environment",
                    rf_signature={k: v for k, v in rf_fp.items()},
                    metadata={k: v for k, v in idents.items()},
                ))

    return all_devices


# ---------------------------------------------------------------------------
# Environment summary statistics
# ---------------------------------------------------------------------------

def compute_environment_summary(
    fingerprint_data: dict, devices: list[DeviceSignature],
) -> dict:
    """
    Compute RF environment summary statistics.

    Returns dict with:
      - device counts per protocol
      - 2.4 GHz congestion score
      - MAC randomization ratio
      - carrier diversity
      - total device count
    """
    summary: dict[str, Any] = {}

    # Count devices per protocol
    proto_counts: dict[str, int] = {}
    for dev in devices:
        proto_counts[dev.protocol] = proto_counts.get(dev.protocol, 0) + 1

    summary["wifi_devices"] = proto_counts.get("wifi", 0)
    summary["ble_devices"] = proto_counts.get("ble", 0)
    summary["lte_cells"] = proto_counts.get("lte", 0)
    summary["5g_cells"] = proto_counts.get("5g", 0)
    summary["tpms_sensors"] = proto_counts.get("tpms", 0)
    summary["rke_signals"] = proto_counts.get("rke", 0)
    summary["ant_devices"] = proto_counts.get("ant", 0)
    summary["total_devices"] = len(devices)

    # 2.4 GHz congestion — WiFi + BLE + ANT all share 2.4 GHz ISM
    ism_devices = (
        proto_counts.get("wifi", 0)
        + proto_counts.get("ble", 0)
        + proto_counts.get("ant", 0)
    )
    # Normalize: 0-20 devices = 0.0-1.0 congestion
    summary["band_congestion_2_4ghz"] = round(min(ism_devices / 20.0, 1.0), 2)

    # MAC randomization ratio (WiFi + BLE)
    randomized_count = 0
    mac_count = 0
    for dev in devices:
        if dev.protocol in ("wifi", "ble"):
            mac_count += 1
            if is_mac_randomized(dev.primary_id):
                randomized_count += 1
    summary["mac_randomization_ratio"] = (
        round(randomized_count / mac_count, 2) if mac_count > 0 else 0.0
    )

    # Carrier diversity
    carriers = set()
    for dev in devices:
        if dev.protocol in ("lte", "5g"):
            carrier = dev.metadata.get("carrier", "")
            if carrier:
                carriers.add(carrier)
    summary["active_carriers"] = sorted(carriers)

    return summary


# ---------------------------------------------------------------------------
# Snapshot save / load
# ---------------------------------------------------------------------------

def save_snapshot(
    fingerprint_data: dict, label: str, path: str,
) -> EnvironmentSnapshot:
    """
    Extract devices from fingerprint data, compute summary, and save as JSON.
    """
    devices = extract_devices(fingerprint_data)
    summary = compute_environment_summary(fingerprint_data, devices)
    timestamp = datetime.now(timezone.utc).isoformat()

    snapshot = EnvironmentSnapshot(
        label=label,
        timestamp=timestamp,
        devices=devices,
        summary=summary,
    )

    export = {
        "spectral_dna_snapshot": {
            "version": "1.0.0",
            "label": label,
            "timestamp": timestamp,
            "device_count": len(devices),
            "summary": summary,
            "devices": [asdict(d) for d in devices],
        }
    }

    with open(path, "w") as f:
        json.dump(export, f, indent=2, default=str)

    log.info("Snapshot saved: %d devices -> %s", len(devices), path)
    return snapshot


def load_snapshot(path: str) -> EnvironmentSnapshot:
    """Load an environment snapshot from JSON."""
    with open(path) as f:
        data = json.load(f)

    snap_data = data.get("spectral_dna_snapshot", data)

    devices = []
    for d in snap_data.get("devices", []):
        devices.append(DeviceSignature(
            protocol=d.get("protocol", ""),
            primary_id=d.get("primary_id", ""),
            display_name=d.get("display_name", ""),
            rf_signature=d.get("rf_signature", {}),
            metadata=d.get("metadata", {}),
        ))

    return EnvironmentSnapshot(
        label=snap_data.get("label", ""),
        timestamp=snap_data.get("timestamp", ""),
        devices=devices,
        summary=snap_data.get("summary", {}),
    )


# ---------------------------------------------------------------------------
# Comparison engine
# ---------------------------------------------------------------------------

def _rf_similarity(baseline_rf: dict, current_rf: dict) -> float:
    """
    Compute similarity score (0.0 to 1.0) between two RF signatures.
    Based on how close CFO, clock drift, RSSI, and timing values are.
    """
    if not baseline_rf or not current_rf:
        return 0.5  # no RF data — neutral confidence

    scores = []
    for key in baseline_rf:
        if key not in current_rf:
            continue
        try:
            b_val = float(baseline_rf[key])
            c_val = float(current_rf[key])
        except (ValueError, TypeError):
            continue

        # Score based on relative closeness
        if abs(b_val) < 1e-10 and abs(c_val) < 1e-10:
            scores.append(1.0)
            continue

        max_abs = max(abs(b_val), abs(c_val), 1.0)
        delta = abs(b_val - c_val) / max_abs
        score = max(0.0, 1.0 - delta)
        scores.append(score)

    return sum(scores) / len(scores) if scores else 0.5


def compare(
    baseline: EnvironmentSnapshot, current_data: dict,
) -> PresenceResult:
    """
    Compare current scan data against an environment baseline.

    1. Extract devices from current scan
    2. Match by primary_id (exact match)
    3. For matches, compute RF similarity score
    4. Classify: matched, missing, new
    """
    current_devices = extract_devices(current_data)
    result = PresenceResult()

    # Index baseline by (protocol, primary_id)
    baseline_index: dict[tuple[str, str], DeviceSignature] = {}
    for dev in baseline.devices:
        baseline_index[(dev.protocol, dev.primary_id)] = dev

    # Index current by (protocol, primary_id)
    current_index: dict[tuple[str, str], DeviceSignature] = {}
    for dev in current_devices:
        current_index[(dev.protocol, dev.primary_id)] = dev

    # Find matches and missing
    for key, b_dev in baseline_index.items():
        if key in current_index:
            c_dev = current_index[key]
            similarity = _rf_similarity(b_dev.rf_signature, c_dev.rf_signature)
            result.matched.append((b_dev, c_dev, similarity))
        else:
            result.missing.append(b_dev)

    # Find new devices
    for key, c_dev in current_index.items():
        if key not in baseline_index:
            result.new_devices.append(c_dev)

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Colors for comparison display
COLOR_PRESENT = "#7EE787"   # green
COLOR_ABSENT = "#F85149"    # red
COLOR_NEW = "#F0E68C"       # yellow
COLOR_HEADER_BG = "#161B22"
COLOR_BORDER = "#30363D"
COLOR_VALUE = "#C9D1D9"
COLOR_HASH = "#7EE787"


def render_comparison(result: PresenceResult, console: Console | None = None):
    """
    Render comparison results as a Rich table.

    Green rows = PRESENT, Red = ABSENT, Yellow = NEW.
    """
    if console is None:
        console = Console()

    table = Table(
        title="Environment Comparison",
        box=box.ROUNDED,
        border_style=COLOR_BORDER,
        expand=True,
        show_lines=True,
    )
    table.add_column("Protocol", style="bold", width=8)
    table.add_column("Device", width=30)
    table.add_column("Status", width=10, justify="center")
    table.add_column("Confidence", width=12, justify="center")
    table.add_column("RF Delta", width=20)

    # Matched (PRESENT)
    for b_dev, c_dev, confidence in result.matched:
        conf_pct = f"{confidence * 100:.0f}%"
        # Compute RF delta summary
        deltas = []
        for key in b_dev.rf_signature:
            if key in c_dev.rf_signature:
                try:
                    b_v = float(b_dev.rf_signature[key])
                    c_v = float(c_dev.rf_signature[key])
                    d = c_v - b_v
                    deltas.append(f"{key}: {d:+.2f}")
                except (ValueError, TypeError):
                    pass
        delta_str = "; ".join(deltas[:3]) if deltas else "-"

        table.add_row(
            Text(b_dev.protocol.upper(), style=COLOR_PRESENT),
            Text(b_dev.display_name, style=COLOR_PRESENT),
            Text("PRESENT", style=f"bold {COLOR_PRESENT}"),
            Text(conf_pct, style=COLOR_PRESENT),
            Text(delta_str, style="dim"),
        )

    # Missing (ABSENT)
    for dev in result.missing:
        table.add_row(
            Text(dev.protocol.upper(), style=COLOR_ABSENT),
            Text(dev.display_name, style=COLOR_ABSENT),
            Text("ABSENT", style=f"bold {COLOR_ABSENT}"),
            Text("-", style="dim"),
            Text("-", style="dim"),
        )

    # New devices
    for dev in result.new_devices:
        table.add_row(
            Text(dev.protocol.upper(), style=COLOR_NEW),
            Text(dev.display_name, style=COLOR_NEW),
            Text("NEW", style=f"bold {COLOR_NEW}"),
            Text("-", style="dim"),
            Text("-", style="dim"),
        )

    console.print()
    console.print(table)

    # Summary footer
    total_baseline = len(result.matched) + len(result.missing)
    matched_count = len(result.matched)
    if total_baseline > 0:
        presence_pct = matched_count / total_baseline * 100
    else:
        presence_pct = 0.0

    console.print(
        Text(
            f"\n  {matched_count} of {total_baseline} baseline devices matched "
            f"({presence_pct:.0f}% presence), {len(result.new_devices)} new devices\n",
            style=f"bold {COLOR_VALUE}",
        )
    )


def render_environment_summary(
    fingerprint_data: dict, console: Console | None = None,
):
    """
    Render RF environment summary statistics after protocol sections.
    """
    if console is None:
        console = Console()

    devices = extract_devices(fingerprint_data)
    summary = compute_environment_summary(fingerprint_data, devices)

    table = Table(
        show_header=False, box=box.SIMPLE, padding=(0, 2),
        expand=True, border_style="#30363D",
    )
    table.add_column("Key", style="#8B949E", width=28)
    table.add_column("Value", style="#C9D1D9")

    table.add_row(
        Text("TOTAL UNIQUE DEVICES", style="#8B949E"),
        Text(str(summary["total_devices"]), style="bold #58A6FF"),
    )
    table.add_row(
        Text("WI-FI DEVICES", style="#8B949E"),
        Text(str(summary["wifi_devices"]), style="#C9D1D9"),
    )
    table.add_row(
        Text("BLE DEVICES", style="#8B949E"),
        Text(str(summary["ble_devices"]), style="#C9D1D9"),
    )
    table.add_row(
        Text("LTE CELLS", style="#8B949E"),
        Text(str(summary["lte_cells"]), style="#C9D1D9"),
    )
    table.add_row(
        Text("5G CELLS", style="#8B949E"),
        Text(str(summary["5g_cells"]), style="#C9D1D9"),
    )
    table.add_row(
        Text("ANT+ DEVICES", style="#8B949E"),
        Text(str(summary["ant_devices"]), style="#C9D1D9"),
    )

    congestion = summary.get("band_congestion_2_4ghz", 0)
    cong_color = "#7EE787" if congestion < 0.4 else "#FFA657" if congestion < 0.7 else "#F85149"
    table.add_row(
        Text("2.4 GHZ CONGESTION", style="#8B949E"),
        Text(f"{congestion:.0%}", style=f"bold {cong_color}"),
    )

    rand_ratio = summary.get("mac_randomization_ratio", 0)
    table.add_row(
        Text("MAC RANDOMIZATION RATIO", style="#8B949E"),
        Text(f"{rand_ratio:.0%}", style="bold #FFA657"),
    )

    carriers = summary.get("active_carriers", [])
    if carriers:
        table.add_row(
            Text("ACTIVE CARRIERS", style="#8B949E"),
            Text(", ".join(carriers), style="bold #58A6FF"),
        )

    label = Text(" ENVIRONMENT SUMMARY ", style="bold white on #264653")
    console.print(Panel(
        table,
        title=label,
        title_align="left",
        border_style="#264653",
        padding=(0, 1),
    ))
