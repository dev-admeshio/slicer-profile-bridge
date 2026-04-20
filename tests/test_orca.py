"""OrcaSlicer translator tests.

Fixtures under `tests/fixtures/orca/` are unmodified copies of profiles
from https://github.com/SoftFever/OrcaSlicer — licence attribution lives
in tests/fixtures/ORCA_ATTRIBUTION.md.

Two layers of coverage:
  1. Primitive coercion (`_to_float`, `_to_int`, `_to_bool`,
     `_parse_printable_area`) — tested directly with crafted inputs so
     edge cases (empty list, `"nil"`, `"15%"`) have named regressions.
  2. End-to-end translation via `load_orca()` against the Bambu X1C
     profile family — exercises inheritance, category normalisation,
     and the printer / filament / process mappings at once.
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
    load_orca,
)
from slicer_profile_bridge.translators.orca import (
    _parse_printable_area,
    _to_bool,
    _to_float,
    _to_int,
    _to_str_list,
)

FIXTURES = Path(__file__).parent / "fixtures" / "orca"


# ── Primitive coercion ────────────────────────────────────────────────


class TestPrimitiveCoercion:
    def test_to_float_unwraps_single_list(self) -> None:
        assert _to_float(["220"]) == 220.0

    def test_to_float_strips_percent(self) -> None:
        assert _to_float("15%") == 15.0

    def test_to_float_returns_none_for_nil(self) -> None:
        assert _to_float("nil") is None
        assert _to_float(None) is None
        assert _to_float([]) is None

    def test_to_float_handles_scalar_string(self) -> None:
        assert _to_float("0.98") == pytest.approx(0.98)

    def test_to_int_truncates(self) -> None:
        assert _to_int("5") == 5
        assert _to_int("5.9") == 5
        assert _to_int(None) is None

    def test_to_bool_orca_style(self) -> None:
        assert _to_bool("1") is True
        assert _to_bool(["1"]) is True
        assert _to_bool("0") is False
        assert _to_bool(None) is False
        assert _to_bool("true") is True

    def test_str_list_handles_scalar(self) -> None:
        assert _to_str_list("foo") == ["foo"]

    def test_str_list_filters_empty(self) -> None:
        assert _to_str_list(["", "a", "  "]) == ["a"]


class TestPrintableArea:
    def test_bambu_x1c_bed_polygon(self) -> None:
        """Orca expresses the bed as a 4-corner polygon. 256×256 bed →
        canonical BuildVolumeMm 256×256×256."""
        area = ["0x0", "256x0", "256x256", "0x256"]
        volume = _parse_printable_area(area, "256")
        assert volume is not None
        assert volume.x == 256.0
        assert volume.y == 256.0
        assert volume.z == 256.0

    def test_malformed_points_are_skipped(self) -> None:
        area = ["0x0", "bogus", "200x200"]
        volume = _parse_printable_area(area, "250")
        assert volume is not None
        assert volume.x == 200.0
        assert volume.y == 200.0

    def test_zero_span_returns_none(self) -> None:
        assert _parse_printable_area(["0x0"], "250") is None


# ── End-to-end: load the BBL fixture dir ──────────────────────────────


@pytest.fixture(scope="module")
def bbl_bundle() -> ProfileBundle:
    """Load every Orca-format profile under tests/fixtures/orca/.

    Module-scoped because parsing + resolving inheritance for every
    profile on every test is wasted work — the bundle is immutable
    downstream.
    """
    return load_orca(FIXTURES)


class TestBambuX1CPrinter:
    def test_printer_is_indexed(self, bbl_bundle: ProfileBundle) -> None:
        # Concrete profile only — the abstract bases
        # (`fdm_bbl_3dp_001_common`, `fdm_machine_common`) are filtered
        # out of the bundle even though the loader saw them.
        ids = list(bbl_bundle.printers.keys())
        x1c_ids = [i for i in ids if "X1 Carbon" in i]
        assert len(x1c_ids) == 1, f"expected one X1C printer, got {ids}"

    def test_printer_basic_fields(self, bbl_bundle: ProfileBundle) -> None:
        printer = next(
            p for p in bbl_bundle.printers.values() if "X1 Carbon" in p.name
        )
        assert printer.technology is PrinterTechnology.FDM
        assert printer.nozzle_diameter_mm == 0.4
        assert printer.kinematics is Kinematics.COREXY  # inferred from model name
        assert printer.enclosure is True                # X1C is enclosed
        assert printer.retraction_type is RetractionType.DIRECT_DRIVE

    def test_printer_build_volume_from_inherited_chain(
        self,
        bbl_bundle: ProfileBundle,
    ) -> None:
        """`printable_area` is declared in `fdm_bbl_3dp_001_common`, not in
        the concrete X1C_0.4 profile. Checks inheritance resolved it.
        """
        printer = next(
            p for p in bbl_bundle.printers.values() if "X1 Carbon" in p.name
        )
        assert printer.build_volume_mm.x > 0
        assert printer.build_volume_mm.y > 0
        assert printer.build_volume_mm.z > 0

    def test_printer_source_trails_inheritance_chain(
        self,
        bbl_bundle: ProfileBundle,
    ) -> None:
        printer = next(
            p for p in bbl_bundle.printers.values() if "X1 Carbon" in p.name
        )
        assert printer.source.slicer == "orca"
        assert printer.source.vendor == "BBL"
        # Parents in root-last order; the concrete profile's direct parent
        # should be the immediate ancestor.
        assert printer.source.inherits_chain[0] == "fdm_bbl_3dp_001_common"
        assert printer.source.inherits_chain[-1] == "fdm_machine_common"


class TestBambuPlaBasicFilament:
    def test_filament_is_indexed_and_concrete_only(
        self,
        bbl_bundle: ProfileBundle,
    ) -> None:
        # Only the concrete `@BBL X1C` shows up; the `@base` and
        # `fdm_filament_pla` / `fdm_filament_common` bases are filtered.
        ids = [f for f in bbl_bundle.filaments if "PLA Basic" in f]
        assert len(ids) == 1
        assert "BBL X1C" in ids[0]

    def test_filament_category_normalised(self, bbl_bundle: ProfileBundle) -> None:
        f = next(f for f in bbl_bundle.filaments.values() if "PLA Basic" in f.name)
        assert f.category is FilamentCategory.PLA
        assert f.raw_category == "PLA"

    def test_filament_merged_values_from_base(
        self,
        bbl_bundle: ProfileBundle,
    ) -> None:
        """`filament_flow_ratio`, `filament_density`, and
        `nozzle_temperature` all live in the `@BBL X1C` override or its
        `@base` parent. Both must be reachable after inheritance merge.
        """
        f = next(f for f in bbl_bundle.filaments.values() if "PLA Basic" in f.name)
        assert f.flow_ratio == pytest.approx(0.98)             # @BBL X1C override
        assert f.density_g_cm3 == pytest.approx(1.26)          # from @base
        assert f.max_volumetric_speed_mm3_s == pytest.approx(21)  # @BBL X1C
        assert f.nozzle_temp_c is not None
        assert f.nozzle_temp_c.normal == 220
        assert f.nozzle_temp_c.first_layer == 220


class TestBambu020Process:
    def test_process_is_indexed(self, bbl_bundle: ProfileBundle) -> None:
        ids = [p for p in bbl_bundle.processes if "0.20mm Standard @BBL X1C" in p]
        assert len(ids) == 1

    def test_process_layer_geometry_from_parent(
        self,
        bbl_bundle: ProfileBundle,
    ) -> None:
        """`layer_height` lives in `fdm_process_common`, not in the
        concrete `0.20mm Standard @BBL X1C`. Inheritance resolve should
        walk up and surface it.
        """
        p = next(p for p in bbl_bundle.processes.values() if "0.20mm Standard @BBL X1C" in p.name)
        assert p.layer_height_mm == 0.2
        # top_shell_layers = 5 lives on fdm_process_single_0.20
        assert p.top_shell_layers == 5

    def test_process_speeds(self, bbl_bundle: ProfileBundle) -> None:
        p = next(p for p in bbl_bundle.processes.values() if "0.20mm Standard @BBL X1C" in p.name)
        assert p.speed_mm_s is not None
        # Declared in the concrete profile
        assert p.speed_mm_s.perimeter == 300          # inner_wall_speed
        assert p.speed_mm_s.external_perimeter == 200  # outer_wall_speed
        assert p.speed_mm_s.travel == 500

    def test_process_compatible_printers(self, bbl_bundle: ProfileBundle) -> None:
        p = next(p for p in bbl_bundle.processes.values() if "0.20mm Standard @BBL X1C" in p.name)
        assert "Bambu Lab X1 Carbon 0.4 nozzle" in p.compatible_printers


class TestCompose:
    def test_compose_returns_fully_typed_recipe(
        self,
        bbl_bundle: ProfileBundle,
    ) -> None:
        printer_id = next(
            i for i in bbl_bundle.printers if "X1 Carbon" in i
        )
        filament_id = next(
            i for i in bbl_bundle.filaments if "PLA Basic" in i
        )
        process_id = next(
            i for i in bbl_bundle.processes if "0.20mm Standard" in i
        )
        recipe = bbl_bundle.compose(printer_id, filament_id, process_id)
        assert recipe.printer.technology is PrinterTechnology.FDM
        assert recipe.filament.category is FilamentCategory.PLA
        assert recipe.process.layer_height_mm == 0.2
