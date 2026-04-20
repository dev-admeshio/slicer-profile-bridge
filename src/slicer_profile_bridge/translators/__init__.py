"""Per-slicer translator modules.

Each submodule exposes a high-level `load_<slicer>(root)` returning a
`ProfileBundle`, plus lower-level `translate_printer / translate_filament
/ translate_process` helpers that act on a single resolved profile for
testability.
"""

from __future__ import annotations

from slicer_profile_bridge.translators.orca import load_orca

__all__ = ["load_orca"]
