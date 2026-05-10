"""PrusaSlicer profile translator.

Prusa distributes profiles as a single multi-section INI per vendor
(`PrusaResearch.ini`, `Creality.ini`, ...) rather than Orca's
one-file-per-profile JSON. The section header `[<type>:<name>]` carries
the profile identity; values are raw strings — no list wrapping, no
string-coded numbers once you're past the parent reference list.

Multi-parent inheritance (`inherits = *0.20mm*; *MK4*`) is resolved by
`inherit.resolve` via the shared `inherits_all` walk; this translator
only needs to deal with field-level mapping once the effective values
are assembled.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slicer_profile_bridge.heuristics import infer_filament_enclosure_required
from slicer_profile_bridge.inherit import ResolvedProfile, resolve
from slicer_profile_bridge.loader import (
    RawProfileIndex,
    _normalise_type,  # noqa: PLC2701 — package-local reuse
    index_ini_file,
)
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
    PeelCycle,
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

# Metadata keys we strip from the raw_data pass-through. Different from
# Orca's set because Prusa exposes extra bookkeeping fields in its INI
# (setting_id, renamed_from, compatible_printers_condition — the last
# one is an expression, not an identifier list, and shouldn't be mixed
# into consumer queries).
_METADATA_KEYS: frozenset[str] = frozenset({
    "type", "name", "inherits", "from", "setting_id", "instantiation",
    "filament_id", "description", "renamed_from",
})


def _strip_metadata(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in _METADATA_KEYS}


# ── Primitive coercion ────────────────────────────────────────────────
# Prusa INI values are raw strings. Multi-extruder fields come as
# comma-separated `"0.4,0.4"`; percentage fields as `"70%"`. Helpers
# below handle both and return Pythonic types.


def _first_csv(value: Any) -> str | None:
    """Unwrap `"0.4,0.4"` → `"0.4"` so single-extruder translation reads
    the same path as multi-extruder profiles.
    """
    if value is None:
        return None
    if isinstance(value, str):
        first = value.split(",", 1)[0].strip()
        return first or None
    return str(value).strip() or None


def _to_float(value: Any) -> float | None:
    s = _first_csv(value)
    if s is None:
        return None
    s = s.strip().rstrip("%").strip()
    if not s or s.lower() in ("nil", "null", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _to_bool(value: Any) -> bool:
    """Prusa encodes flags as `"1"` / `"0"` scalars."""
    s = _first_csv(value)
    if s is None:
        return False
    return s.strip().lower() in ("1", "true", "yes", "on")


def _to_str(value: Any) -> str | None:
    s = _first_csv(value)
    return s if s else None


def _to_str_verbatim(value: Any) -> str | None:
    """Like `_to_str` but preserves embedded commas. Needed for
    multi-line g-code fields (`start_gcode`, `end_gcode`): Prusa
    encodes nested template expressions — `{if x < max, y}` — that
    the CSV-first helper would truncate at the first comma.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value).strip() or None
    return value.strip() or None


