"""Canonical 3D print profile schema.

Design notes:
  - Units are SI throughout: mm, mm/s, °C, g/cm³. No imperial fallback.
  - Fields are Optional only when "slicer may legitimately omit this" is
    true for *at least one* supported slicer. Required fields are the ones
    we assert every translator must produce.
  - No material-mechanics fields (Young's modulus, yield strength, etc.):
    those are domain-specific overlays a consumer like Admeshio adds on
    top. Keeping them out of the canonical schema means every tool can
    agree on the shape without arguing about which mechanical dataset is
    authoritative.
  - `source_slicer` + `source_version` + `source_ids` travel with the
    output so audits can always cite exactly which vendor file a value
    came from.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, PositiveFloat


class _StrictModel(BaseModel):
    """Base for every canonical model.

    `extra="forbid"` catches translator bugs early — a mapping that tries
    to emit an unrecognised field fails loudly instead of silently adding
    stray data that downstream consumers then start depending on.
    """

    model_config = ConfigDict(extra="forbid", frozen=False, validate_assignment=True)


# ── Enums ──────────────────────────────────────────────────────────────


class PrinterTechnology(str, Enum):
    """Printing process the printer drives.

    `resin_msla` covers the common LCD / photon family. Dedicated values
    for DLP, SLA-laser, jetting, etc. can be added when those translators
    land; keeping the set tight today avoids speculative enumeration.
    """

    FDM = "fdm"
    RESIN_MSLA = "resin_msla"
    SLS = "sls"
    MJF = "mjf"


class Kinematics(str, Enum):
    CARTESIAN = "cartesian"
    COREXY = "corexy"
    DELTA = "delta"
    IDEX = "idex"
    UNKNOWN = "unknown"


class RetractionType(str, Enum):
    DIRECT_DRIVE = "direct_drive"
    BOWDEN = "bowden"
    UNKNOWN = "unknown"


class FilamentCategory(str, Enum):
    """Normalised material family.

    The raw `category` string from a slicer ("PLA+", "PLA Silk", "PLA CF")
    always collapses to one of these buckets. Downstream consumers that
    need finer-grained distinctions (e.g. fibre content) can inspect the
    original name via `CanonicalFilament.raw_category`.
    """

    PLA = "pla"
    PETG = "petg"
    ABS = "abs"
    ASA = "asa"
    TPU = "tpu"
    PA = "pa"          # nylon family (PA6, PA12 if not powder-bed)
    PC = "pc"          # polycarbonate
    HIPS = "hips"
    PVA = "pva"
    PVB = "pvb"
    PET_CF = "pet_cf"
    PLA_CF = "pla_cf"
    STD_RESIN = "std_resin"
    WATER_WASHABLE_RESIN = "water_washable_resin"
    TOUGH_RESIN = "tough_resin"
    CASTABLE_RESIN = "castable_resin"
    PA12_POWDER = "pa12_powder"    # SLS / MJF
    OTHER = "other"


class InfillPattern(str, Enum):
    """Common infill patterns. Non-exhaustive by design; vendor-specific
    exotics fall back to `OTHER` and the original name is preserved in
    `CanonicalProcess.raw_infill_pattern`.
    """

    GRID = "grid"
    LINE = "line"
    TRIANGLES = "triangles"
    CUBIC = "cubic"
    GYROID = "gyroid"
    HONEYCOMB = "honeycomb"
    HILBERT = "hilbert"
    CONCENTRIC = "concentric"
    ADAPTIVE_CUBIC = "adaptive_cubic"
    LIGHTNING = "lightning"
    OTHER = "other"


class SupportPattern(str, Enum):
    GRID = "grid"
    SNUG = "snug"
    TREE = "tree"
    ORGANIC = "organic"
    UNKNOWN = "unknown"


class SeamPosition(str, Enum):
    NEAREST = "nearest"
    ALIGNED = "aligned"
    BACK = "back"
    RANDOM = "random"
    UNKNOWN = "unknown"


# ── Nested value objects ───────────────────────────────────────────────


class BuildVolumeMm(_StrictModel):
    """Usable build volume in millimetres, origin at the bed.

    Represents the *reachable* area, not the bed dimensions — on a
    machine where the head cannot access every bed corner the canonical
    value is the smaller rectangle.
    """

    x: PositiveFloat
    y: PositiveFloat
    z: PositiveFloat


class AxisSpeeds(_StrictModel):
    """Per-axis rates. Missing axes are valid when the slicer profile
    doesn't declare them; defaults live with the consumer, not here.
    """

    x: PositiveFloat | None = None
    y: PositiveFloat | None = None
    z: PositiveFloat | None = None
    e: PositiveFloat | None = None


class NozzleTemps(_StrictModel):
    """Nozzle / extrusion temperature envelope. `normal` is required for
    FDM filaments; `first_layer` defaults to the same if a slicer doesn't
    split them.
    """

    normal: PositiveFloat
    first_layer: PositiveFloat
    range_min: PositiveFloat | None = None
    range_max: PositiveFloat | None = None


class BedTemps(_StrictModel):
    normal: NonNegativeFloat
    first_layer: NonNegativeFloat


class CoolingSettings(_StrictModel):
    """Fan / layer-time cooling envelope.

    `fan_min_pct` and `fan_max_pct` describe the range the slicer will
    ramp the part-cooling fan through. `fan_cooling_layer_time_s` is the
    layer-time threshold under which the fan ramps toward max.
    """

    fan_min_pct: Annotated[int, Field(ge=0, le=100)]
    fan_max_pct: Annotated[int, Field(ge=0, le=100)]
    fan_cooling_layer_time_s: NonNegativeFloat
    disable_fan_first_layers: Annotated[int, Field(ge=0)]
    overhang_fan_speed_pct: Annotated[int, Field(ge=0, le=100)] | None = None
    overhang_fan_threshold_pct: Annotated[int, Field(ge=0, le=100)] | None = None
    bridge_fan_speed_pct: Annotated[int, Field(ge=0, le=100)] | None = None


class RetractionSettings(_StrictModel):
    length_mm: NonNegativeFloat
    speed_mm_s: PositiveFloat
    deretraction_speed_mm_s: PositiveFloat | None = None
    z_hop_mm: NonNegativeFloat | None = None


class ProcessSpeeds(_StrictModel):
    """Slicing speeds in mm/s. Present fields are the common denominators
    across Orca / Prusa / Bambu; vendor-specific speeds (e.g. ironing,
    gap-fill) are not surfaced here to keep the canonical lean.
    """

    perimeter: PositiveFloat | None = None
    external_perimeter: PositiveFloat | None = None
    infill: PositiveFloat | None = None
    solid_infill: PositiveFloat | None = None
    top_solid_infill: PositiveFloat | None = None
    support: PositiveFloat | None = None
    travel: PositiveFloat | None = None
    first_layer: PositiveFloat | None = None
    first_layer_infill: PositiveFloat | None = None


class SupportSettings(_StrictModel):
    enabled: bool
    threshold_angle_deg: Annotated[float, Field(ge=0, le=90)] | None = None
    pattern: SupportPattern = SupportPattern.UNKNOWN
    z_distance_mm: NonNegativeFloat | None = None
    interface_layers: Annotated[int, Field(ge=0)] | None = None
    on_build_plate_only: bool = False


class AdhesionSettings(_StrictModel):
    skirt_loops: Annotated[int, Field(ge=0)] | None = None
    skirt_distance_mm: NonNegativeFloat | None = None
    brim_width_mm: NonNegativeFloat | None = None
    raft_layers: Annotated[int, Field(ge=0)] | None = None


class SourceMetadata(_StrictModel):
    """Traceability: where did this canonical profile come from?"""

    slicer: str                          # "orca", "prusa", "bambu"
    slicer_version: str | None = None
    vendor: str | None = None            # "BBL", "Creality", ...
    source_id: str | None = None         # original profile id inside the slicer
    inherits_chain: list[str] = Field(default_factory=list)


# ── Top-level canonical models ────────────────────────────────────────


class CanonicalPrinter(_StrictModel):
    """A printer's hardware envelope.

    FDM machines populate `nozzle_diameter_mm`; resin machines populate
    `pixel_size_mm` + `lcd_resolution_px`; SLS / MJF populate neither.
    Validators don't force this because a few cross-technology machines
    (e.g. tool-changers with a laser + FFF) break any rule we'd write.
    """

    id: str
    name: str
    vendor: str
    technology: PrinterTechnology
    build_volume_mm: BuildVolumeMm
    firmware: str | None = None
    kinematics: Kinematics = Kinematics.UNKNOWN
    enclosure: bool = False

    # FDM-specific
    nozzle_diameter_mm: PositiveFloat | None = None
    retraction_type: RetractionType = RetractionType.UNKNOWN
    max_feedrate_mm_s: AxisSpeeds | None = None
    max_accel_mm_s2: AxisSpeeds | None = None

    # Resin-specific
    pixel_size_mm: PositiveFloat | None = None
    lcd_resolution_px: tuple[int, int] | None = None
    z_step_mm: PositiveFloat | None = None

    source: SourceMetadata


class CanonicalFilament(_StrictModel):
    """A material as configured for extrusion or exposure."""

    id: str
    name: str
    vendor: str
    category: FilamentCategory
    raw_category: str | None = None       # "PLA Silk", "PLA+", etc.

    # FDM thermal envelope
    nozzle_temp_c: NozzleTemps | None = None
    bed_temp_c: BedTemps | None = None

    # FDM flow + physical
    flow_ratio: Annotated[float, Field(gt=0, le=2)] | None = None
    density_g_cm3: PositiveFloat | None = None
    shrinkage_pct: float | None = None
    bridge_flow: Annotated[float, Field(gt=0, le=2)] | None = None
    max_volumetric_speed_mm3_s: PositiveFloat | None = None

    # FDM handling hints
    cooling: CoolingSettings | None = None
    retraction: RetractionSettings | None = None
    drying_required: bool = False
    enclosure_required: bool = False
    bed_adhesion_rating: Annotated[int, Field(ge=0, le=500)] | None = None

    # Resin exposure
    exposure_time_s: PositiveFloat | None = None
    bottom_exposure_time_s: PositiveFloat | None = None
    bottom_layer_count: Annotated[int, Field(ge=0)] | None = None

    source: SourceMetadata


class CanonicalProcess(_StrictModel):
    """Slicing settings that a vendor profile pre-tunes for a
    printer+filament combo.

    A process doesn't hard-bind to one printer / filament — most profiles
    list a compatibility glob. We surface those raw so the consumer can
    decide how strict to be when composing recipes.
    """

    id: str
    name: str
    compatible_printers: list[str] = Field(default_factory=list)
    compatible_filaments: list[str] = Field(default_factory=list)

    # Layer geometry
    layer_height_mm: PositiveFloat
    first_layer_height_mm: PositiveFloat

    # Shell structure
    wall_count: Annotated[int, Field(ge=0)] | None = None
    top_shell_layers: Annotated[int, Field(ge=0)] | None = None
    bottom_shell_layers: Annotated[int, Field(ge=0)] | None = None
    top_shell_thickness_mm: NonNegativeFloat | None = None
    bottom_shell_thickness_mm: NonNegativeFloat | None = None

    # Infill
    infill_pct: Annotated[int, Field(ge=0, le=100)] | None = None
    infill_pattern: InfillPattern = InfillPattern.OTHER
    raw_infill_pattern: str | None = None

    # Motion
    speed_mm_s: ProcessSpeeds | None = None

    # Support structure
    support: SupportSettings | None = None

    # Bed adhesion / first layer
    adhesion: AdhesionSettings | None = None

    # Seam
    seam_position: SeamPosition = SeamPosition.UNKNOWN

    source: SourceMetadata


class CanonicalRecipe(_StrictModel):
    """A fully-resolved print setup: one printer + one filament + one process.

    This is what a downstream tool actually consumes — the three canonical
    models pre-composed, with the original source IDs carried through each
    nested `source` block for auditability.
    """

    printer: CanonicalPrinter
    filament: CanonicalFilament
    process: CanonicalProcess


# ── Bundle: everything a slicer install has to offer ──────────────────


class ProfileBundle(_StrictModel):
    """Indexed view of an entire slicer install.

    Maps from canonical ID to the translated model. IDs keep the vendor
    directory and the original filename so cross-referencing back to the
    source file is trivial.
    """

    slicer: str
    slicer_version: str | None = None
    printers: dict[str, CanonicalPrinter] = Field(default_factory=dict)
    filaments: dict[str, CanonicalFilament] = Field(default_factory=dict)
    processes: dict[str, CanonicalProcess] = Field(default_factory=dict)

    def compose(
        self,
        printer_id: str,
        filament_id: str,
        process_id: str,
    ) -> CanonicalRecipe:
        """Compose a recipe from three IDs.

        Raises `KeyError` (with the missing ID in the message) when any
        of the three lookups fails — caller is expected to validate the
        selection against `ProfileBundle.{printers,filaments,processes}`
        before calling.
        """
        try:
            printer = self.printers[printer_id]
        except KeyError as exc:
            raise KeyError(f"printer not found: {printer_id!r}") from exc
        try:
            filament = self.filaments[filament_id]
        except KeyError as exc:
            raise KeyError(f"filament not found: {filament_id!r}") from exc
        try:
            process = self.processes[process_id]
        except KeyError as exc:
            raise KeyError(f"process not found: {process_id!r}") from exc
        return CanonicalRecipe(printer=printer, filament=filament, process=process)
