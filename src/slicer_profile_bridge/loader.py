"""Filesystem loader for slicer profile directories.

This module is deliberately vendor-agnostic at the file-walking level —
all slicers in scope (Orca, Prusa, Bambu) ship their profiles as JSON
under `resources/profiles/<Vendor>/{machine,filament,process}/*.json`.
The translator handles field-level differences; the loader just indexes
what's on disk.

Profile files declare their type via a top-level `"type"` key. We accept
both Orca's `"machine"` and the older `"printer"` value as printer
profiles, since Prusa's older format uses the latter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ProfileType = str  # "printer" | "filament" | "process"


@dataclass
class RawProfile:
    """A parsed profile JSON plus the breadcrumbs needed to resolve
    inheritance and re-emit source metadata downstream.
    """

    type: ProfileType           # normalised: "printer" | "filament" | "process"
    name: str                   # from JSON "name" field, used as inheritance key
    vendor: str                 # directory under resources/profiles/
    path: Path                  # absolute file path
    data: dict[str, Any]        # raw parsed JSON, untouched

    @property
    def inherits(self) -> str | None:
        """Name of the parent profile this one derives from, or None."""
        value = self.data.get("inherits")
        return value if isinstance(value, str) and value else None

    @property
    def instantiation(self) -> bool:
        """Whether this profile is concrete (user-selectable) or just a
        base for others to inherit from.

        Orca uses the string `"true"` / `"false"` in JSON; missing = true
        (for backward compat with profiles that predate the field).
        """
        flag = self.data.get("instantiation")
        if flag is None:
            return True
        return str(flag).lower() in ("true", "1", "yes")


@dataclass
class RawProfileIndex:
    """All profiles found under one slicer's profile root, grouped by type.

    Keys are profile names (from the JSON `"name"` field). We key by name
    rather than filename because `inherits` references names; inheritance
    resolution needs name-based lookup.
    """

    root: Path
    printers: dict[str, RawProfile] = field(default_factory=dict)
    filaments: dict[str, RawProfile] = field(default_factory=dict)
    processes: dict[str, RawProfile] = field(default_factory=dict)

    def get(self, ptype: ProfileType, name: str) -> RawProfile | None:
        mapping = self._store_for(ptype)
        return mapping.get(name)

    def _store_for(self, ptype: ProfileType) -> dict[str, RawProfile]:
        if ptype == "printer":
            return self.printers
        if ptype == "filament":
            return self.filaments
        if ptype == "process":
            return self.processes
        raise ValueError(f"unknown profile type: {ptype!r}")


_TYPE_ALIASES = {
    "machine": "printer",
    "printer": "printer",
    "filament": "filament",
    "process": "process",
    "print": "process",  # PrusaSlicer INI bucket name
}


def _normalise_type(raw_type: str | None) -> ProfileType | None:
    if not raw_type:
        return None
    return _TYPE_ALIASES.get(str(raw_type).lower())


def load_profile_file(path: Path) -> RawProfile | None:
    """Parse one JSON profile file. Returns None on malformed input —
    callers upstream are bulk-processing entire directories and one bad
    file shouldn't sink the whole load. Caller can warn if it cares.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ptype = _normalise_type(data.get("type"))
    name = data.get("name")
    if not ptype or not isinstance(name, str) or not name:
        return None
    vendor = _vendor_from_path(path)
    return RawProfile(type=ptype, name=name, vendor=vendor, path=path, data=data)


def _vendor_from_path(path: Path) -> str:
    """Infer the vendor name from a `.../<Vendor>/<subdir>/<file>.json`
    layout. Falls back to the immediate parent dir if the layout is flat.
    """
    parts = path.parts
    # Walk up looking for the vendor dir — parent is usually machine/filament/process
    # and the grand-parent is the vendor.
    if len(parts) >= 3 and parts[-2].lower() in ("machine", "filament", "process", "print"):
        return parts[-3]
    return path.parent.name


def index_directory(root: str | Path) -> RawProfileIndex:
    """Walk a slicer's profile root and index every `.json` found.

    Expected layout (Orca / Bambu / PrusaSlicer JSON):
        <root>/<Vendor>/machine/*.json
        <root>/<Vendor>/filament/*.json
        <root>/<Vendor>/process/*.json

    Non-JSON files and parse failures are silently skipped. We glob
    recursively so nested vendor subfolders (a few Prusa profiles do this)
    still get picked up.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(f"profile root does not exist: {root_path}")

    index = RawProfileIndex(root=root_path)
    for candidate in root_path.rglob("*.json"):
        profile = load_profile_file(candidate)
        if profile is None:
            continue
        store = index._store_for(profile.type)  # noqa: SLF001 — private but package-local
        # If the same name appears twice (user override of a system
        # profile, or a duplicate ship), the later one wins. Callers who
        # need to distinguish can inspect RawProfile.path.
        store[profile.name] = profile
    return index
