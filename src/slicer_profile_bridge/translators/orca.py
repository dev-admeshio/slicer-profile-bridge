"""OrcaSlicer profile translator.

Maps Orca's JSON profile format into the canonical schema. Covers the
three concrete profile types — machine (printer), filament, process —
and resolves inheritance before translating so a concrete profile sees
the effective value of every inherited field.

Field-mapping rationale lives next to the code it applies to. The
authoritative list of Orca fields is whatever
`SoftFever/OrcaSlicer/resources/profiles/*.json` contains; we map the
subset needed to populate the canonical schema and preserve the rest in
`SourceMetadata.inherits_chain` for traceability.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slicer_profile_bridge.inherit import ResolvedProfile, resolve
from slicer_profile_bridge.loader import index_directory
from slicer_profile_bridge.schema import (
    AdhesionSettings,
    AxisSpeeds,
    BedTemps,
    BuildVolumeMm,
    CanonicalFilament,
    CanonicalPrinter,
    CanonicalProcess,
    CoolingSettings,
    FilamentCategory,
    InfillPattern,
    Kinematics,
    NozzleTemps,
    NozzleType,
    PrinterTechnology,
    ProcessSpeeds,
    ProfileBundle,
    RetractionSettings,
    RetractionType,
    SeamPosition,
    SourceMetadata,
    SupportPattern,
    SupportSettings,
)

# Metadata keys we strip from the raw_data pass-through — these describe
# the profile's place in the inheritance DAG, not its effective values,
# and repeating them inside `raw_data` would just invite confusion.
_METADATA_KEYS: frozenset[str] = frozenset({
    "type", "name", "inherits", "from", "setting_id", "instantiation",
    "filament_id", "description",
})


def _strip_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `data` without the profile-metadata keys."""
    return {k: v for k, v in data.items() if k not in _METADATA_KEYS}


# ── Primitive coercion ────────────────────────────────────────────────
# Orca stores almost every numeric value as a string, and frequently
# wraps scalars in a one-element list so multi-extruder machines can
# carry per-extruder overrides. These helpers normalise the weird shapes
# once so the field-mapping code below stays readable.


