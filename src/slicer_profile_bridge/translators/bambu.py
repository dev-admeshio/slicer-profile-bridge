"""BambuStudio profile translator.

Bambu's JSON profile format is the upstream of OrcaSlicer (Orca is a
fork), so the shape is near-identical: same `type`/`name`/`inherits`
keys, same `"nil"` sentinel for inherited values, same list-wrapping
for multi-extruder fields. The few differences Bambu still carries
(`slow_down_min_speed`, `include`) are higher-level niceties that don't
affect any canonical field, so the translator here delegates field
mapping to the Orca code path and only overrides the source label.

If Bambu and Orca ever diverge on a field the canonical schema cares
about, the fix is to add a per-slicer branch inside the Orca helpers
rather than maintain two near-duplicate translators.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from slicer_profile_bridge.inherit import ResolvedProfile, resolve
from slicer_profile_bridge.loader import index_directory
from slicer_profile_bridge.schema import (
    CanonicalFilament,
    CanonicalPrinter,
    CanonicalProcess,
    ProfileBundle,
    SourceMetadata,
)
from slicer_profile_bridge.translators import orca as _orca


def _relabel_source(source: SourceMetadata) -> SourceMetadata:
    """Swap an Orca-labelled SourceMetadata for a Bambu one without
    touching the rest of the audit trail.
    """
    return source.model_copy(update={"slicer": "bambu"})


def translate_printer(resolved: ResolvedProfile) -> CanonicalPrinter:
    printer = _orca.translate_printer(resolved)
    return printer.model_copy(update={"source": _relabel_source(printer.source)})


def translate_filament(resolved: ResolvedProfile) -> CanonicalFilament:
    filament = _orca.translate_filament(resolved)
    return filament.model_copy(update={"source": _relabel_source(filament.source)})


def translate_process(resolved: ResolvedProfile) -> CanonicalProcess:
    process = _orca.translate_process(resolved)
    return process.model_copy(update={"source": _relabel_source(process.source)})


def load_bambu(profiles_root: str | Path) -> ProfileBundle:
    """Index a BambuStudio `resources/profiles/` tree and translate every
    concrete profile. Layout matches OrcaSlicer's so the shared loader
    works without a Bambu-specific walker.
    """
    index = index_directory(profiles_root)
    bundle = ProfileBundle(slicer="bambu")

    def _maybe_add(
        raw_map: dict[str, Any],
        bundle_map: dict[str, Any],
        translate: Any,
    ) -> None:
        for raw in raw_map.values():
            if not raw.instantiation:
                continue
            resolved = resolve(raw, index)
            try:
                canonical = translate(resolved)
            except (ValueError, KeyError):
                continue
            bundle_map[canonical.id] = canonical

    _maybe_add(index.printers, bundle.printers, translate_printer)
    _maybe_add(index.filaments, bundle.filaments, translate_filament)
    _maybe_add(index.processes, bundle.processes, translate_process)
    return bundle


__all__ = [
    "load_bambu",
    "translate_filament",
    "translate_printer",
    "translate_process",
]
