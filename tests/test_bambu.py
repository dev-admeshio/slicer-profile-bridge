"""BambuStudio translator tests.

Fixtures under `tests/fixtures/bambu/` are unmodified profiles from
https://github.com/bambulab/BambuStudio — attribution in
tests/fixtures/BAMBU_ATTRIBUTION.md.

The Bambu translator is a thin re-label over the Orca translator (same
JSON format), so the tests mirror Orca's end-to-end assertions and
additionally confirm `source.slicer == "bambu"` throughout the output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slicer_profile_bridge import (
    FilamentCategory,
    Kinematics,
    PrinterTechnology,
    ProfileBundle,
    RetractionType,
    load_bambu,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bambu"


@pytest.fixture(scope="module")
def bambu_bundle() -> ProfileBundle:
    return load_bambu(FIXTURES)


class TestBambuPrinter:
    def test_x1c_is_present(self, bambu_bundle: ProfileBundle) -> None:
        x1c_ids = [i for i in bambu_bundle.printers if "X1 Carbon" in i]
        assert len(x1c_ids) == 1

    def test_x1c_basic_fields(self, bambu_bundle: ProfileBundle) -> None:
        printer = next(
            p for p in bambu_bundle.printers.values() if "X1 Carbon" in p.name
        )
        assert printer.technology is PrinterTechnology.FDM
        assert printer.nozzle_diameter_mm == 0.4
        assert printer.kinematics is Kinematics.COREXY
        assert printer.enclosure is True
        assert printer.retraction_type is RetractionType.DIRECT_DRIVE

    def test_x1c_build_volume(self, bambu_bundle: ProfileBundle) -> None:
        printer = next(
            p for p in bambu_bundle.printers.values() if "X1 Carbon" in p.name
        )
        assert printer.build_volume_mm.x > 200
        assert printer.build_volume_mm.y > 200
        assert printer.build_volume_mm.z > 200

    def test_source_labelled_bambu(self, bambu_bundle: ProfileBundle) -> None:
        """Core reason the Bambu translator exists — Orca and Bambu
        ship near-identical JSON, but downstream consumers still want
        to know which slicer the profile came from."""
        printer = next(
            p for p in bambu_bundle.printers.values() if "X1 Carbon" in p.name
        )
        assert printer.source.slicer == "bambu"
        assert printer.source.vendor == "BBL"
        # Inheritance chain carried through from the shared resolver.
        assert printer.source.inherits_chain[0] == "fdm_bbl_3dp_001_common"


class TestBambuFilament:
    def test_pla_basic_resolved(self, bambu_bundle: ProfileBundle) -> None:
        f = next(
            f for f in bambu_bundle.filaments.values() if "PLA Basic" in f.name
        )
        assert f.category is FilamentCategory.PLA
        assert f.flow_ratio == pytest.approx(0.98)
        assert f.density_g_cm3 == pytest.approx(1.26)
        assert f.nozzle_temp_c is not None
        assert f.nozzle_temp_c.normal == 220

    def test_filament_source_labelled_bambu(
        self,
        bambu_bundle: ProfileBundle,
    ) -> None:
        f = next(
            f for f in bambu_bundle.filaments.values() if "PLA Basic" in f.name
        )
        assert f.source.slicer == "bambu"


class TestBambuProcess:
    def test_020mm_standard_resolves_layer_height(
        self,
        bambu_bundle: ProfileBundle,
    ) -> None:
        p = next(
            p for p in bambu_bundle.processes.values()
            if p.name == "0.20mm Standard @BBL X1C"
        )
        assert p.layer_height_mm == 0.2
        assert p.source.slicer == "bambu"


class TestCrossSlicerConsistency:
    """Same BBL X1C printer on Bambu vs Orca should yield the same
    canonical build volume, nozzle diameter, and kinematics — the
    translators differ only in the source label.
    """

    def test_x1c_volume_matches_orca_fixture(
        self,
        bambu_bundle: ProfileBundle,
    ) -> None:
        from slicer_profile_bridge import load_orca

        orca_fixtures = Path(__file__).parent / "fixtures" / "orca"
        orca_bundle = load_orca(orca_fixtures)

        bambu_printer = next(
            p for p in bambu_bundle.printers.values() if "X1 Carbon" in p.name
        )
        orca_printer = next(
            p for p in orca_bundle.printers.values() if "X1 Carbon" in p.name
        )
        assert bambu_printer.build_volume_mm == orca_printer.build_volume_mm
        assert bambu_printer.nozzle_diameter_mm == orca_printer.nozzle_diameter_mm
        assert bambu_printer.kinematics == orca_printer.kinematics
        # Only source.slicer should differ.
        assert bambu_printer.source.slicer == "bambu"
        assert orca_printer.source.slicer == "orca"
