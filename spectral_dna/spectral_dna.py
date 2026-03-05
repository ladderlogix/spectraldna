"""
SpectralDNA — Main orchestrator.

Runs all protocol modules, aggregates results, computes the composite
fingerprint hash, and renders the output.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .capture import HackRFCapture
from . import (
    wifi_print,
    ble_print,
    lte_print,
    fiveg_print,
    tpms_print,
    rke_print,
    ant_print,
    gnss_print,
)
from .renderer import (
    render_terminal,
    export_json,
    export_html,
    compute_composite_hash,
)
from .environment import (
    save_snapshot,
    load_snapshot,
    compare,
    render_comparison,
    render_environment_summary,
)
from .subjects import (
    create_subject,
    check_presence,
    list_subjects,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol registry
# ---------------------------------------------------------------------------

PROTOCOL_MODULES = {
    "wifi":  {"module": wifi_print,   "label": "Wi-Fi 2.4/5 GHz"},
    "ble":   {"module": ble_print,    "label": "BLE Advertising"},
    "lte":   {"module": lte_print,    "label": "LTE (B2/B4/B12/B41/B71)"},
    "5g":    {"module": fiveg_print,  "label": "5G NR (n77/n78)"},
    "tpms":  {"module": tpms_print,   "label": "TPMS 315 MHz"},
    "rke":   {"module": rke_print,    "label": "RKE 315 MHz"},
    "ant":   {"module": ant_print,    "label": "ANT/ANT+ 2.4 GHz"},
    "gnss":  {"module": gnss_print,   "label": "GPS/GNSS L1/L5"},
}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class ScanConfig:
    """Configuration for a full SpectralDNA scan."""
    target: str = "UNKNOWN"
    protocols: list[str] | None = None  # None = all
    wifi_duration: float = 2.0
    ble_duration: float = 5.0
    lte_duration: float = 3.0
    fiveg_duration: float = 2.0
    tpms_duration: float = 30.0
    rke_duration: float = 60.0
    ant_duration: float = 10.0
    gnss_duration: float = 2.0
    wifi_monitor_iface: str | None = None
    output_json: str | None = None
    output_html: str | None = None
    # Environment & subject features
    snapshot_path: str | None = None      # --snapshot: save env snapshot after scan
    compare_path: str | None = None       # --compare: compare scan against snapshot
    enroll_name: str | None = None        # --enroll: create/update subject profile
    check_name: str | None = None         # --check: check subject presence


def run_scan(config: ScanConfig) -> dict[str, Any]:
    """
    Execute a full SpectralDNA fingerprint scan.

    Opens the HackRF, runs each enabled protocol module in sequence,
    aggregates results, computes the composite hash, and renders output.

    Returns the aggregated fingerprint data dict.
    """
    enabled = config.protocols or list(PROTOCOL_MODULES.keys())
    log.info("SpectralDNA scan starting — target: %s", config.target)
    log.info("Enabled protocols: %s", ", ".join(enabled))

    sdr = HackRFCapture()
    sdr.open()

    fingerprint_data: dict[str, Any] = {}
    t_start = time.time()

    try:
        # ── Wi-Fi ──────────────────────────────────────────────────
        if "wifi" in enabled:
            log.info("─── Wi-Fi scan ───")
            fp = wifi_print.scan(
                sdr,
                duration_per_channel=config.wifi_duration,
                monitor_iface=config.wifi_monitor_iface,
            )
            fingerprint_data["wifi"] = {
                "identifiers": fp.identifiers(),
                "rf_fingerprint": fp.rf_fingerprint(),
                "hash_material": fp.hash_material(),
            }

        # ── BLE ────────────────────────────────────────────────────
        if "ble" in enabled:
            log.info("─── BLE scan ───")
            fp = ble_print.scan(sdr, duration_per_channel=config.ble_duration)
            fingerprint_data["ble"] = {
                "identifiers": fp.identifiers(),
                "rf_fingerprint": fp.rf_fingerprint(),
                "hash_material": fp.hash_material(),
            }

        # ── LTE ────────────────────────────────────────────────────
        if "lte" in enabled:
            log.info("─── LTE scan ───")
            fp = lte_print.scan(sdr, duration_per_band=config.lte_duration)
            fingerprint_data["lte"] = {
                "identifiers": fp.identifiers(),
                "rf_fingerprint": fp.rf_fingerprint(),
                "hash_material": fp.hash_material(),
            }

        # ── 5G NR ──────────────────────────────────────────────────
        if "5g" in enabled:
            log.info("─── 5G NR scan ───")
            fp = fiveg_print.scan(sdr, duration_per_freq=config.fiveg_duration)
            fingerprint_data["5g"] = {
                "identifiers": fp.identifiers(),
                "rf_fingerprint": fp.rf_fingerprint(),
                "hash_material": fp.hash_material(),
            }

        # ── TPMS ───────────────────────────────────────────────────
        if "tpms" in enabled:
            log.info("─── TPMS scan ───")
            fp = tpms_print.scan(sdr, duration=config.tpms_duration)
            fingerprint_data["tpms"] = {
                "identifiers": fp.identifiers(),
                "rf_fingerprint": fp.rf_fingerprint(),
                "hash_material": fp.hash_material(),
            }

        # ── RKE ────────────────────────────────────────────────────
        if "rke" in enabled:
            log.info("─── RKE scan ───")
            fp = rke_print.scan(sdr, duration=config.rke_duration)
            fingerprint_data["rke"] = {
                "identifiers": fp.identifiers(),
                "rf_fingerprint": fp.rf_fingerprint(),
                "hash_material": fp.hash_material(),
            }

        # ── ANT/ANT+ ─────────────────────────────────────────────
        if "ant" in enabled:
            log.info("─── ANT/ANT+ scan ───")
            fp = ant_print.scan(sdr, duration_per_freq=config.ant_duration)
            fingerprint_data["ant"] = {
                "identifiers": fp.identifiers(),
                "rf_fingerprint": fp.rf_fingerprint(),
                "hash_material": fp.hash_material(),
            }

        # ── GPS/GNSS ─────────────────────────────────────────────
        if "gnss" in enabled:
            log.info("─── GPS/GNSS scan ───")
            fp = gnss_print.scan(sdr, duration=config.gnss_duration)
            fingerprint_data["gnss"] = {
                "identifiers": fp.identifiers(),
                "rf_fingerprint": fp.rf_fingerprint(),
                "hash_material": fp.hash_material(),
            }

    finally:
        sdr.close()

    elapsed = time.time() - t_start
    log.info("Scan complete in %.1f s", elapsed)

    # ── Compute composite hash ────────────────────────────────────
    composite_hash = compute_composite_hash(fingerprint_data)
    log.info("Composite SpectralDNA hash: %s", composite_hash)

    # ── Render terminal output ────────────────────────────────────
    render_terminal(fingerprint_data, config.target, composite_hash)

    # ── Environment summary ────────────────────────────────────────
    render_environment_summary(fingerprint_data)

    # ── Snapshot save ──────────────────────────────────────────────
    if config.snapshot_path:
        save_snapshot(fingerprint_data, config.target, config.snapshot_path)
        log.info("Snapshot saved to %s", config.snapshot_path)

    # ── Snapshot comparison ────────────────────────────────────────
    if config.compare_path:
        baseline = load_snapshot(config.compare_path)
        result = compare(baseline, fingerprint_data)
        render_comparison(result)

    # ── Subject enroll ─────────────────────────────────────────────
    if config.enroll_name:
        profile = create_subject(config.enroll_name, fingerprint_data)
        log.info("Subject '%s' enrolled: %d devices, %d scans",
                 profile.name, len(profile.devices), profile.scans)

    # ── Subject presence check ─────────────────────────────────────
    if config.check_name:
        result = check_presence(config.check_name, fingerprint_data)
        render_comparison(result)

    # ── Export ─────────────────────────────────────────────────────
    if config.output_json:
        export_json(fingerprint_data, config.target, composite_hash, config.output_json)
        log.info("JSON written to %s", config.output_json)

    if config.output_html:
        export_html(fingerprint_data, config.target, composite_hash, config.output_html)
        log.info("HTML written to %s", config.output_html)

    return fingerprint_data
