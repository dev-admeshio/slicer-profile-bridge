"""Heuristics helper tests — the `enclosure_required` derivation gates
on category / bed temp / name hints. Any of the three gates trips → True.

These exist because the three translators (orca / prusa / bambu) each
need to set `CanonicalFilament.enclosure_required` at emit time, and
that derivation is a shared policy — duplicating it per translator is
where drift creeps in.
"""

from __future__ import annotations

import pytest

from slicer_profile_bridge.heuristics import infer_filament_enclosure_required


class TestCategoryGate:
    @pytest.mark.parametrize("category", [
        "abs", "asa", "pc", "polycarbonate", "pa", "nylon",
        "pa_cf", "pa_gf", "ppa", "pet_cf", "peek", "pei",
        # Normalisation: hyphen → underscore.
        "pa-cf", "pet-cf",
        # Case insensitivity.
        "ABS", "Asa", "PC",
    ])
    def test_known_engineering_categories_require_enclosure(self, category: str) -> None:
        assert infer_filament_enclosure_required(
            category=category, bed_temp_normal_c=None, name=None,
        ) is True

    @pytest.mark.parametrize("category", [
        "pla", "pla_cf", "pla_hs", "pla_silk",
        "petg", "petg_cf",
        "tpu", "tpu_85a", "tpu_95a",
        "pva", "hips", "pet", "bvoh",
    ])
    def test_pla_tier_categories_do_not_require_enclosure(self, category: str) -> None:
        assert infer_filament_enclosure_required(
            category=category, bed_temp_normal_c=None, name=None,
        ) is False


class TestBedTempGate:
    def test_high_bed_triggers_regardless_of_category(self) -> None:
        # PLA bed 100°C shouldn't happen in practice but proves the bed gate
        # works independently of category.
        assert infer_filament_enclosure_required(
            category="pla", bed_temp_normal_c=110.0, name=None,
        ) is True

    def test_boundary_95c_triggers(self) -> None:
        assert infer_filament_enclosure_required(
            category=None, bed_temp_normal_c=95.0, name=None,
        ) is True

    def test_just_below_95c_does_not_trigger(self) -> None:
        assert infer_filament_enclosure_required(
            category=None, bed_temp_normal_c=94.9, name=None,
        ) is False

    def test_standard_pla_bed(self) -> None:
        assert infer_filament_enclosure_required(
            category=None, bed_temp_normal_c=60.0, name=None,
        ) is False


class TestNameHintGate:
    @pytest.mark.parametrize("name", [
        "Generic ABS @Anker",
        "Prusament ASA",
        "Bambu PC Carbon",
        "Polymaker Nylon",
        "Esun PA-12",
        "ColorFabb PA6 Pro",
        "Polymaker PEEK",
    ])
    def test_name_containing_engineering_chemistry_fires(self, name: str) -> None:
        assert infer_filament_enclosure_required(
            category=None, bed_temp_normal_c=None, name=name,
        ) is True

    @pytest.mark.parametrize("name", [
        "Prusament PLA",
        "Generic PETG",
        "PolyLite TPU",
        "Bambu PLA Basic",
        # Substring false-positive canaries — names that contain ABS/PC
        # letters but aren't engineering filaments.
        "Stabilized PLA",
        "Compact PLA",
    ])
    def test_name_without_chemistry_hint_stays_silent(self, name: str) -> None:
        assert infer_filament_enclosure_required(
            category="pla", bed_temp_normal_c=60.0, name=name,
        ) is False


class TestCombinedGates:
    def test_all_three_gates_off_returns_false(self) -> None:
        assert infer_filament_enclosure_required(
            category="pla", bed_temp_normal_c=55.0, name="Bambu PLA Basic",
        ) is False

    def test_missing_data_returns_false(self) -> None:
        # All-None input shouldn't crash, just conservatively say False.
        assert infer_filament_enclosure_required(
            category=None, bed_temp_normal_c=None, name=None,
        ) is False

    def test_any_single_gate_sufficient(self) -> None:
        # category gate alone
        assert infer_filament_enclosure_required("abs", 60.0, "Some Name") is True
        # bed gate alone
        assert infer_filament_enclosure_required("unknown", 100.0, "Some Name") is True
        # name gate alone
        assert infer_filament_enclosure_required("unknown", 60.0, "ASA Pro") is True
