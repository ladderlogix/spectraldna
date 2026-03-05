"""
SpectralDNA fingerprint renderer.

Produces:
  - Rich terminal output styled as a dark-themed console readout
  - JSON export
  - Styled HTML export

Color palette:
  Background  #0D1117
  Wi-Fi       #5500FF
  BLE         #E63946
  LTE         #2A9D8F
  5G          #264653
  TPMS        #7209B7
  RKE         #4361EE
  Keys        gray (dim)
  Values      light gray
  Identifiers blue (#58A6FF)
  PHY values  orange (#FFA657)
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich import box

# ---------------------------------------------------------------------------
# Protocol metadata
# ---------------------------------------------------------------------------

PROTOCOL_META = {
    "wifi":  {"label": "Wi-Fi",  "color": "#5500FF", "icon": ""},
    "ble":   {"label": "BLE",    "color": "#E63946", "icon": ""},
    "lte":   {"label": "LTE",    "color": "#2A9D8F", "icon": ""},
    "5g":    {"label": "5G NR",  "color": "#264653", "icon": ""},
    "tpms":  {"label": "TPMS",   "color": "#7209B7", "icon": ""},
    "rke":   {"label": "RKE",    "color": "#4361EE", "icon": ""},
    "ant":   {"label": "ANT+",   "color": "#F77F00", "icon": ""},
    "gnss":  {"label": "GNSS",   "color": "#06D6A0", "icon": ""},
}

# Value styling
COLOR_KEY = "#8B949E"         # gray for keys
COLOR_VALUE = "#C9D1D9"       # light gray for regular values
COLOR_IDENT = "#58A6FF"       # blue for identifiers
COLOR_PHY = "#FFA657"         # orange for physical-layer values
COLOR_HEADER_BG = "#161B22"
COLOR_BG = "#0D1117"
COLOR_BORDER = "#30363D"
COLOR_HASH = "#7EE787"        # green for the final hash

# Physical-layer fingerprint keywords
PHY_KEYWORDS = {
    "cfo", "iq_gain", "iq_phase", "clock_drift", "imd3", "pa_",
    "pulse_width", "psd_", "rise_time", "overshoot", "fall_time",
    "settling_time", "timing_drift", "jitter", "interval",
    "doppler", "interference", "band_power", "congestion",
}


def _is_phy_key(key: str) -> bool:
    """Check if a key represents a physical-layer fingerprint value."""
    key_lower = key.lower()
    return any(kw in key_lower for kw in PHY_KEYWORDS)


def _is_identifier_key(key: str) -> bool:
    """Check if a key represents a protocol-level identifier."""
    ident_keywords = {
        "mac", "ssid", "imei", "sensor_id", "cell_id", "nr_cell",
        "pci", "earfcn", "rolling_code", "fixed_code", "manufacturer",
        "preamble_code", "name", "uuid",
        "device_number", "prn", "satellite", "profile",
        "carrier", "oui", "randomized", "service_names",
    }
    key_lower = key.lower()
    return any(kw in key_lower for kw in ident_keywords)


# ---------------------------------------------------------------------------
# Terminal renderer (Rich)
# ---------------------------------------------------------------------------

def render_terminal(
    fingerprint_data: dict[str, Any],
    target: str = "UNKNOWN",
    composite_hash: str = "",
    console: Console | None = None,
):
    """
    Render the SpectralDNA fingerprint to the terminal using Rich.

    Parameters
    ----------
    fingerprint_data : dict
        Keys are protocol names, values are dicts with 'identifiers' and
        'rf_fingerprint' sub-dicts.
    target : str
        Target label from --target flag.
    composite_hash : str
        SHA-256 composite hash of all collected values.
    """
    if console is None:
        console = Console()

    # ── Top bar ───────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_protocols = len([p for p in fingerprint_data if fingerprint_data[p]])
    total_identifiers = sum(
        len(fingerprint_data[p].get("identifiers", {}))
        for p in fingerprint_data
    )

    header = Table(
        show_header=False, box=None, padding=(0, 2),
        style=f"on {COLOR_HEADER_BG}",
        expand=True,
    )
    header.add_column(ratio=1)
    header.add_column(ratio=1, justify="center")
    header.add_column(ratio=1, justify="right")

    header.add_row(
        Text(" SPECTRAL DNA", style=f"bold {COLOR_HASH}"),
        Text(f"TARGET: {target}", style=f"bold {COLOR_IDENT}"),
        Text(now, style=f"dim {COLOR_KEY}"),
    )

    console.print()
    console.print(Panel(
        header,
        border_style=COLOR_BORDER,
        padding=(0, 0),
    ))

    # ── Protocol sections ─────────────────────────────────────────────
    for proto_key, meta in PROTOCOL_META.items():
        proto_data = fingerprint_data.get(proto_key, {})
        identifiers = proto_data.get("identifiers", {})
        rf_fp = proto_data.get("rf_fingerprint", {})

        if not identifiers and not rf_fp:
            continue

        table = Table(
            show_header=False,
            box=box.SIMPLE,
            padding=(0, 1),
            expand=True,
            border_style=meta["color"],
        )
        table.add_column("Key", style=COLOR_KEY, width=32, no_wrap=True)
        table.add_column("Value", style=COLOR_VALUE)

        # Identifiers
        for key, value in identifiers.items():
            display_key = key.replace(f"{proto_key}_", "").replace("_", " ").upper()
            if _is_identifier_key(key):
                val_style = f"bold {COLOR_IDENT}"
            else:
                val_style = COLOR_VALUE
            table.add_row(
                Text(display_key, style=COLOR_KEY),
                Text(str(value), style=val_style),
            )

        # RF fingerprint values
        for key, value in rf_fp.items():
            display_key = key.replace(f"{proto_key}_", "").replace("_", " ").upper()
            if _is_phy_key(key):
                val_style = f"bold {COLOR_PHY}"
            else:
                val_style = COLOR_VALUE
            table.add_row(
                Text(display_key, style=COLOR_KEY),
                Text(str(value), style=val_style),
            )

        label = Text(f" {meta['label']} ", style=f"bold white on {meta['color']}")
        console.print(Panel(
            table,
            title=label,
            title_align="left",
            border_style=meta["color"],
            padding=(0, 1),
        ))

    # ── Footer ────────────────────────────────────────────────────────
    footer = Table(
        show_header=False, box=None, padding=(0, 2),
        style=f"on {COLOR_HEADER_BG}",
        expand=True,
    )
    footer.add_column(ratio=1)
    footer.add_column(ratio=2, justify="center")
    footer.add_column(ratio=1, justify="right")

    footer.add_row(
        Text(f"PROTOCOLS: {total_protocols}", style=f"bold {COLOR_VALUE}"),
        Text(f"SHA-256: {composite_hash[:32]}...", style=f"bold {COLOR_HASH}"),
        Text(f"IDENTIFIERS: {total_identifiers}", style=f"bold {COLOR_VALUE}"),
    )

    console.print(Panel(
        footer,
        border_style=COLOR_BORDER,
        padding=(0, 0),
    ))
    console.print()

    # Full hash
    console.print(
        Text(f"  COMPOSITE SPECTRALDNA HASH:\n  {composite_hash}\n",
             style=f"bold {COLOR_HASH}"),
    )


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def export_json(
    fingerprint_data: dict[str, Any],
    target: str,
    composite_hash: str,
    output_path: str,
):
    """Export fingerprint data as JSON."""
    export = {
        "spectral_dna": {
            "version": "1.0.0",
            "target": target,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "composite_hash": composite_hash,
            "protocols": fingerprint_data,
        }
    }
    with open(output_path, "w") as f:
        json.dump(export, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SpectralDNA — {target}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: {bg};
    color: {value_color};
    font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
    font-size: 13px;
    line-height: 1.6;
    padding: 24px;
  }}
  .header {{
    background: {header_bg};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 12px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }}
  .header .logo {{ color: {hash_color}; font-weight: bold; font-size: 16px; }}
  .header .target {{ color: {ident_color}; font-weight: bold; }}
  .header .time {{ color: {key_color}; font-size: 11px; }}
  .protocol {{
    border: 1px solid {border};
    border-radius: 6px;
    margin-bottom: 12px;
    overflow: hidden;
  }}
  .protocol-header {{
    padding: 6px 16px;
    font-weight: bold;
    font-size: 13px;
    color: white;
    letter-spacing: 1px;
  }}
  .protocol-body {{
    padding: 8px 16px;
    display: grid;
    grid-template-columns: 260px 1fr;
    gap: 2px 16px;
  }}
  .key {{ color: {key_color}; text-transform: uppercase; font-size: 11px; padding: 2px 0; }}
  .val {{ color: {value_color}; padding: 2px 0; }}
  .val.ident {{ color: {ident_color}; font-weight: bold; }}
  .val.phy {{ color: {phy_color}; font-weight: bold; }}
  .footer {{
    background: {header_bg};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 12px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 16px;
  }}
  .footer .stat {{ color: {value_color}; font-weight: bold; }}
  .footer .hash {{ color: {hash_color}; font-weight: bold; font-size: 12px; }}
  .composite {{
    color: {hash_color};
    font-weight: bold;
    padding: 12px 0;
    font-size: 12px;
    word-break: break-all;
  }}
  .rail {{
    width: 4px;
    display: inline-block;
    border-radius: 2px;
    margin-right: 8px;
  }}
</style>
</head>
<body>

<div class="header">
  <span class="logo">SPECTRAL DNA</span>
  <span class="target">TARGET: {target}</span>
  <span class="time">{timestamp}</span>
</div>

{protocol_sections}

<div class="footer">
  <span class="stat">PROTOCOLS: {total_protocols}</span>
  <span class="hash">SHA-256: {hash_short}...</span>
  <span class="stat">IDENTIFIERS: {total_identifiers}</span>
</div>

<div class="composite">
  COMPOSITE SPECTRALDNA HASH:<br>
  {composite_hash}
</div>

</body>
</html>
"""

