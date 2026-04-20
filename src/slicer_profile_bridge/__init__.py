"""slicer-profile-bridge: vendor-neutral 3D print profile schema + translators.

Public API surface — everything a consumer needs lives at the package root.
Internal modules (`translators.*`, `inherit`, etc.) are not part of the
public contract and may reorganise between minor versions.
"""

from __future__ import annotations

from slicer_profile_bridge.schema import (
    CanonicalFilament,
    CanonicalPrinter,
    CanonicalProcess,
    CanonicalRecipe,
    CoolingSettings,
    FilamentCategory,
    Kinematics,
    NozzleTemps,
    PrinterTechnology,
    ProfileBundle,
    RetractionType,
    SupportSettings,
)

__version__ = "0.1.0"

__all__ = [
    "CanonicalFilament",
    "CanonicalPrinter",
    "CanonicalProcess",
    "CanonicalRecipe",
    "CoolingSettings",
    "FilamentCategory",
    "Kinematics",
    "NozzleTemps",
    "PrinterTechnology",
    "ProfileBundle",
    "RetractionType",
    "SupportSettings",
    "__version__",
]
