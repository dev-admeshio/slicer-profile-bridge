"""Smoke tests for the canonical schema.

Intent: verify models round-trip cleanly, enums enforce their domain,
required fields fail loudly when missing, and ProfileBundle.compose
surfaces useful errors. Translator-level tests live alongside each
translator module as that work lands.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from slicer_profile_bridge import (
    CanonicalFilament,
    CanonicalPrinter,
    CanonicalProcess,
    FilamentCategory,
    Kinematics,
    PrinterTechnology,
    ProfileBundle,
    RetractionType,
)
from slicer_profile_bridge.schema import (
    AdhesionSettings,
    BedTemps,
    BuildVolumeMm,
    CoolingSettings,
    InfillPattern,
    NozzleTemps,
    NozzleType,
    ProcessSpeeds,
    SourceMetadata,
    SupportPattern,
    SupportSettings,
)


def _source(slicer: str = "orca", source_id: str = "test") -> SourceMetadata:
    return SourceMetadata(slicer=slicer, source_id=source_id)


def _valid_fdm_printer() -> CanonicalPrinter:
    return CanonicalPrinter(
        id="BBL/X1C_0.4",
        name="Bambu Lab X1 Carbon (0.4 nozzle)",
        vendor="BBL",
        technology=PrinterTechnology.FDM,
        build_volume_mm=BuildVolumeMm(x=256, y=256, z=256),
        nozzle_diameter_mm=0.4,
        kinematics=Kinematics.COREXY,
        retraction_type=RetractionType.DIRECT_DRIVE,
        enclosure=True,
        source=_source(source_id="BBL/X1C_0.4"),
    )


def _valid_pla_filament() -> CanonicalFilament:
    return CanonicalFilament(
        id="BBL/PLA_Basic",
        name="Bambu PLA Basic",
        vendor="BBL",
        category=FilamentCategory.PLA,
        nozzle_temp_c=NozzleTemps(normal=220, first_layer=220, range_min=190, range_max=240),
        bed_temp_c=BedTemps(normal=55, first_layer=55),
        flow_ratio=0.98,
        density_g_cm3=1.24,
        shrinkage_pct=0.3,
        max_volumetric_speed_mm3_s=18,
        cooling=CoolingSettings(
            fan_min_pct=100,
            fan_max_pct=100,
            fan_cooling_layer_time_s=4,
            disable_fan_first_layers=1,
        ),
        bed_adhesion_rating=100,
        source=_source(source_id="BBL/PLA_Basic"),
    )


def _valid_process() -> CanonicalProcess:
    return CanonicalProcess(
        id="BBL/0.20mm_Standard",
        name="0.20mm Standard @X1C",
        layer_height_mm=0.2,
        first_layer_height_mm=0.2,
        wall_count=3,
        top_shell_layers=5,
        bottom_shell_layers=3,
        infill_pct=15,
        infill_pattern=InfillPattern.GYROID,
        speed_mm_s=ProcessSpeeds(perimeter=200, infill=270, travel=500, first_layer=50),
        support=SupportSettings(enabled=False, pattern=SupportPattern.TREE),
        adhesion=AdhesionSettings(skirt_loops=1, brim_width_mm=0),
        source=_source(source_id="BBL/0.20mm_Standard"),
    )


class TestCanonicalPrinter:
    def test_valid_fdm_round_trips(self) -> None:
        printer = _valid_fdm_printer()
        dumped = printer.model_dump()
        reloaded = CanonicalPrinter.model_validate(dumped)
        assert reloaded == printer

    def test_build_volume_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            BuildVolumeMm(x=0, y=200, z=200)
        with pytest.raises(ValidationError):
            BuildVolumeMm(x=200, y=-1, z=200)

    def test_unknown_field_rejected(self) -> None:
        """`extra="forbid"` catches translator bugs early."""
        with pytest.raises(ValidationError):
            CanonicalPrinter.model_validate(
                {
                    **_valid_fdm_printer().model_dump(),
                    "not_a_real_field": "oops",
                }
            )

    def test_resin_printer_nozzle_optional(self) -> None:
        """Resin machines legitimately omit nozzle_diameter_mm."""
        resin = CanonicalPrinter(
            id="Elegoo/Saturn3",
            name="Elegoo Saturn 3 Ultra",
            vendor="Elegoo",
            technology=PrinterTechnology.RESIN_MSLA,
            build_volume_mm=BuildVolumeMm(x=219, y=123, z=260),
            pixel_size_mm=0.057,
            lcd_resolution_px=(3840, 2160),
            z_step_mm=0.01,
            source=_source(source_id="Elegoo/Saturn3"),
        )
        assert resin.nozzle_diameter_mm is None


class TestCanonicalFilament:
    def test_valid_pla_round_trips(self) -> None:
        f = _valid_pla_filament()
        assert CanonicalFilament.model_validate(f.model_dump()) == f

    def test_flow_ratio_bounded(self) -> None:
        """Flow ratio > 2 or ≤ 0 is never legitimate."""
        base = _valid_pla_filament().model_dump()
        with pytest.raises(ValidationError):
            CanonicalFilament.model_validate({**base, "flow_ratio": 2.5})
        with pytest.raises(ValidationError):
            CanonicalFilament.model_validate({**base, "flow_ratio": 0.0})

    def test_raw_category_preserved(self) -> None:
        """Normalised + raw both survive so consumers can do their own
        fine-grained classification."""
        silk = _valid_pla_filament().model_copy(
            update={"category": FilamentCategory.PLA, "raw_category": "PLA Silk"}
        )
        assert silk.category is FilamentCategory.PLA
        assert silk.raw_category == "PLA Silk"


class TestCanonicalProcess:
    def test_valid_process_round_trips(self) -> None:
        p = _valid_process()
        assert CanonicalProcess.model_validate(p.model_dump()) == p

    def test_infill_pct_bounded(self) -> None:
        base = _valid_process().model_dump()
        with pytest.raises(ValidationError):
            CanonicalProcess.model_validate({**base, "infill_pct": 150})
        with pytest.raises(ValidationError):
            CanonicalProcess.model_validate({**base, "infill_pct": -1})

    def test_layer_height_required(self) -> None:
        base = _valid_process().model_dump()
        del base["layer_height_mm"]
        with pytest.raises(ValidationError):
            CanonicalProcess.model_validate(base)


class TestProfileBundle:
    def test_compose_produces_recipe(self) -> None:
        bundle = ProfileBundle(
            slicer="orca",
            printers={"BBL/X1C_0.4": _valid_fdm_printer()},
            filaments={"BBL/PLA_Basic": _valid_pla_filament()},
            processes={"BBL/0.20mm_Standard": _valid_process()},
        )
        recipe = bundle.compose(
            printer_id="BBL/X1C_0.4",
            filament_id="BBL/PLA_Basic",
            process_id="BBL/0.20mm_Standard",
        )
        assert recipe.printer.name == "Bambu Lab X1 Carbon (0.4 nozzle)"
        assert recipe.filament.category is FilamentCategory.PLA
        assert recipe.process.layer_height_mm == 0.2

    def test_compose_missing_printer_is_keyerror_with_id(self) -> None:
        bundle = ProfileBundle(slicer="orca")
        with pytest.raises(KeyError) as exc_info:
            bundle.compose("nope", "nope", "nope")
        assert "nope" in str(exc_info.value)

    def test_compose_missing_filament_names_the_missing_piece(self) -> None:
        bundle = ProfileBundle(
            slicer="orca",
            printers={"BBL/X1C_0.4": _valid_fdm_printer()},
        )
        with pytest.raises(KeyError, match="filament"):
            bundle.compose("BBL/X1C_0.4", "nope", "nope")


# ── v0.3 additions: motion tuning + custom gcode + filament diameter ──


class TestV03PrinterMotionTuning:
    def test_pressure_advance_non_negative(self) -> None:
        base = _valid_fdm_printer().model_dump()
        CanonicalPrinter.model_validate({**base, "pressure_advance_k": 0.0})  # zero OK
        CanonicalPrinter.model_validate({**base, "pressure_advance_k": 0.045})
        with pytest.raises(ValidationError):
            CanonicalPrinter.model_validate({**base, "pressure_advance_k": -0.01})

    def test_max_jerk_must_be_positive(self) -> None:
        base = _valid_fdm_printer().model_dump()
        CanonicalPrinter.model_validate({**base, "max_jerk_mm_s": 8.0})
        with pytest.raises(ValidationError):
            CanonicalPrinter.model_validate({**base, "max_jerk_mm_s": 0.0})
        with pytest.raises(ValidationError):
            CanonicalPrinter.model_validate({**base, "max_jerk_mm_s": -1.0})

    def test_junction_deviation_non_negative(self) -> None:
        base = _valid_fdm_printer().model_dump()
        CanonicalPrinter.model_validate({**base, "junction_deviation_mm": 0.05})
        CanonicalPrinter.model_validate({**base, "junction_deviation_mm": 0.0})
        with pytest.raises(ValidationError):
            CanonicalPrinter.model_validate({**base, "junction_deviation_mm": -0.01})

    def test_start_end_gcode_are_strings(self) -> None:
        base = _valid_fdm_printer().model_dump()
        p = CanonicalPrinter.model_validate({
            **base,
            "start_gcode": "G28\nG29",
            "end_gcode": "M104 S0\nM140 S0",
        })
        assert "G28" in p.start_gcode
        assert "M104" in p.end_gcode


class TestV03NozzleType:
    def test_default_unknown(self) -> None:
        p = _valid_fdm_printer()
        assert p.nozzle_type is NozzleType.UNKNOWN

    def test_enum_values_accepted(self) -> None:
        base = _valid_fdm_printer().model_dump()
        for nt in (NozzleType.VOLCANO, NozzleType.CHT, NozzleType.HIGH_FLOW):
            p = CanonicalPrinter.model_validate({**base, "nozzle_type": nt.value})
            assert p.nozzle_type is nt

    def test_unknown_string_rejected(self) -> None:
        base = _valid_fdm_printer().model_dump()
        with pytest.raises(ValidationError):
            CanonicalPrinter.model_validate({**base, "nozzle_type": "not_a_nozzle"})


class TestV03FilamentDiameter:
    def test_default_none(self) -> None:
        # Consumers treat None as "assume 1.75" but must not mix that
        # with a 2.85mm printer silently — schema doesn't default because
        # silent default is the trap.
        f = _valid_pla_filament()
        assert f.filament_diameter_mm is None

    def test_1_75_and_2_85_both_valid(self) -> None:
        base = _valid_pla_filament().model_dump()
        CanonicalFilament.model_validate({**base, "filament_diameter_mm": 1.75})
        CanonicalFilament.model_validate({**base, "filament_diameter_mm": 2.85})

    def test_non_positive_rejected(self) -> None:
        base = _valid_pla_filament().model_dump()
        with pytest.raises(ValidationError):
            CanonicalFilament.model_validate({**base, "filament_diameter_mm": 0.0})
        with pytest.raises(ValidationError):
            CanonicalFilament.model_validate({**base, "filament_diameter_mm": -1.75})


class TestV03ProcessPressureAdvance:
    def test_process_pa_override(self) -> None:
        """Process-level PA wins over printer PA. Schema only enforces
        the bound; resolution-priority is a consumer concern."""
        base = _valid_process().model_dump()
        CanonicalProcess.model_validate({**base, "pressure_advance_k": 0.035})
        with pytest.raises(ValidationError):
            CanonicalProcess.model_validate({**base, "pressure_advance_k": -0.01})