def _first(value: Any) -> Any:
    """Unwrap Orca's single-extruder wrapping: `["220"]` → `"220"`."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _to_float(value: Any) -> float | None:
    """Parse a scalar / list-scalar into a float. Percent strings are
    stripped because Orca writes e.g. `"15%"` for infill density; the
    caller decides whether that percent should become 15.0 or 0.15.
    """
    scalar = _first(value)
    if scalar is None:
        return None
    if isinstance(scalar, (int, float)):
        return float(scalar)
    if isinstance(scalar, str):
        s = scalar.strip().rstrip("%").strip()
        if not s or s.lower() in ("nil", "null"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _to_int(value: Any) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _to_bool(value: Any) -> bool:
    """Orca encodes booleans as `"0"` / `"1"` strings."""
    scalar = _first(value)
    if isinstance(scalar, bool):
        return scalar
    if isinstance(scalar, (int, float)):
        return scalar != 0
    if isinstance(scalar, str):
        return scalar.strip().lower() in ("1", "true", "yes", "on")
    return False


def _to_str(value: Any) -> str | None:
    scalar = _first(value)
    if scalar is None:
        return None
    return str(scalar).strip() or None


def _to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# ── Enum mapping ──────────────────────────────────────────────────────

_FILAMENT_CATEGORY_MAP = {
    "pla": FilamentCategory.PLA,
    "pla+": FilamentCategory.PLA,
    "pla plus": FilamentCategory.PLA,
    "pla silk": FilamentCategory.PLA,
    "pla cf": FilamentCategory.PLA_CF,
    "pla-cf": FilamentCategory.PLA_CF,
    "petg": FilamentCategory.PETG,
    "pet": FilamentCategory.PETG,
    "petg cf": FilamentCategory.PET_CF,
    "pet-cf": FilamentCategory.PET_CF,
    "abs": FilamentCategory.ABS,
    "asa": FilamentCategory.ASA,
    "tpu": FilamentCategory.TPU,
    "tpu 95a": FilamentCategory.TPU,
    "tpu 85a": FilamentCategory.TPU,
    "pa": FilamentCategory.PA,
    "nylon": FilamentCategory.PA,
    "pa6": FilamentCategory.PA,
    "pa12": FilamentCategory.PA,
    "pc": FilamentCategory.PC,
    "hips": FilamentCategory.HIPS,
    "pva": FilamentCategory.PVA,
    "pvb": FilamentCategory.PVB,
}


def _normalise_filament_category(raw: str | None) -> FilamentCategory:
    if not raw:
        return FilamentCategory.OTHER
    key = raw.strip().lower()
    if key in _FILAMENT_CATEGORY_MAP:
        return _FILAMENT_CATEGORY_MAP[key]
    # Try looser match — "PLA Aero" → PLA, "ABS Plus" → ABS
    for prefix, cat in _FILAMENT_CATEGORY_MAP.items():
        if key.startswith(prefix + " "):
            return cat
    return FilamentCategory.OTHER


_INFILL_PATTERN_MAP = {
    "grid": InfillPattern.GRID,
    "line": InfillPattern.LINE,
    "lines": InfillPattern.LINE,
    "triangles": InfillPattern.TRIANGLES,
    "cubic": InfillPattern.CUBIC,
    "gyroid": InfillPattern.GYROID,
    "honeycomb": InfillPattern.HONEYCOMB,
    "hilbert": InfillPattern.HILBERT,
    "hilbertcurve": InfillPattern.HILBERT,
    "concentric": InfillPattern.CONCENTRIC,
    "adaptivecubic": InfillPattern.ADAPTIVE_CUBIC,
    "adaptive_cubic": InfillPattern.ADAPTIVE_CUBIC,
    "lightning": InfillPattern.LIGHTNING,
}


def _normalise_infill(raw: str | None) -> tuple[InfillPattern, str | None]:
    if not raw:
        return InfillPattern.OTHER, None
    key = raw.strip().lower().replace(" ", "_")
    return _INFILL_PATTERN_MAP.get(key, InfillPattern.OTHER), raw


_SUPPORT_TYPE_RE = re.compile(r"^(normal|tree|snug|organic|grid)", re.IGNORECASE)


def _normalise_support_type(raw: str | None) -> SupportPattern:
    if not raw:
        return SupportPattern.UNKNOWN
    m = _SUPPORT_TYPE_RE.match(raw.strip())
    if not m:
        return SupportPattern.UNKNOWN
    tag = m.group(1).lower()
    return {
        "normal": SupportPattern.GRID,
        "grid": SupportPattern.GRID,
        "snug": SupportPattern.SNUG,
        "tree": SupportPattern.TREE,
        "organic": SupportPattern.ORGANIC,
    }.get(tag, SupportPattern.UNKNOWN)


_SEAM_MAP = {
    "nearest": SeamPosition.NEAREST,
    "aligned": SeamPosition.ALIGNED,
    "back": SeamPosition.BACK,
    "random": SeamPosition.RANDOM,
}


def _normalise_seam(raw: str | None) -> SeamPosition:
    if not raw:
        return SeamPosition.UNKNOWN
    return _SEAM_MAP.get(raw.strip().lower(), SeamPosition.UNKNOWN)


# ── Printer-specific helpers ──────────────────────────────────────────


_PRINTABLE_AREA_RE = re.compile(r"^\s*([\d.]+)\s*x\s*([\d.]+)\s*$")


def _parse_printable_area(area: Any, height: Any) -> BuildVolumeMm | None:
    """Orca's `printable_area` is a list of `"x x y"` points defining the
    bed polygon. We take axis-aligned bounding box of that polygon as the
    canonical build volume X/Y.

    `printable_height` is a scalar.
    """
    points = area if isinstance(area, list) else []
    xs: list[float] = []
    ys: list[float] = []
    for p in points:
        if not isinstance(p, str):
            continue
        m = _PRINTABLE_AREA_RE.match(p)
        if not m:
            continue
        xs.append(float(m.group(1)))
        ys.append(float(m.group(2)))
    if not xs or not ys:
        return None
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    z = _to_float(height)
    if x_span <= 0 or y_span <= 0 or not z or z <= 0:
        return None
    return BuildVolumeMm(x=x_span, y=y_span, z=z)


_COREXY_KEYWORDS = ("x1", "p1s", "p1p", "a1", "voron", "ratrig", "creality k1", "vzbot")
_DELTA_KEYWORDS = ("flying bear", "kossel", "delta", "flsun")
_IDEX_KEYWORDS = ("idex", "snapmaker j1", "copymaker")


def _infer_kinematics(printer_model: str | None) -> Kinematics:
    """Orca doesn't declare kinematics in profiles; infer from model name.

    Better here than guessing at GRI-R-compute time because the consumer
    shouldn't have to know Bambu X1 is CoreXY.
    """
    if not printer_model:
        return Kinematics.UNKNOWN
    name = printer_model.lower()
    if any(k in name for k in _IDEX_KEYWORDS):
        return Kinematics.IDEX
    if any(k in name for k in _DELTA_KEYWORDS):
        return Kinematics.DELTA
    if any(k in name for k in _COREXY_KEYWORDS):
        return Kinematics.COREXY
    # Default Cartesian covers Ender-style bedslingers and Prusa MK-series.
    return Kinematics.CARTESIAN


_ENCLOSED_MODEL_HINTS = ("x1", "p1s", "x1e", "voron")


def _infer_enclosure(printer_model: str | None) -> bool:
    if not printer_model:
        return False
    name = printer_model.lower()
    return any(h in name for h in _ENCLOSED_MODEL_HINTS)


def _nozzle_type_from_orca(data: dict[str, Any]) -> NozzleType:
    """Infer nozzle geometry class from Orca's `nozzle_volume_type` or
    `extruder_variant_list`. These carry strings like "Standard", "High
    Flow", "Volcano", etc. "nozzle_type" itself is the *material*
    (brass / hardened steel) in Orca's vocabulary, so we avoid it.
    """
    for key in ("nozzle_volume_type", "extruder_variant_list"):
        raw = _first(data.get(key))
        if not isinstance(raw, str):
            continue
        s = raw.lower()
        if "volcano" in s:
            return NozzleType.VOLCANO
        if "cht" in s:
            return NozzleType.CHT
        if "chc" in s:
            return NozzleType.CHC
        if "high flow" in s or "high_flow" in s or "bigtraffic" in s or "big traffic" in s:
            return NozzleType.HIGH_FLOW
        if "standard" in s:
            return NozzleType.STANDARD
    return NozzleType.UNKNOWN


def _retraction_type_from_orca(data: dict[str, Any]) -> RetractionType:
    """Orca stores retraction topology in `extruder_type` or in
    `extruder_variant_list` (comma-separated). Bambu X1C uses
    `extruder_variant_list: ["Direct Drive Standard,Direct Drive High Flow"]`.
    """
    for key in ("extruder_type", "extruder_variant_list"):
        raw = _first(data.get(key))
        if not isinstance(raw, str):
            continue
        s = raw.lower()
        if "bowden" in s:
            return RetractionType.BOWDEN
        if "direct" in s:
            return RetractionType.DIRECT_DRIVE
    return RetractionType.UNKNOWN


def _axis_speeds(
    data: dict[str, Any],
    *,
    x_key: str,
    y_key: str,
    z_key: str,
    e_key: str,
) -> AxisSpeeds | None:
    values = {
        "x": _to_float(data.get(x_key)),
        "y": _to_float(data.get(y_key)),
        "z": _to_float(data.get(z_key)),
        "e": _to_float(data.get(e_key)),
    }
    if not any(v for v in values.values()):
        return None
    return AxisSpeeds(**values)


# ── Translators ───────────────────────────────────────────────────────


def translate_printer(resolved: ResolvedProfile) -> CanonicalPrinter:
    data = resolved.data
    vendor = resolved.vendor
    printer_model = _to_str(data.get("printer_model")) or resolved.name
    printer_variant = _to_str(data.get("printer_variant"))
    # Composite ID: vendor + model + variant. Matches how Orca files itself
    # (one .json per nozzle variant) so round-tripping to/from Orca UI stays
    # natural.
    profile_id = (
        f"{vendor}/{printer_model}_{printer_variant}"
        if printer_variant
        else f"{vendor}/{printer_model}"
    )
    is_resin = _to_str(data.get("printer_technology")) == "SLA"

    build_volume = _parse_printable_area(
        data.get("printable_area"),
        data.get("printable_height"),
    )
    if build_volume is None:
        # Defensive fallback: synthesise a placeholder volume so the
        # model stays constructable. Real-world profiles always declare
        # these; failing the whole parse for one missing field would
        # break bulk loads for no consumer gain.
        build_volume = BuildVolumeMm(x=1, y=1, z=1)

    feed = _axis_speeds(
        data,
        x_key="machine_max_speed_x",
        y_key="machine_max_speed_y",
        z_key="machine_max_speed_z",
        e_key="machine_max_speed_e",
    )
    accel = _axis_speeds(
        data,
        x_key="machine_max_acceleration_x",
        y_key="machine_max_acceleration_y",
        z_key="machine_max_acceleration_z",
        e_key="machine_max_acceleration_e",
    )

    source = SourceMetadata(
        slicer="orca",
        vendor=vendor,
        source_id=resolved.name,
        inherits_chain=resolved.inherits_chain,
    )

    raw_data = _strip_metadata(data)

    if is_resin:
        return CanonicalPrinter(
            id=profile_id,
            name=resolved.name,
            vendor=vendor,
            technology=PrinterTechnology.RESIN_MSLA,
            build_volume_mm=build_volume,
            pixel_size_mm=_to_float(data.get("display_pixel_size")),
            z_step_mm=_to_float(data.get("z_step")),
            source=source,
            raw_data=raw_data,
        )

    # Motion tuning (Klipper-style). Orca carries printer-level PA on
    # some vendor profiles (`pressure_advance`), but the authoritative
    # value usually lives on the process. Populate only when a printer
    # default exists; consumers should prefer CanonicalProcess.pressure_advance_k.
    pa_k = _to_float(data.get("pressure_advance"))
    max_jerk = _to_float(data.get("machine_max_jerk_x"))
    # Some Orca profiles also store a jerk-y; take the max so audits read
    # the outer envelope the planner can emit, not the smaller axis.
    jerk_y = _to_float(data.get("machine_max_jerk_y"))
    if max_jerk is not None and jerk_y is not None:
        max_jerk = max(max_jerk, jerk_y)
    elif jerk_y is not None:
        max_jerk = jerk_y
    junction_dev = _to_float(data.get("default_junction_deviation"))

    return CanonicalPrinter(
        id=profile_id,
        name=resolved.name,
        vendor=vendor,
        technology=PrinterTechnology.FDM,
        build_volume_mm=build_volume,
        nozzle_diameter_mm=_to_float(data.get("nozzle_diameter")),
        nozzle_type=_nozzle_type_from_orca(data),
        firmware=_to_str(data.get("gcode_flavor")),
        kinematics=_infer_kinematics(printer_model),
        enclosure=_infer_enclosure(printer_model),
        retraction_type=_retraction_type_from_orca(data),
        max_feedrate_mm_s=feed,
        max_accel_mm_s2=accel,
        pressure_advance_k=pa_k if pa_k and pa_k > 0 else None,
        max_jerk_mm_s=max_jerk if max_jerk and max_jerk > 0 else None,
        junction_deviation_mm=junction_dev if junction_dev and junction_dev > 0 else None,
        start_gcode=_to_str(data.get("machine_start_gcode")),
        end_gcode=_to_str(data.get("machine_end_gcode")),
        source=source,
        raw_data=raw_data,
    )


def translate_filament(resolved: ResolvedProfile) -> CanonicalFilament:
    data = resolved.data
    vendor = resolved.vendor
    raw_category = _to_str(data.get("filament_type"))
    category = _normalise_filament_category(raw_category)

    nozzle_normal = _to_float(data.get("nozzle_temperature"))
    nozzle_first = _to_float(data.get("nozzle_temperature_initial_layer")) or nozzle_normal
    nozzle_range_min = _to_float(data.get("nozzle_temperature_range_low"))
    nozzle_range_max = _to_float(data.get("nozzle_temperature_range_high"))
    nozzle_temps: NozzleTemps | None = None
    if nozzle_normal and nozzle_first:
        nozzle_temps = NozzleTemps(
            normal=nozzle_normal,
            first_layer=nozzle_first,
            range_min=nozzle_range_min,
            range_max=nozzle_range_max,
        )

    # Orca exposes bed temps per bed surface (cool/hot/textured/engineering).
    # We pick `hot_plate_temp` as the canonical reference; consumers that
    # care about surface-specific temps can reach into raw Orca data —
    # that's why `source.inherits_chain` is preserved.
    bed_normal = (
        _to_float(data.get("hot_plate_temp"))
        or _to_float(data.get("bed_temperature"))
    )
    bed_first = (
        _to_float(data.get("hot_plate_temp_initial_layer"))
        or _to_float(data.get("bed_temperature_initial_layer"))
        or bed_normal
    )
    bed_temps: BedTemps | None = None
    if bed_normal is not None and bed_first is not None:
        bed_temps = BedTemps(normal=bed_normal, first_layer=bed_first)

    cooling: CoolingSettings | None = None
    fan_min = _to_int(data.get("fan_min_speed"))
    fan_max = _to_int(data.get("fan_max_speed"))
    if fan_min is not None and fan_max is not None:
        cooling = CoolingSettings(
            fan_min_pct=max(0, min(100, fan_min)),
            fan_max_pct=max(0, min(100, fan_max)),
            fan_cooling_layer_time_s=(
                _to_float(data.get("fan_cooling_layer_time"))
                or _to_float(data.get("slow_down_layer_time"))
                or 0.0
            ),
            disable_fan_first_layers=(
                _to_int(data.get("close_fan_the_first_x_layers"))
                or _to_int(data.get("disable_fan_first_layers"))
                or 0
            ),
            overhang_fan_speed_pct=_to_int(data.get("overhang_fan_speed")),
            overhang_fan_threshold_pct=_to_int(data.get("overhang_fan_threshold")),
        )

    retraction: RetractionSettings | None = None
    retract_len = _to_float(data.get("filament_retraction_length"))
    retract_speed = _to_float(data.get("filament_retraction_speed"))
    if retract_len is not None and retract_speed is not None and retract_speed > 0:
        retraction = RetractionSettings(
            length_mm=retract_len,
            speed_mm_s=retract_speed,
            deretraction_speed_mm_s=_to_float(data.get("filament_deretraction_speed")),
            z_hop_mm=_to_float(data.get("filament_z_hop")),
        )

    filament_vendor = _to_str(data.get("filament_vendor")) or vendor
    source = SourceMetadata(
        slicer="orca",
        vendor=vendor,
        source_id=resolved.name,
        inherits_chain=resolved.inherits_chain,
    )

    return CanonicalFilament(
        id=f"{vendor}/{resolved.name}",
        name=resolved.name,
        vendor=filament_vendor,
        category=category,
        raw_category=raw_category,
        nozzle_temp_c=nozzle_temps,
        bed_temp_c=bed_temps,
        flow_ratio=_to_float(data.get("filament_flow_ratio")),
        density_g_cm3=_to_float(data.get("filament_density")),
        shrinkage_pct=_to_float(data.get("filament_shrink")),
        bridge_flow=_to_float(data.get("bridge_flow")),
        max_volumetric_speed_mm3_s=_to_float(data.get("filament_max_volumetric_speed")),
        filament_diameter_mm=_to_float(data.get("filament_diameter")),
        cooling=cooling,
        retraction=retraction,
        source=source,
        raw_data=_strip_metadata(data),
    )


def translate_process(resolved: ResolvedProfile) -> CanonicalProcess:
    data = resolved.data
    layer_height = _to_float(data.get("layer_height"))
    first_layer = _to_float(data.get("initial_layer_print_height")) or layer_height
    if not layer_height or not first_layer:
        # Process profiles can only be constructed if they carry layer
        # geometry. A profile missing both is malformed; raising here is
        # the right move so bulk load surfaces it instead of silently
        # producing a junk value.
        raise ValueError(
            f"process profile {resolved.name!r} missing layer_height"
        )

    speeds = ProcessSpeeds(
        perimeter=_to_float(data.get("inner_wall_speed")),
        external_perimeter=_to_float(data.get("outer_wall_speed")),
        small_perimeter=_to_float(data.get("small_perimeter_speed")),
        infill=_to_float(data.get("sparse_infill_speed")),
        solid_infill=_to_float(data.get("internal_solid_infill_speed")),
        top_solid_infill=_to_float(data.get("top_surface_speed")),
        gap_infill=_to_float(data.get("gap_infill_speed")),
        bridge=_to_float(data.get("bridge_speed")),
        support=_to_float(data.get("support_speed")),
        travel=_to_float(data.get("travel_speed")),
        first_layer=_to_float(data.get("initial_layer_speed")),
        first_layer_infill=_to_float(data.get("initial_layer_infill_speed")),
    )

    infill_pattern, raw_pattern = _normalise_infill(_to_str(data.get("sparse_infill_pattern")))

    support: SupportSettings | None = None
    support_enabled_raw = data.get("enable_support")
    if support_enabled_raw is not None:
        support = SupportSettings(
            enabled=_to_bool(support_enabled_raw),
            threshold_angle_deg=_to_float(data.get("support_threshold_angle")),
            pattern=_normalise_support_type(_to_str(data.get("support_type"))),
            z_distance_mm=_to_float(data.get("support_top_z_distance")),
            interface_layers=_to_int(data.get("support_interface_top_layers")),
            on_build_plate_only=_to_bool(data.get("support_on_build_plate_only")),
        )

    adhesion = AdhesionSettings(
        skirt_loops=_to_int(data.get("skirt_loops")),
        skirt_distance_mm=_to_float(data.get("skirt_distance")),
        brim_width_mm=_to_float(data.get("brim_width")),
        raft_layers=_to_int(data.get("raft_layers")),
    )

    # Process-level PA override. Orca exposes this through
    # `pressure_advance` at the process level for recipes that tune PA
    # per material / quality — wins over the printer default when present.
    pa_k = _to_float(data.get("pressure_advance"))

    return CanonicalProcess(
        id=f"{resolved.vendor}/{resolved.name}",
        name=resolved.name,
        compatible_printers=_to_str_list(data.get("compatible_printers")),
        compatible_filaments=_to_str_list(data.get("compatible_filaments")),
        layer_height_mm=layer_height,
        first_layer_height_mm=first_layer,
        wall_count=_to_int(data.get("wall_loops")),
        top_shell_layers=_to_int(data.get("top_shell_layers")),
        bottom_shell_layers=_to_int(data.get("bottom_shell_layers")),
        top_shell_thickness_mm=_to_float(data.get("top_shell_thickness")),
        bottom_shell_thickness_mm=_to_float(data.get("bottom_shell_thickness")),
        infill_pct=_to_int(data.get("sparse_infill_density")),
        infill_pattern=infill_pattern,
        raw_infill_pattern=raw_pattern if infill_pattern is InfillPattern.OTHER else None,
        speed_mm_s=speeds,
        default_acceleration_mm_s2=_to_float(data.get("default_acceleration")),
        pressure_advance_k=pa_k if pa_k and pa_k > 0 else None,
        support=support,
        adhesion=adhesion,
        seam_position=_normalise_seam(_to_str(data.get("seam_position"))),
        source=SourceMetadata(
            slicer="orca",
            vendor=resolved.vendor,
            source_id=resolved.name,
            inherits_chain=resolved.inherits_chain,
        ),
        raw_data=_strip_metadata(data),
    )


# ── High-level entry point ────────────────────────────────────────────


def load_orca(profiles_root: str | Path) -> ProfileBundle:
    """Index an OrcaSlicer `resources/profiles/` tree and translate every
    concrete profile into its canonical form.

    Abstract / intermediate profiles (those with `instantiation: false`)
    are kept in the index for inheritance resolution but don't appear in
    the returned bundle — they're implementation detail, not something
    a user would pick.
    """
    index = index_directory(profiles_root)
    bundle = ProfileBundle(slicer="orca")

    def _maybe_add(
        raw_map: dict[str, Any],
        bundle_map: dict[str, Any],
        translate: Any,
    ) -> None:
        for raw in raw_map.values():
            if not raw.instantiation:
                continue  # abstract base, not user-selectable
            resolved = resolve(raw, index)
            try:
                canonical = translate(resolved)
            except (ValueError, KeyError):
                # One bad profile doesn't sink the whole bundle — log-
                # worthy but not fatal. Upstream caller can inspect
                # raw_map if they need to know what was dropped.
                continue
            bundle_map[canonical.id] = canonical

    _maybe_add(index.printers, bundle.printers, translate_printer)
    _maybe_add(index.filaments, bundle.filaments, translate_filament)
    _maybe_add(index.processes, bundle.processes, translate_process)

    return bundle
