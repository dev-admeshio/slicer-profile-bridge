"""Shared derivation helpers — filament-side material flags that upstream
slicer profiles do not express directly and therefore must be inferred
from bed temperature, material category, and material name.

Why heuristics here: Orca / Bambu / PrusaSlicer filament profiles do not
ship a first-class `enclosure_required` or `drying_required` flag. The
community knowledge lives in the GUI (PrusaSlicer warns "Low bed adhesion
— consider enclosure", Bambu Studio lists drying guidelines per material)
but the underlying JSON / INI profile has no boolean. Consumers of the
canonical catalog shouldn't have to re-derive these per-downstream; we
do it once in the translator layer.

Conservative by design — false positives are less harmful than false
negatives for these flags (telling a user "PLA might need an enclosure"
is noise; missing "ABS WILL peel on an open frame" is a print failure).
"""

from __future__ import annotations

# Canonical filament categories that essentially always require a heated
# chamber or enclosure to print reliably. Keyed by the normalised
# FilamentCategory values produced by `_normalise_filament_category()` in
# each translator.
_ENCLOSURE_REQUIRED_CATEGORIES: frozenset[str] = frozenset({
    "abs", "asa", "pc", "polycarbonate",
    "pa", "nylon", "pa_cf", "pa_gf", "ppa",
    # PET-CF / PEEK / PEI — advanced engineering, always enclosure.
    "pet_cf", "peek", "pei",
})

# Bed temperature (normal printing, °C) above which we assume the material
# is warp-prone enough to need enclosure regardless of category name.
# 95°C catches ABS/ASA (bed ~100°C), PETG-HT (bed ~85-90°C stays PLA-tier),
# most PC blends. PLA variants top out at 60-65°C so won't trigger.
_ENCLOSURE_BED_TEMP_THRESHOLD_C: float = 95.0

# Substring hints in the filament NAME (not category). Vendors sometimes
# ship e.g. "Generic PLA-CF" which normalises to `pla_cf` — the CF marker
# doesn't automatically require enclosure. But "ABS Pro" or "Nylon X" on
# a profile that happens to be mis-categorised as PLA should still fire.
# Case-sensitive to avoid matching "Nabsoluteweight" or similar tokens;
# vendor names typically use ALL-CAPS for chemistry.
_ENCLOSURE_NAME_HINTS: tuple[str, ...] = (
    "ABS", "ASA", " PC ", "PC-", "PC+",
    "Nylon", "PA-", "PA6", "PA12",
    "PPS", "PEEK", "PEI", "ULTEM",
)


def infer_filament_enclosure_required(
    category: str | None,
    bed_temp_normal_c: float | None,
    name: str | None = None,
) -> bool:
    """Derive `CanonicalFilament.enclosure_required` from upstream signals.

    Three gates in OR:
      1. Canonical category membership (`abs`, `asa`, `pc`, `pa*`, etc.).
      2. Bed-temp threshold (normal printing bed >= 95°C).
      3. Name substring hint (ABS/ASA/PC/Nylon/... appearing in the
         human-readable profile name).

    Any gate trips → True. Conservative: false positives on exotic PLA
    blends with high bed temps are preferable to missing a true ABS.
    """
    if category:
        cat_norm = category.lower().strip().replace("-", "_")
        if cat_norm in _ENCLOSURE_REQUIRED_CATEGORIES:
            return True
    if bed_temp_normal_c is not None and bed_temp_normal_c >= _ENCLOSURE_BED_TEMP_THRESHOLD_C:
        return True
    if name:
        for hint in _ENCLOSURE_NAME_HINTS:
            if hint in name:
                return True
    return False
