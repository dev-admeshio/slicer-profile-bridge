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
        """First parent's name (single-inheritance compatibility).

        For slicers that only support one parent (Orca's JSON format),
        this is the whole story. For PrusaSlicer's INI format, where
        `inherits = a; b` is legal, prefer `inherits_all` — this property
        returns the first name so single-parent call sites still work.
        """
        names = self.inherits_all
        return names[0] if names else None

    @property
    def inherits_all(self) -> list[str]:
        """All parent profile names, in declaration order.

        Orca JSON always has at most one; Prusa INI can have several
        separated by `;`. Empty list means no inheritance.
        """
        value = self.data.get("inherits")
        if not value:
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str):
            # Prusa uses `;` to separate parents; single-parent callers
            # get a one-element list back.
            parts = [p.strip() for p in value.split(";")]
            return [p for p in parts if p]
        return []

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


# ── INI bundle loader (PrusaSlicer) ────────────────────────────────────
# PrusaSlicer distributes profiles as a single multi-section INI per
# vendor (`PrusaResearch.ini`, `Creality.ini`, ...) instead of one file
# per profile. Sections are headed `[<type>:<name>]` where type is one
# of `printer`, `filament`, `print` (our canonical "process"). We parse
# with a stdlib-light hand-rolled scanner rather than `configparser`
# because Prusa uses ambiguous characters in both section names (`:`,
# `.`) and values (`;` as parent separator, `=` inside expressions).


def _iter_ini_sections(path: Path) -> list[tuple[str, dict[str, str]]]:
    """Return [(section_header, {key: value}), ...] for a Prusa-style INI.

    Section headers keep the original form with brackets, e.g.
    `"[printer:Original Prusa MK4 0.4 nozzle]"`. Keys are lowercased
    preserving underscores; values are stripped of surrounding whitespace
    but otherwise left intact (so `"0x0,250x0"` and `"70%"` survive).
    """
    sections: list[tuple[str, dict[str, str]]] = []
    current_header: str | None = None
    current_body: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            if not stripped or stripped[0] in ("#", ";"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                if current_header is not None:
                    sections.append((current_header, current_body))
                current_header = stripped
                current_body = {}
                continue
            if current_header is None:
                continue
            eq = line.find("=")
            if eq < 0:
                continue
            key = line[:eq].strip().lower()
            value = line[eq + 1 :].strip()
            if key:
                current_body[key] = value
    if current_header is not None:
        sections.append((current_header, current_body))
    return sections


def load_ini_bundle(path: str | Path, vendor: str | None = None) -> list[RawProfile]:
    """Parse one Prusa-style INI file into a list of RawProfile objects.

    Section types other than `printer` / `filament` / `print` (notably
    `vendor` and `printer_model`) are dropped — they describe the bundle,
    not individual profiles.
    """
    p = Path(path).expanduser().resolve()
    resolved_vendor = vendor or p.stem
    profiles: list[RawProfile] = []
    for header, body in _iter_ini_sections(p):
        # Header shape: `[<type>:<name>]`. Split on the first colon only
        # because names routinely contain colons in date / version
        # decorations (e.g. `MK4:0.4`).
        inner = header[1:-1]
        if ":" not in inner:
            continue
        raw_type, name = inner.split(":", 1)
        ptype = _normalise_type(raw_type)
        if ptype is None:
            continue
        name = name.strip()
        if not name:
            continue
        profiles.append(
            RawProfile(
                type=ptype,
                name=name,
                vendor=resolved_vendor,
                path=p,
                data=dict(body),
            )
        )
    return profiles


def index_ini_file(path: str | Path, vendor: str | None = None) -> RawProfileIndex:
    """Build a RawProfileIndex out of one Prusa-style multi-section INI."""
    raw_path = Path(path).expanduser().resolve()
    if not raw_path.is_file():
        raise FileNotFoundError(f"INI bundle not found: {raw_path}")
    index = RawProfileIndex(root=raw_path.parent)
    for profile in load_ini_bundle(raw_path, vendor=vendor):
        store = index._store_for(profile.type)  # noqa: SLF001
        store[profile.name] = profile
    return index
