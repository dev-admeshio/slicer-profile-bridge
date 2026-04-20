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
    "petg": FilamentCategory.PETG,
    "pet": FilamentCategory.PETG,
    "abs": FilamentCategory.ABS,
    "asa": FilamentCategory.ASA,
    "flex": FilamentCategory.TPU,
    "tpu": FilamentCategory.TPU,
    "pa": FilamentCategory.PA,
    "nylon": FilamentCategory.PA,
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
    for prefix, cat in _FILAMENT_CATEGORY_MAP.items():
        if key.startswith(prefix + " ") or key.startswith(prefix + "-"):
            return cat
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


# ── Translators ───────────────────────────────────────────────────────


def translate_printer(resolved: ResolvedProfile) -> CanonicalPrinter:
    data = resolved.data
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

    source = SourceMetadata(
        slicer="prusa",
        vendor=resolved.vendor,
        source_id=resolved.name,
        inherits_chain=resolved.inherits_chain,
    )

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


__all__ = [
    "load_prusa",
    "load_prusa_ini",
    "translate_filament",
    "translate_printer",
    "translate_process",
]

# Satisfy mypy for the `_normalise_type` import — it's exposed through
# the loader module's package-local helper but we only need the public
# surface in this file. The explicit reference keeps it in the import
# graph so refactors that rename it break loudly here, not in the wild.
_ = _normalise_type
