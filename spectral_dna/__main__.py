"""
SpectralDNA CLI entry point.

Usage:
    python -m spectral_dna --target "Subject Alpha" --output fingerprint.json
    python -m spectral_dna --target "Subject Alpha" --protocols wifi ble --output fp.json --html fp.html
    python -m spectral_dna --snapshot office_baseline.json --target "Office"
    python -m spectral_dna --compare office_baseline.json --target "Office Later"
    python -m spectral_dna --enroll Alice --target "Alice"
    python -m spectral_dna --check Alice
    python -m spectral_dna --list-subjects
"""

from __future__ import annotations

import logging
import sys

import click

from .spectral_dna import ScanConfig, run_scan, PROTOCOL_MODULES
from .subjects import list_subjects as _list_subjects


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--target", "-t",
    default="UNKNOWN",
    help="Label for this capture session (e.g. person or location name).",
)
@click.option(
    "--output", "-o",
    default=None,
    help="Export fingerprint as JSON to this path.",
)
@click.option(
    "--html",
    default=None,
    help="Export fingerprint as styled HTML to this path.",
)
@click.option(
    "--protocols", "-p",
    multiple=True,
    type=click.Choice(list(PROTOCOL_MODULES.keys()), case_sensitive=False),
    help="Protocol modules to run.  Omit to run all.",
)
@click.option(
    "--wifi-duration",
    default=2.0, type=float, show_default=True,
    help="Seconds per Wi-Fi channel.",
)
@click.option(
    "--ble-duration",
    default=5.0, type=float, show_default=True,
    help="Seconds per BLE advertising channel.",
)
@click.option(
    "--lte-duration",
    default=3.0, type=float, show_default=True,
    help="Seconds per LTE band.",
)
@click.option(
    "--fiveg-duration",
    default=2.0, type=float, show_default=True,
    help="Seconds per 5G NR frequency.",
)
@click.option(
    "--tpms-duration",
    default=30.0, type=float, show_default=True,
    help="TPMS capture duration (seconds).",
)
@click.option(
    "--rke-duration",
    default=60.0, type=float, show_default=True,
    help="RKE capture duration (seconds).",
)
@click.option(
    "--ant-duration",
    default=10.0, type=float, show_default=True,
    help="Seconds per ANT/ANT+ frequency.",
)
@click.option(
    "--gnss-duration",
    default=2.0, type=float, show_default=True,
    help="Seconds per GNSS band (L1/L5).",
)
@click.option(
    "--wifi-monitor-iface",
    default=None,
    help="Monitor-mode Wi-Fi interface for scapy passive capture.",
)
@click.option(
    "--snapshot",
    default=None,
    help="Save environment snapshot to this path after scan.",
)
@click.option(
    "--compare",
    default=None,
    help="Compare scan against saved snapshot at this path.",
)
@click.option(
    "--enroll",
    default=None,
    help="Create/update subject profile from this scan (subject name).",
)
@click.option(
    "--check",
    default=None,
    help="Check if subject's devices are present in current scan.",
)
@click.option(
    "--list-subjects",
    is_flag=True, default=False,
    help="List all enrolled subjects and exit.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True, default=False,
    help="Enable verbose (DEBUG) logging.",
)
def main(
    target: str,
    output: str | None,
    html: str | None,
    protocols: tuple[str, ...],
    wifi_duration: float,
    ble_duration: float,
    lte_duration: float,
    fiveg_duration: float,
    tpms_duration: float,
    rke_duration: float,
    ant_duration: float,
    gnss_duration: float,
    wifi_monitor_iface: str | None,
    snapshot: str | None,
    compare: str | None,
    enroll: str | None,
    check: str | None,
    list_subjects: bool,
    verbose: bool,
):
    """
    SpectralDNA — RF emission fingerprinting tool for HackRF One Pro.

    Passively captures RF emissions across Wi-Fi, BLE, LTE, 5G NR, TPMS,
    RKE, ANT/ANT+, and GPS/GNSS bands to produce a composite spectral
    fingerprint.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Handle --list-subjects (no scan needed)
    if list_subjects:
        subjects = _list_subjects()
        if not subjects:
            click.echo("No subjects enrolled.")
        else:
            click.echo(f"Enrolled subjects ({len(subjects)}):")
            for name in subjects:
                click.echo(f"  - {name}")
        return

    config = ScanConfig(
        target=target,
        protocols=list(protocols) if protocols else None,
        wifi_duration=wifi_duration,
        ble_duration=ble_duration,
        lte_duration=lte_duration,
        fiveg_duration=fiveg_duration,
        tpms_duration=tpms_duration,
        rke_duration=rke_duration,
        ant_duration=ant_duration,
        gnss_duration=gnss_duration,
        wifi_monitor_iface=wifi_monitor_iface,
        output_json=output,
        output_html=html,
        snapshot_path=snapshot,
        compare_path=compare,
        enroll_name=enroll,
        check_name=check,
    )

    try:
        run_scan(config)
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\nScan interrupted.", err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
