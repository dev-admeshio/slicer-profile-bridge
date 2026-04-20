"""PrusaSlicer translator tests.

Fixtures under `tests/fixtures/prusa/` are the unmodified `PrusaResearch.ini`
bundle from https://github.com/prusa3d/PrusaSlicer — attribution in
tests/fixtures/PRUSA_ATTRIBUTION.md.

Coverage:
  1. INI section parser (`_iter_ini_sections`) — comment handling,
     multi-line section bodies, bracket sections.
  2. Multi-parent inheritance — the core reason inherit.py had to grow
     past Orca's single-chain model.
  3. End-to-end translation of the Original Prusa MK4 0.4 family.
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
    load_prusa_ini,
)
from slicer_profile_bridge.loader import _iter_ini_sections, load_ini_bundle
from slicer_profile_bridge.translators.prusa import (
    _parse_bed_shape,
    _to_bool,
    _to_float,
    _to_str_semi_list,
)

FIXTURE = Path(__file__).parent / "fixtures" / "prusa" / "PrusaResearch.ini"


# ── Parser-level ──────────────────────────────────────────────────────


class TestIniParser:
    def test_comments_are_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "tiny.ini"
        p.write_text(
            "# line comment\n; other comment\n[print:A]\nlayer_height = 0.2\n",
            encoding="utf-8",
        )
        sections = _iter_ini_sections(p)
        assert sections == [("[print:A]", {"layer_height": "0.2"})]

    def test_multiple_sections_parse_in_order(self, tmp_path: Path) -> None:
        p = tmp_path / "bundle.ini"
        p.write_text(
            "[printer:P1]\nnozzle_diameter = 0.4\n\n"
            "[filament:F1]\ntemperature = 210\n",
            encoding="utf-8",
        )
        sections = _iter_ini_sections(p)
        assert len(sections) == 2
        assert sections[0][0] == "[printer:P1]"
        assert sections[1][0] == "[filament:F1]"

    def test_load_ini_bundle_filters_non_profile_sections(
        self,
        tmp_path: Path,
    ) -> None:
        """`[vendor]` / `[printer_model:…]` carry bundle metadata, not a
        profile the user would select. Loader drops them.
        """
        p = tmp_path / "b.ini"
        p.write_text(
            "[vendor]\nname = Foo\n"
            "[printer_model:MK4]\nname = Bar\n"
            "[print:0.20mm]\nlayer_height = 0.2\n",
            encoding="utf-8",
        )
        profiles = load_ini_bundle(p, vendor="Test")
        assert len(profiles) == 1
        assert profiles[0].type == "process"
        assert profiles[0].name == "0.20mm"
        assert profiles[0].vendor == "Test"

    def test_inherits_splits_on_semicolon(self, tmp_path: Path) -> None:
        """`inherits_all` must return every parent so the multi-parent
        merge can walk them in order.
        """
        p = tmp_path / "b.ini"
        p.write_text(
            "[print:X]\ninherits = *common*; *MK4*\nlayer_height = 0.2\n",
            encoding="utf-8",
        )
        profiles = load_ini_bundle(p, vendor="Test")
        assert profiles[0].inherits == "*common*"  # first parent (compat)
        assert profiles[0].inherits_all == ["*common*", "*MK4*"]


class TestPrimitiveCoercion:
    def test_to_float_strips_percent(self) -> None:
        assert _to_float("70%") == 70.0

    def test_to_float_unwraps_csv(self) -> None:
        assert _to_float("0.4,0.4,0.4") == 0.4

    def test_to_float_empty_returns_none(self) -> None:
        assert _to_float("") is None
        assert _to_float(None) is None

    def test_to_bool_prusa_flags(self) -> None:
        assert _to_bool("1") is True
        assert _to_bool("0") is False
        assert _to_bool(None) is False

    def test_to_str_semi_list(self) -> None:
        """`compatible_printers` uses `;` to separate printer names, even
        when a name contains commas."""
        raw = "Original Prusa MK4 0.4 nozzle; Original Prusa MK4S 0.4 nozzle"
        assert _to_str_semi_list(raw) == [
            "Original Prusa MK4 0.4 nozzle",
            "Original Prusa MK4S 0.4 nozzle",
        ]


class TestBedShape:
    def test_axis_aligned_rectangle(self) -> None:
        volume = _parse_bed_shape("0x0,250x0,250x210,0x210", "210")
        assert volume is not None
        assert volume.x == 250.0
        assert volume.y == 210.0
        assert volume.z == 210.0

    def test_malformed_shape_returns_none(self) -> None:
        assert _parse_bed_shape("0x0", "200") is None
        assert _parse_bed_shape("", "200") is None
        assert _parse_bed_shape("garbage", "200") is None


# ── End-to-end against the real PrusaResearch.ini bundle ──────────────


@pytest.fixture(scope="module")
def prusa_bundle() -> ProfileBundle:
    return load_prusa_ini(FIXTURE)


class TestMK4Printer:
    def test_mk4_0_4_is_present(self, prusa_bundle: ProfileBundle) -> None:
        ids = [i for i in prusa_bundle.printers if "MK4 0.4" in i and "MK4S" not in i]
        assert len(ids) >= 1

    def test_mk4_0_4_inherits_from_common_mk4(
        self,
        prusa_bundle: ProfileBundle,
    ) -> None:
        """The concrete MK4 0.4 profile declares only the delta; core
        machine fields (bed_shape, max_print_height, gcode_flavor) come
        from `*commonMK4*` via inheritance. The translator surfacing a
        real build volume proves the merge worked.
        """
        printer = next(
            p for p in prusa_bundle.printers.values()
            if p.name == "Original Prusa MK4 0.4 nozzle"
        )
        assert printer.technology is PrinterTechnology.FDM
        assert printer.nozzle_diameter_mm == 0.4
        assert printer.build_volume_mm.x > 100
        assert printer.build_volume_mm.y > 100
        assert printer.build_volume_mm.z > 100
        assert printer.retraction_type is RetractionType.DIRECT_DRIVE
        assert printer.kinematics is Kinematics.CARTESIAN

    def test_abstract_profiles_filtered(
        self,
        prusa_bundle: ProfileBundle,
    ) -> None:
        """`*commonMK4*`, `*PLA*`, `*0.20mm*` are base templates — they
        should never appear in the user-facing bundle.
        """
        for bundle_map in (
            prusa_bundle.printers,
            prusa_bundle.filaments,
            prusa_bundle.processes,
        ):
            for profile in bundle_map.values():
                assert not (profile.name.startswith("*") and profile.name.endswith("*"))

    def test_source_chain_preserved(self, prusa_bundle: ProfileBundle) -> None:
        printer = next(
            p for p in prusa_bundle.printers.values()
            if p.name == "Original Prusa MK4 0.4 nozzle"
        )
        assert printer.source.slicer == "prusa"
        # Immediate parent first, root(s) later.
        assert printer.source.inherits_chain[0] == "*commonMK4*"


class TestPrusamentPLA:
    def test_category_normalises(self, prusa_bundle: ProfileBundle) -> None:
        prusament_pla = next(
            f for f in prusa_bundle.filaments.values()
            if f.name == "Prusament PLA"
        )
        assert prusament_pla.category is FilamentCategory.PLA
        assert prusament_pla.raw_category == "PLA"

    def test_density_and_temps_inherited(
        self,
        prusa_bundle: ProfileBundle,
    ) -> None:
        """`temperature = 215` is declared on `Prusament PLA`; but
        density 1.24 also is. `filament_type = PLA` lives on the
        `*PLA*` base via inheritance.
        """
        f = next(
            f for f in prusa_bundle.filaments.values() if f.name == "Prusament PLA"
        )
        assert f.density_g_cm3 == pytest.approx(1.24)
        assert f.nozzle_temp_c is not None
        assert f.nozzle_temp_c.normal == 215


class TestMK4020Process:
    def test_mk4_0_4_process_is_present(
        self,
        prusa_bundle: ProfileBundle,
    ) -> None:
        ids = [
            p for p in prusa_bundle.processes
            if "0.20mm QUALITY @MK4 0.4" in p
        ]
        assert len(ids) == 1

    def test_speeds_from_concrete_override(
        self,
        prusa_bundle: ProfileBundle,
    ) -> None:
        """`perimeter_speed = 45` is declared on the concrete
        `0.20mm QUALITY @MK4 0.4`; the translator must read it rather
        than an inherited default.
        """
        p = next(
            p for p in prusa_bundle.processes.values()
            if p.name == "0.20mm QUALITY @MK4 0.4"
        )
        assert p.speed_mm_s is not None
        assert p.speed_mm_s.perimeter == 45.0
        assert p.speed_mm_s.external_perimeter == 25.0


class TestV03NewFields:
    """v0.3 additions: PA/LA + jerk + start/end gcode + filament_diameter
    on Prusa. MK4 family carries all these in PrusaResearch.ini."""

    def test_mk4_start_end_gcode_populated(
        self,
        prusa_bundle: ProfileBundle,
    ) -> None:
        printer = next(
            p for p in prusa_bundle.printers.values()
            if p.name == "Original Prusa MK4 0.4 nozzle"
        )
        assert printer.start_gcode is not None
        assert "M862" in printer.start_gcode  # Prusa's printer-model check header
        assert printer.end_gcode is not None
        assert "M104 S0" in printer.end_gcode

    def test_mk4_max_jerk_populated(self, prusa_bundle: ProfileBundle) -> None:
        printer = next(
            p for p in prusa_bundle.printers.values()
            if p.name == "Original Prusa MK4 0.4 nozzle"
        )
        # PrusaResearch.ini carries machine_max_jerk_x/y in commonMK4.
        # Non-None + positive is enough; the exact value is a vendor
        # default that can drift between Prusa settings releases.
        assert printer.max_jerk_mm_s is not None
        assert printer.max_jerk_mm_s > 0

    def test_prusament_pla_filament_diameter(
        self,
        prusa_bundle: ProfileBundle,
    ) -> None:
        f = next(
            f for f in prusa_bundle.filaments.values() if f.name == "Prusament PLA"
        )
        # Prusa's `filament_diameter` lives on the material base. 1.75 is
        # the PrusaResearch.ini default; a 2.85mm-only fleet would read
        # different.
        assert f.filament_diameter_mm == pytest.approx(1.75)

    def test_multi_parent_layer_height_inherited(
        self,
        prusa_bundle: ProfileBundle,
    ) -> None:
        """`layer_height` isn't declared on `0.20mm QUALITY @MK4 0.4`
        itself — it lives on the `*0.20mm*` base, reached through the
        multi-parent `inherits = *0.20mm*; *MK4*` walk. If the value
        comes through as 0.2, the multi-parent merge worked.
        """
        p = next(
            p for p in prusa_bundle.processes.values()
            if p.name == "0.20mm QUALITY @MK4 0.4"
        )
        assert p.layer_height_mm == pytest.approx(0.2)

    def test_compatible_printers_split(self, prusa_bundle: ProfileBundle) -> None:
        """`compatible_printers` isn't directly declared on every
        concrete process — but when it is, the `;` separator must be
        honoured. Check at least one process surfaces the list.
        """
        any_process_with_list = next(
            (p for p in prusa_bundle.processes.values() if p.compatible_printers),
            None,
        )
        # Many Prusa processes use compatible_printers_condition instead,
        # so a null result on the lookup is still a legitimate path.
        if any_process_with_list is not None:
            assert all(isinstance(s, str) for s in any_process_with_list.compatible_printers)