def _to_str_semi_list(value: Any) -> list[str]:
    """Prusa uses `;`-separated lists for `compatible_printers` and the
    like. `inherits` already gets split by the loader — this is for the
    other fields.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.split(";") if x.strip()]
    return []


# ── Enum mapping (Prusa-specific strings) ─────────────────────────────

_FILAMENT_CATEGORY_MAP = {
    "pla": FilamentCategory.PLA,
    "pla+": FilamentCategory.PLA,
    "pla-cf": FilamentCategory.PLA_CF,
    "pla cf": FilamentCategory.PLA_CF,
    "petg": FilamentCategory.PETG,
    "pet": FilamentCategory.PETG,
    "petg-cf": FilamentCategory.PET_CF,
    "petg cf": FilamentCategory.PET_CF,
    "pet-cf": FilamentCategory.PET_CF,
    "abs": FilamentCategory.ABS,
    "abs-gf": FilamentCategory.ABS_GF,
    "abs gf": FilamentCategory.ABS_GF,
    "asa": FilamentCategory.ASA,
    "flex": FilamentCategory.TPU,
    "tpu": FilamentCategory.TPU,
    "pa": FilamentCategory.PA,
    "nylon": FilamentCategory.PA,
    "pa6": FilamentCategory.PA,
    "pa12": FilamentCategory.PA,
    # Fibre-reinforced polyamides -- see orca.py map for family notes.
    "pa-cf": FilamentCategory.PA_CF,
    "pa cf": FilamentCategory.PA_CF,
    "pa6-cf": FilamentCategory.PA_CF,
    "pa12-cf": FilamentCategory.PA_CF,
    "paht": FilamentCategory.PA_CF,
    "paht-cf": FilamentCategory.PA_CF,
    "pa-gf": FilamentCategory.PA_GF,
    "pa gf": FilamentCategory.PA_GF,
    # Polyphthalamide family
    "ppa": FilamentCategory.PPA,
    "ppa-cf": FilamentCategory.PPA_CF,
    "ppa cf": FilamentCategory.PPA_CF,
    "ppa-gf": FilamentCategory.PPA_GF,
    "pc": FilamentCategory.PC,
    "hips": FilamentCategory.HIPS,
    "pva": FilamentCategory.PVA,
    "pvb": FilamentCategory.PVB,
    "bvoh": FilamentCategory.BVOH,
}


def _normalise_filament_category(raw: str | None) -> FilamentCategory:
    if not raw:
        return FilamentCategory.OTHER
    key = raw.strip().lower()
    if key in _FILAMENT_CATEGORY_MAP:
        return _FILAMENT_CATEGORY_MAP[key]
    # Longest-prefix match so "pa-cf" wins over "pa" when the raw is
    # "PA-CF Pro". Separator can be space, dash, or underscore.
    sorted_keys = sorted(_FILAMENT_CATEGORY_MAP.keys(), key=len, reverse=True)
    for prefix in sorted_keys:
        for sep in (" ", "-", "_"):
            if key.startswith(prefix + sep):
                return _FILAMENT_CATEGORY_MAP[prefix]
    return FilamentCategory.OTHER


_INFILL_PATTERN_MAP = {
    "rectilinear": InfillPattern.LINE,
    "monotonic": InfillPattern.LINE,
    "monotoniclines": InfillPattern.LINE,
    "grid": InfillPattern.GRID,
    "triangles": InfillPattern.TRIANGLES,
    "stars": InfillPattern.OTHER,
    "cubic": InfillPattern.CUBIC,
    "line": InfillPattern.LINE,
    "concentric": InfillPattern.CONCENTRIC,
    "honeycomb": InfillPattern.HONEYCOMB,
    "3dhoneycomb": InfillPattern.HONEYCOMB,
    "gyroid": InfillPattern.GYROID,
    "hilbertcurve": InfillPattern.HILBERT,
    "archimedeanchords": InfillPattern.OTHER,
    "octagramspiral": InfillPattern.OTHER,
    "adaptivecubic": InfillPattern.ADAPTIVE_CUBIC,
    "supportcubic": InfillPattern.ADAPTIVE_CUBIC,
    "lightning": InfillPattern.LIGHTNING,
}


def _normalise_infill(raw: str | None) -> tuple[InfillPattern, str | None]:
    if not raw:
        return InfillPattern.OTHER, None
    key = raw.strip().lower().replace("_", "").replace(" ", "")
    return _INFILL_PATTERN_MAP.get(key, InfillPattern.OTHER), raw


_SUPPORT_STYLE_MAP = {
    "grid": SupportPattern.GRID,
    "snug": SupportPattern.SNUG,
    "organic": SupportPattern.ORGANIC,
    "tree": SupportPattern.TREE,
}


def _normalise_support_pattern(style: str | None, tree_flag: str | None) -> SupportPattern:
    """Prusa encodes support topology two ways:
      * `support_material_style = grid | snug | organic`
      * `support_material = 1` plus `support_tree_*` fields for trees
    """
    if tree_flag and _to_bool(tree_flag):
        return SupportPattern.TREE
    if not style:
        return SupportPattern.UNKNOWN
    return _SUPPORT_STYLE_MAP.get(style.strip().lower(), SupportPattern.UNKNOWN)


_SEAM_MAP = {
    "nearest": SeamPosition.NEAREST,
    "aligned": SeamPosition.ALIGNED,
    "rear": SeamPosition.BACK,
    "back": SeamPosition.BACK,
    "random": SeamPosition.RANDOM,
}


def _normalise_seam(raw: str | None) -> SeamPosition:
    if not raw:
        return SeamPosition.UNKNOWN
    return _SEAM_MAP.get(raw.strip().lower(), SeamPosition.UNKNOWN)


# ── Printer helpers ───────────────────────────────────────────────────


_BED_POINT_RE = re.compile(r"^\s*([\d.]+)\s*x\s*([\d.]+)\s*$")


def _parse_bed_shape(shape: Any, height: Any) -> BuildVolumeMm | None:
    """Prusa's `bed_shape` is a comma-separated list of `x` × `y`
    points, each on one item: `"0x0,250x0,250x210,0x210"`. We take the
    axis-aligned bounding box as the canonical build volume.
    """
    if not isinstance(shape, str) or not shape.strip():
        return None
    xs: list[float] = []
    ys: list[float] = []
    for piece in shape.split(","):
        m = _BED_POINT_RE.match(piece)
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


_COREXY_HINTS = ("core one", "xl", "voron", "ratrig")
_DELTA_HINTS = ("delta", "kossel")
_IDEX_HINTS = ("idex", "xl ", "copymaker")


def _infer_kinematics(name: str | None) -> Kinematics:
    if not name:
        return Kinematics.UNKNOWN
    n = name.lower()
    if any(h in n for h in _IDEX_HINTS):
        return Kinematics.IDEX
    if any(h in n for h in _DELTA_HINTS):
        return Kinematics.DELTA
    if any(h in n for h in _COREXY_HINTS):
        return Kinematics.COREXY
    return Kinematics.CARTESIAN


_ENCLOSED_HINTS = ("core one", "voron")


def _infer_enclosure(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(h in n for h in _ENCLOSED_HINTS)


# ── SLA / resin branch helpers ────────────────────────────────────────
#
# PrusaSlicer SLA profile INI uses `printer_technology = SLA` (vs the
# implicit FFF default for FDM). UVtools bundles ~150 SLA profiles in
# its `Assets/PrusaSlicer/printer/*.ini` — the same shape PrusaSlicer
# itself ships, with `inherits = Original Prusa SL1` chains and a
# `printer_notes` blob carrying vendor-specific peel-cycle values
# inside a `START_CUSTOM_VALUES` / `END_CUSTOM_VALUES` block.

_CUSTOM_VALUE_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)_([\-0-9.]+)")


def _detect_technology(data: dict[str, Any]) -> PrinterTechnology:
    """Read `printer_technology` from the resolved profile.

    PrusaSlicer FDM profiles often omit the field (defaults to FFF) so
    we treat anything that isn't an explicit `SLA` literal as FDM.
    Future powder-bed translators (SLS / MJF) would extend this map.
    """
    raw = (data.get("printer_technology") or "").strip().upper()
    if raw == "SLA":
        return PrinterTechnology.RESIN_MSLA
    return PrinterTechnology.FDM


def _parse_custom_values_block(notes: str | None) -> dict[str, float]:
    """Pull the START_CUSTOM_VALUES … END_CUSTOM_VALUES block out of
    `printer_notes`. PrusaSlicer's INI escapes newlines as `\\n`, so
    accept both the literal and the escaped form.
    """
    if not notes:
        return {}
    text = notes.replace("\\n", "\n")
    start = text.find("START_CUSTOM_VALUES")
    end = text.find("END_CUSTOM_VALUES")
    if start < 0 or end < 0 or end <= start:
        return {}
    block = text[start + len("START_CUSTOM_VALUES") : end]
    out: dict[str, float] = {}
    for m in _CUSTOM_VALUE_RE.finditer(block):
        try:
            out[m.group(1)] = float(m.group(2))
        except ValueError:
            continue
    return out


def _parse_sla_peel_cycle(data: dict[str, Any]) -> PeelCycle | None:
    """Build the resin peel-cycle block from a PrusaSlicer SLA profile.

    Sources, in priority order:
      1. `printer_notes` `START_CUSTOM_VALUES` block (BottomLiftHeight,
         LiftHeight, BottomLiftSpeed, LiftSpeed, RetractSpeed,
         BottomLightPWM, LightPWM, WaitTimeBeforeCure) — Anycubic /
         Elegoo / Phrozen vendor convention.
      2. Top-level INI keys (`fast_tilt_time`, `slow_tilt_time`) —
         Original Prusa SL1 convention for tilt-bed printers.
    """
    custom = _parse_custom_values_block(_to_str_verbatim(data.get("printer_notes")))

    def _f(key: str) -> float | None:
        v = custom.get(key)
        return v if v and v > 0 else None

    def _i(key: str) -> int | None:
        v = custom.get(key)
        if v is None:
            return None
        i = int(v)
        if 0 <= i <= 255:
            return i
        return None

    fields: dict[str, Any] = {
        "lift_height_mm":              _f("LiftHeight"),
        "lift_speed_mm_min":           _f("LiftSpeed"),
        "retract_speed_mm_min":        _f("RetractSpeed"),
        "bottom_lift_height_mm":       _f("BottomLiftHeight"),
        "bottom_lift_speed_mm_min":    _f("BottomLiftSpeed"),
        "bottom_retract_speed_mm_min": _f("BottomRetractSpeed"),
        "light_pwm":                   _i("LightPWM"),
        "bottom_light_pwm":            _i("BottomLightPWM"),
        "fast_tilt_time_s":            _to_float(data.get("fast_tilt_time")),
        "slow_tilt_time_s":            _to_float(data.get("slow_tilt_time")),
        "wait_time_before_cure_s":     _f("WaitTimeBeforeCure"),
    }
    if not any(v is not None for v in fields.values()):
        return None
    return PeelCycle(**{
        k: v for k, v in fields.items()
        if v is not None and (not isinstance(v, (int, float)) or v > 0 or k.endswith("_pwm"))
    })


def _build_sla_printer(
    resolved: ResolvedProfile,
    data: dict[str, Any],
    source: SourceMetadata,
) -> CanonicalPrinter:
    """Assemble a CanonicalPrinter from a PrusaSlicer SLA INI profile.

    Maps the LCD geometry fields to the canonical resin block, parses
    peel-cycle from `printer_notes`, skips every FDM-only field
    (nozzle, retraction, gcode_flavor, axis feedrates).
    """
    width = _to_float(data.get("display_width"))
    height = _to_float(data.get("display_height"))
    z_height = _to_float(data.get("max_print_height"))
    if width is None or height is None or not z_height or z_height <= 0:
        # Geometry missing — keep the printer in catalog with a
        # sentinel so the consumer surfaces it rather than silently
        # dropping. Strict schema needs positive values, so fall back
        # to 1mm — a downstream "did this profile resolve?" check
        # catches the all-1 case.
        build_volume = BuildVolumeMm(
            x=width if width and width > 0 else 1,
            y=height if height and height > 0 else 1,
            z=z_height if z_height and z_height > 0 else 1,
        )
    else:
        build_volume = BuildVolumeMm(x=width, y=height, z=z_height)

    px_x = _to_int(data.get("display_pixels_x"))
    px_y = _to_int(data.get("display_pixels_y"))
    lcd_resolution: tuple[int, int] | None = None
    pixel_size: float | None = None
    if px_x and px_y and px_x > 0 and px_y > 0:
        lcd_resolution = (px_x, px_y)
        if width and width > 0:
            pixel_size = width / px_x

    return CanonicalPrinter(
        id=f"{resolved.vendor}/{resolved.name}",
        name=resolved.name,
        vendor=resolved.vendor,
        technology=PrinterTechnology.RESIN_MSLA,
        build_volume_mm=build_volume,
        firmware=None,
        kinematics=Kinematics.UNKNOWN,
        enclosure=False,
        pixel_size_mm=pixel_size,
        lcd_resolution_px=lcd_resolution,
        peel_cycle=_parse_sla_peel_cycle(data),
        source=source,
        raw_data=_strip_metadata(data),
    )


# ── Translators ───────────────────────────────────────────────────────


def translate_printer(resolved: ResolvedProfile) -> CanonicalPrinter:
    data = resolved.data

    # Branch on the technology field. SLA profiles have a separate
    # geometry shape (LCD area, not nozzle / extruder) and entirely
    # different motion model (peel cycle, no retraction). Sharing a
    # single FDM-shaped fall-through silently dropped 152 resin
    # printers from the canonical catalog; SLA path now lifts them.
    tech = _detect_technology(data)
    source = SourceMetadata(
        slicer="prusa",
        vendor=resolved.vendor,
        source_id=resolved.name,
        inherits_chain=resolved.inherits_chain,
    )
    if tech == PrinterTechnology.RESIN_MSLA:
        return _build_sla_printer(resolved, data, source)

    # FDM path (existing behaviour) ─────────────────────────────────────
    build_volume = _parse_bed_shape(
        data.get("bed_shape"),
        data.get("max_print_height"),
    )
    if build_volume is None:
        build_volume = BuildVolumeMm(x=1, y=1, z=1)

    axis_feed = AxisSpeeds(
        x=_to_float(data.get("machine_max_feedrate_x")),
        y=_to_float(data.get("machine_max_feedrate_y")),
        z=_to_float(data.get("machine_max_feedrate_z")),
        e=_to_float(data.get("machine_max_feedrate_e")),
    )
    if not any(v for v in axis_feed.model_dump().values()):
        axis_feed = None  # type: ignore[assignment]
    axis_accel = AxisSpeeds(
        x=_to_float(data.get("machine_max_acceleration_x")),
        y=_to_float(data.get("machine_max_acceleration_y")),
        z=_to_float(data.get("machine_max_acceleration_z")),
        e=_to_float(data.get("machine_max_acceleration_e")),
    )
    if not any(v for v in axis_accel.model_dump().values()):
        axis_accel = None  # type: ignore[assignment]

    # Prusa's extruder direct-drive vs bowden isn't declared directly;
    # the convention is that MK3 / MK4 family = direct drive, older MK2
    # (rare today) = Bowden. Default to direct drive for Prusa-branded
    # printers, unknown for anyone else.
    retraction_type = (
        RetractionType.DIRECT_DRIVE
        if "prusa" in resolved.name.lower()
        else RetractionType.UNKNOWN
    )

    # `source` is built above the SLA branch (line ~485) so we don't
    # rebuild it here.

    # Printer model for kinematics/enclosure inference — prefer the
    # dedicated field when present, fall back to the profile name.
    printer_model = _to_str(data.get("printer_model")) or resolved.name

    # Motion tuning. Prusa uses the older `machine_max_jerk_*` family as
    # well as `default_junction_deviation` on newer firmware profiles
    # (MK4 / XL / MINI+ post-firmware-5). Take the max of x/y jerk for
    # the outer envelope — same strategy as Orca.
    max_jerk = _to_float(data.get("machine_max_jerk_x"))
    jerk_y = _to_float(data.get("machine_max_jerk_y"))
    if max_jerk is not None and jerk_y is not None:
        max_jerk = max(max_jerk, jerk_y)
    elif jerk_y is not None:
        max_jerk = jerk_y
    junction_dev = (
        _to_float(data.get("machine_max_junction_deviation"))
        or _to_float(data.get("default_junction_deviation"))
    )

    return CanonicalPrinter(
        id=f"{resolved.vendor}/{resolved.name}",
        name=resolved.name,
        vendor=resolved.vendor,
        technology=PrinterTechnology.FDM,
        build_volume_mm=build_volume,
        nozzle_diameter_mm=_to_float(data.get("nozzle_diameter")),
        firmware=_to_str(data.get("gcode_flavor")),
        kinematics=_infer_kinematics(printer_model),
        enclosure=_infer_enclosure(printer_model),
        retraction_type=retraction_type,
        max_feedrate_mm_s=axis_feed,
        max_accel_mm_s2=axis_accel,
        max_jerk_mm_s=max_jerk if max_jerk and max_jerk > 0 else None,
        junction_deviation_mm=junction_dev if junction_dev and junction_dev > 0 else None,
        start_gcode=(
            _to_str_verbatim(data.get("start_gcode"))
            or _to_str_verbatim(data.get("machine_start_gcode"))
        ),
        end_gcode=(
            _to_str_verbatim(data.get("end_gcode"))
            or _to_str_verbatim(data.get("machine_end_gcode"))
        ),
        source=source,
        raw_data=_strip_metadata(data),
    )


def translate_filament(resolved: ResolvedProfile) -> CanonicalFilament:
    data = resolved.data
    raw_category = _to_str(data.get("filament_type"))
    category = _normalise_filament_category(raw_category)

    nozzle_normal = _to_float(data.get("temperature"))
    nozzle_first = _to_float(data.get("first_layer_temperature")) or nozzle_normal
    nozzle_temps: NozzleTemps | None = None
    if nozzle_normal and nozzle_first:
        nozzle_temps = NozzleTemps(
            normal=nozzle_normal,
            first_layer=nozzle_first,
            range_min=_to_float(data.get("filament_ramming_parameters_min_temp")),
            range_max=_to_float(data.get("filament_ramming_parameters_max_temp")),
        )

    bed_normal = _to_float(data.get("bed_temperature"))
    bed_first = _to_float(data.get("first_layer_bed_temperature")) or bed_normal
    bed_temps: BedTemps | None = None
    if bed_normal is not None and bed_first is not None:
        bed_temps = BedTemps(normal=bed_normal, first_layer=bed_first)

    cooling: CoolingSettings | None = None
    fan_min = _to_int(data.get("min_fan_speed"))
    fan_max = _to_int(data.get("max_fan_speed"))
    if fan_min is not None and fan_max is not None:
        cooling = CoolingSettings(
            fan_min_pct=max(0, min(100, fan_min)),
            fan_max_pct=max(0, min(100, fan_max)),
            fan_cooling_layer_time_s=(
                _to_float(data.get("fan_below_layer_time"))
                or _to_float(data.get("slowdown_below_layer_time"))
                or 0.0
            ),
            disable_fan_first_layers=_to_int(data.get("disable_fan_first_layers")) or 0,
            overhang_fan_speed_pct=_to_int(data.get("bridge_fan_speed")),
            overhang_fan_threshold_pct=None,
            bridge_fan_speed_pct=_to_int(data.get("bridge_fan_speed")),
        )

    retraction: RetractionSettings | None = None
    retract_len = _to_float(data.get("filament_retract_length"))
    retract_speed = _to_float(data.get("filament_retract_speed"))
    if retract_len is not None and retract_speed is not None and retract_speed > 0:
        retraction = RetractionSettings(
            length_mm=retract_len,
            speed_mm_s=retract_speed,
            deretraction_speed_mm_s=_to_float(data.get("filament_deretract_speed")),
            z_hop_mm=_to_float(data.get("filament_retract_lift")),
        )

    source = SourceMetadata(
        slicer="prusa",
        vendor=resolved.vendor,
        source_id=resolved.name,
        inherits_chain=resolved.inherits_chain,
    )

    return CanonicalFilament(
        id=f"{resolved.vendor}/{resolved.name}",
        name=resolved.name,
        vendor=_to_str(data.get("filament_vendor")) or resolved.vendor,
        category=category,
        raw_category=raw_category,
        nozzle_temp_c=nozzle_temps,
        bed_temp_c=bed_temps,
        flow_ratio=_to_float(data.get("extrusion_multiplier")),
        density_g_cm3=_to_float(data.get("filament_density")),
        shrinkage_pct=None,    # Prusa doesn't carry a first-class shrinkage field
        bridge_flow=_to_float(data.get("bridge_flow_ratio")),
        max_volumetric_speed_mm3_s=_to_float(data.get("filament_max_volumetric_speed")),
        filament_diameter_mm=_to_float(data.get("filament_diameter")),
        cooling=cooling,
        retraction=retraction,
        enclosure_required=infer_filament_enclosure_required(
            category=category,
            bed_temp_normal_c=bed_normal,
            name=resolved.name,
        ),
        source=source,
        raw_data=_strip_metadata(data),
    )


def translate_process(resolved: ResolvedProfile) -> CanonicalProcess:
    data = resolved.data
    layer_height = _to_float(data.get("layer_height"))
    first_layer = (
        _to_float(data.get("first_layer_height"))
        or _to_float(data.get("initial_layer_print_height"))
        or layer_height
    )
    if not layer_height or not first_layer:
        raise ValueError(
            f"process profile {resolved.name!r} missing layer_height"
        )

    speeds = ProcessSpeeds(
        perimeter=_to_float(data.get("perimeter_speed")),
        external_perimeter=_to_float(data.get("external_perimeter_speed")),
        small_perimeter=_to_float(data.get("small_perimeter_speed")),
        infill=_to_float(data.get("infill_speed")),
        solid_infill=_to_float(data.get("solid_infill_speed")),
        top_solid_infill=_to_float(data.get("top_solid_infill_speed")),
        gap_infill=_to_float(data.get("gap_fill_speed")),
        bridge=_to_float(data.get("bridge_speed")),
        support=_to_float(data.get("support_material_speed")),
        travel=_to_float(data.get("travel_speed")),
        first_layer=_to_float(data.get("first_layer_speed")),
        first_layer_infill=_to_float(data.get("first_layer_infill_speed")),
    )

    infill_pattern, raw_pattern = _normalise_infill(_to_str(data.get("fill_pattern")))

    support: SupportSettings | None = None
    support_flag = data.get("support_material")
    if support_flag is not None:
        support = SupportSettings(
            enabled=_to_bool(support_flag),
            threshold_angle_deg=_to_float(data.get("support_material_threshold")),
            pattern=_normalise_support_pattern(
                _to_str(data.get("support_material_style")),
                data.get("support_tree_style"),
            ),
            z_distance_mm=_to_float(data.get("support_material_contact_distance")),
            interface_layers=_to_int(data.get("support_material_interface_layers")),
            on_build_plate_only=_to_bool(data.get("support_material_buildplate_only")),
        )

    adhesion = AdhesionSettings(
        skirt_loops=_to_int(data.get("skirts")),
        skirt_distance_mm=_to_float(data.get("skirt_distance")),
        brim_width_mm=_to_float(data.get("brim_width")),
        raft_layers=_to_int(data.get("raft_layers")),
    )

    source = SourceMetadata(
        slicer="prusa",
        vendor=resolved.vendor,
        source_id=resolved.name,
        inherits_chain=resolved.inherits_chain,
    )

    # Process-level Linear Advance on Prusa. PrusaSlicer exposes LA via
    # the print preset as an extrusion-rate override; the k-factor lives
    # in the filament preset usually but some process presets carry a
    # speed ceiling. Map `linear_advance_speed` → pressure_advance_k so
    # consumers have one field name regardless of firmware family.
    pa_k = _to_float(data.get("linear_advance_speed"))

    return CanonicalProcess(
        id=f"{resolved.vendor}/{resolved.name}",
        name=resolved.name,
        compatible_printers=_to_str_semi_list(data.get("compatible_printers")),
        compatible_filaments=_to_str_semi_list(data.get("compatible_filaments")),
        layer_height_mm=layer_height,
        first_layer_height_mm=first_layer,
        wall_count=_to_int(data.get("perimeters")),
        top_shell_layers=_to_int(data.get("top_solid_layers")),
        bottom_shell_layers=_to_int(data.get("bottom_solid_layers")),
        top_shell_thickness_mm=_to_float(data.get("top_solid_min_thickness")),
        bottom_shell_thickness_mm=_to_float(data.get("bottom_solid_min_thickness")),
        infill_pct=_to_int(data.get("fill_density")),
        infill_pattern=infill_pattern,
        raw_infill_pattern=raw_pattern if infill_pattern is InfillPattern.OTHER else None,
        speed_mm_s=speeds,
        default_acceleration_mm_s2=_to_float(data.get("default_acceleration")),
        pressure_advance_k=pa_k if pa_k and pa_k > 0 else None,
        support=support,
        adhesion=adhesion,
        seam_position=_normalise_seam(_to_str(data.get("seam_position"))),
        source=source,
        raw_data=_strip_metadata(data),
    )


# ── Entry points ──────────────────────────────────────────────────────


def _is_abstract(name: str) -> bool:
    """Prusa marks base / template profiles by wrapping the name in
    asterisks (`*commonMK4*`, `*0.20mm*`, `*PLA*`). Those aren't
    user-selectable — they exist solely so concrete profiles can
    `inherits = *commonMK4*`.
    """
    return name.startswith("*") and name.endswith("*")


def _build_bundle_from_index(
    index: RawProfileIndex,
    vendor_label: str | None = None,
) -> ProfileBundle:
    bundle = ProfileBundle(slicer="prusa")

    def _maybe_add(
        raw_map: dict[str, Any],
        bundle_map: dict[str, Any],
        translate: Any,
    ) -> None:
        for raw in raw_map.values():
            if _is_abstract(raw.name):
                continue
            if vendor_label:
                raw.vendor = vendor_label  # harmonise ID namespace per vendor
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


def load_prusa_ini(
    ini_path: str | Path,
    *,
    vendor: str | None = None,
) -> ProfileBundle:
    """Parse one Prusa-style INI bundle and translate every concrete
    profile in it. `vendor` defaults to the file stem (e.g.
    `PrusaResearch.ini` → `"PrusaResearch"`).
    """
    path = Path(ini_path).expanduser().resolve()
    resolved_vendor = vendor or path.stem
    index = index_ini_file(path, vendor=resolved_vendor)
    return _build_bundle_from_index(index, vendor_label=resolved_vendor)


def load_prusa(profiles_root: str | Path) -> ProfileBundle:
    """Parse a PrusaSlicer `resources/profiles/` directory — one INI per
    vendor — and translate every concrete profile across all of them
    into a single bundle.

    If the root contains only one INI the result matches `load_prusa_ini`
    against that file; if several vendors live side by side, they are
    merged with IDs namespaced by vendor (e.g. `PrusaResearch/MK4…` vs
    `Creality/Ender3V2…`).
    """
    root = Path(profiles_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"profile root does not exist: {root}")

    merged = ProfileBundle(slicer="prusa")
    for ini in sorted(root.rglob("*.ini")):
        # Bundle-internal types the tokeniser can't otherwise filter:
        # PrusaSlicer ships `*.idx` index files too but those have
        # `.idx` extension and are skipped by the glob above.
        bundle = load_prusa_ini(ini, vendor=ini.stem)
        merged.printers.update(bundle.printers)
        merged.filaments.update(bundle.filaments)
        merged.processes.update(bundle.processes)
    return merged


# ── UVtools-style per-file SLA loader ─────────────────────────────────
#
# UVtools bundles ~150 PrusaSlicer SLA profiles in
# `Assets/PrusaSlicer/printer/<Name>.ini` (one printer per file, no
# `[printer:Name]` section header — type implied by directory). Same
# value dialect as `load_prusa_ini` consumes inside multi-section
# vendor INIs, so we lift the existing translate_* functions verbatim
# and just rebuild the index walker.

def _load_per_file_ini(path: Path) -> dict[str, Any]:
    """Read a single per-file PrusaSlicer-style INI (no section header)
    and return the flat key→value map. Comments and blank lines are
    skipped; section markers (`[printer:Name]`) are tolerated when
    present and treated as no-ops.
    """
    out: dict[str, Any] = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def load_uvtools_assets(assets_root: str | Path) -> ProfileBundle:
    """Parse a UVtools `Assets/PrusaSlicer/` directory and translate the
    bundled SLA profiles into a canonical bundle.

    Layout convention (matches PrusaSlicer source repo
    `resources/profiles/<Vendor>/printer/*.ini`):

        <root>/printer/<Name>.ini           → CanonicalPrinter (RESIN_MSLA)
        <root>/sla_print/<Name>.ini         → CanonicalProcess
        <root>/sla_material/<Name>.ini      → CanonicalFilament   (rare)

    UVtools ships `printer/` + `sla_print/` only; `sla_material/` is
    typically empty and the consumer synthesises a Generic Std Resin
    when no filament is bundled.

    The `vendor` carried on each profile is derived from the printer
    name's first token ("Anycubic", "Elegoo", "Phrozen", ...). PrusaSlicer
    SLA bundles don't carry vendor metadata at the file layer the way
    FDM multi-section INIs do.
    """
    root = Path(assets_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"UVtools assets root does not exist: {root}")

    bundle = ProfileBundle(slicer="prusa", slicer_version="uvtools-bundle")

    # Walk printer/<Name>.ini → translate_printer
    printer_dir = root / "printer"
    if printer_dir.is_dir():
        for ini in sorted(printer_dir.glob("*.ini")):
            data = _load_per_file_ini(ini)
            if not data:
                continue
            vendor = data.get("printer_vendor") or ini.stem.split(" ", 1)[0]
            resolved = ResolvedProfile(
                type="printer",
                name=ini.stem,
                vendor=vendor,
                data=data,
                inherits_chain=[],
            )
            try:
                printer = translate_printer(resolved)
            except Exception:
                # Skip the rare profile that fails strict-schema validation
                # (incomplete display fields, etc.) rather than failing the
                # whole bundle. The profile loss is logged at the consumer
                # layer; bundle-level resilience is more valuable than a
                # 100%-or-nothing parse.
                continue
            bundle.printers[printer.id] = printer

    # `sla_print/` and `sla_material/` directories are intentionally
    # skipped at the bundle layer for V1: the existing
    # `translate_process` / `translate_filament` are FDM-shaped and
    # SLA INIs miss keys those validators require (perimeter_count,
    # nozzle_temp_c, etc.). Consumers synthesise a Generic Std Resin
    # process + filament at recipe-build time, which is the same
    # pragmatic shortcut Admeshio's `profile_catalog` already takes
    # for resin recipes today. A future SLA-aware translate_process /
    # translate_filament can lift these dirs without breaking the
    # printer-only consumer surface.

    return bundle


__all__ = [
    "load_prusa",
    "load_prusa_ini",
    "load_uvtools_assets",
    "translate_filament",
    "translate_printer",
    "translate_process",
]

# Satisfy mypy for the `_normalise_type` import — it's exposed through
# the loader module's package-local helper but we only need the public
# surface in this file. The explicit reference keeps it in the import
# graph so refactors that rename it break loudly here, not in the wild.
_ = _normalise_type