PROTOCOL_SECTION_TEMPLATE = """\
<div class="protocol">
  <div class="protocol-header" style="background:{color};">
    {label}
  </div>
  <div class="protocol-body">
    {rows}
  </div>
</div>
"""


def export_html(
    fingerprint_data: dict[str, Any],
    target: str,
    composite_hash: str,
    output_path: str,
):
    """Export fingerprint data as a styled HTML file."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    protocol_sections = []
    total_protocols = 0
    total_identifiers = 0

    for proto_key, meta in PROTOCOL_META.items():
        proto_data = fingerprint_data.get(proto_key, {})
        identifiers = proto_data.get("identifiers", {})
        rf_fp = proto_data.get("rf_fingerprint", {})

        if not identifiers and not rf_fp:
            continue

        total_protocols += 1
        total_identifiers += len(identifiers)

        rows = []
        for key, value in identifiers.items():
            display_key = key.replace(f"{proto_key}_", "").replace("_", " ")
            cls = "ident" if _is_identifier_key(key) else ""
            rows.append(
                f'    <div class="key">{display_key}</div>'
                f'<div class="val {cls}">{value}</div>'
            )
        for key, value in rf_fp.items():
            display_key = key.replace(f"{proto_key}_", "").replace("_", " ")
            cls = "phy" if _is_phy_key(key) else ""
            rows.append(
                f'    <div class="key">{display_key}</div>'
                f'<div class="val {cls}">{value}</div>'
            )

        section = PROTOCOL_SECTION_TEMPLATE.format(
            color=meta["color"],
            label=meta["label"],
            rows="\n".join(rows),
        )
        protocol_sections.append(section)

    html = HTML_TEMPLATE.format(
        target=target,
        timestamp=timestamp,
        bg=COLOR_BG,
        header_bg=COLOR_HEADER_BG,
        border=COLOR_BORDER,
        key_color=COLOR_KEY,
        value_color=COLOR_VALUE,
        ident_color=COLOR_IDENT,
        phy_color=COLOR_PHY,
        hash_color=COLOR_HASH,
        protocol_sections="\n".join(protocol_sections),
        total_protocols=total_protocols,
        total_identifiers=total_identifiers,
        hash_short=composite_hash[:32],
        composite_hash=composite_hash,
    )

    with open(output_path, "w") as f:
        f.write(html)


# ---------------------------------------------------------------------------
# Composite hash computation
# ---------------------------------------------------------------------------

def compute_composite_hash(fingerprint_data: dict[str, Any]) -> str:
    """
    Compute SHA-256 hash over all collected identifier and fingerprint values.
    This is the final "SpectralDNA" string.
    """
    hasher = hashlib.sha256()

    for proto_key in sorted(fingerprint_data.keys()):
        proto = fingerprint_data[proto_key]
        for section in ("identifiers", "rf_fingerprint"):
            data = proto.get(section, {})
            for key in sorted(data.keys()):
                hasher.update(f"{key}={data[key]}".encode("utf-8"))

    return hasher.hexdigest()
