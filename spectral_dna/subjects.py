"""
SpectralDNA subject profiles — associate scans with people.

Storage: ~/.spectral_dna/subjects/{name}.json

Provides:
  - Create subject profile from scan
  - Update/merge subsequent scans
  - Check if subject's devices are present in a current scan
  - List / delete subjects
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .environment import (
    DeviceSignature,
    PresenceResult,
    extract_devices,
    compare,
    EnvironmentSnapshot,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SubjectProfile:
    name: str
    created: str = ""
    updated: str = ""
    scans: int = 0
    devices: list[DeviceSignature] = field(default_factory=list)
    stable_devices: list[str] = field(default_factory=list)  # primary_ids seen in >50% of scans
    _device_seen_counts: dict[str, int] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def get_subjects_dir() -> Path:
    """Return the subjects directory, creating it if needed."""
    d = Path.home() / ".spectral_dna" / "subjects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _subject_path(name: str) -> Path:
    """Return path to a subject's JSON file."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return get_subjects_dir() / f"{safe_name}.json"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _save_profile(profile: SubjectProfile):
    """Write subject profile to disk."""
    path = _subject_path(profile.name)
    data = {
        "spectral_dna_subject": {
            "version": "1.0.0",
            "name": profile.name,
            "created": profile.created,
            "updated": profile.updated,
            "scans": profile.scans,
            "stable_devices": profile.stable_devices,
            "device_seen_counts": profile._device_seen_counts,
            "devices": [asdict(d) for d in profile.devices],
        }
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log.info("Subject '%s' saved: %d devices, %d scans", profile.name, len(profile.devices), profile.scans)


def _load_profile(path: Path) -> SubjectProfile:
    """Read subject profile from disk."""
    with open(path) as f:
        data = json.load(f)

    s = data.get("spectral_dna_subject", data)

    devices = []
    for d in s.get("devices", []):
        devices.append(DeviceSignature(
            protocol=d.get("protocol", ""),
            primary_id=d.get("primary_id", ""),
            display_name=d.get("display_name", ""),
            rf_signature=d.get("rf_signature", {}),
            metadata=d.get("metadata", {}),
        ))

    return SubjectProfile(
        name=s.get("name", ""),
        created=s.get("created", ""),
        updated=s.get("updated", ""),
        scans=s.get("scans", 0),
        devices=devices,
        stable_devices=s.get("stable_devices", []),
        _device_seen_counts=s.get("device_seen_counts", {}),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_subject(name: str, fingerprint_data: dict) -> SubjectProfile:
    """
    Create a new subject profile from a scan.

    If the subject already exists, delegates to update_subject.
    """
    path = _subject_path(name)
    if path.exists():
        log.info("Subject '%s' already exists — merging scan", name)
        return update_subject(name, fingerprint_data)

    now = datetime.now(timezone.utc).isoformat()
    devices = extract_devices(fingerprint_data)

    seen_counts = {dev.primary_id: 1 for dev in devices}

    profile = SubjectProfile(
        name=name,
        created=now,
        updated=now,
        scans=1,
        devices=devices,
        stable_devices=[dev.primary_id for dev in devices],  # all stable on first scan
        _device_seen_counts=seen_counts,
    )

    _save_profile(profile)
    return profile


def load_subject(name: str) -> SubjectProfile:
    """Load a subject profile by name."""
    path = _subject_path(name)
    if not path.exists():
        raise FileNotFoundError(f"No subject profile found: {name}")
    return _load_profile(path)


def update_subject(name: str, fingerprint_data: dict) -> SubjectProfile:
    """
    Merge a new scan into an existing subject profile.

    Updates device list, increments scan count, and recalculates stable devices
    (primary_ids seen in >50% of scans).
    """
    path = _subject_path(name)
    if not path.exists():
        log.info("Subject '%s' does not exist — creating", name)
        return create_subject(name, fingerprint_data)

    profile = _load_profile(path)
    new_devices = extract_devices(fingerprint_data)
    now = datetime.now(timezone.utc).isoformat()

    # Build index of existing devices
    existing_index: dict[tuple[str, str], int] = {}
    for i, dev in enumerate(profile.devices):
        existing_index[(dev.protocol, dev.primary_id)] = i

    # Merge new devices
    new_ids_seen: set[str] = set()
    for new_dev in new_devices:
        key = (new_dev.protocol, new_dev.primary_id)
        new_ids_seen.add(new_dev.primary_id)

        if key in existing_index:
            # Update RF signature with latest values
            idx = existing_index[key]
            profile.devices[idx].rf_signature = new_dev.rf_signature
            profile.devices[idx].metadata = new_dev.metadata
            profile.devices[idx].display_name = new_dev.display_name
        else:
            # New device for this subject
            profile.devices.append(new_dev)
            existing_index[key] = len(profile.devices) - 1

    # Update seen counts
    for pid in new_ids_seen:
        profile._device_seen_counts[pid] = profile._device_seen_counts.get(pid, 0) + 1

    profile.scans += 1
    profile.updated = now

    # Recalculate stable devices (seen in >50% of scans)
    threshold = profile.scans / 2.0
    profile.stable_devices = [
        pid for pid, count in profile._device_seen_counts.items()
        if count > threshold
    ]

    _save_profile(profile)
    return profile


def check_presence(name: str, fingerprint_data: dict) -> PresenceResult:
    """
    Check if a subject's stable devices are present in the current scan.

    Loads the subject's profile, builds a virtual snapshot from their
    stable devices, and compares against the current scan data.
    """
    profile = load_subject(name)

    # Build snapshot from stable devices only
    stable_set = set(profile.stable_devices)
    stable_devices = [d for d in profile.devices if d.primary_id in stable_set]

    baseline = EnvironmentSnapshot(
        label=f"Subject: {name}",
        timestamp=profile.updated,
        devices=stable_devices,
    )

    return compare(baseline, fingerprint_data)


def list_subjects() -> list[str]:
    """List all enrolled subject names."""
    subjects_dir = get_subjects_dir()
    names = []
    for path in sorted(subjects_dir.glob("*.json")):
        try:
            profile = _load_profile(path)
            names.append(profile.name)
        except Exception:
            names.append(path.stem)
    return names


def delete_subject(name: str):
    """Delete a subject profile."""
    path = _subject_path(name)
    if path.exists():
        path.unlink()
        log.info("Subject '%s' deleted", name)
    else:
        raise FileNotFoundError(f"No subject profile found: {name}")
