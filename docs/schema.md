# Canonical schema reference

This document specifies the vendor-neutral print-profile schema the library
produces. The authoritative source is [`src/slicer_profile_bridge/schema.py`](../src/slicer_profile_bridge/schema.py) — pydantic models live
there, this file is the human-readable companion.

## Design principles

- **SI units everywhere.** mm, mm/s, °C, g/cm³. No opinionated unit
  conversion at consumption time.
- **Required fields are the minimum every translator must emit.** If one
  vendor legitimately omits a field, it's Optional. Translators that have
  the data still fill it in.
- **No mechanical / FEA data in the canonical.** Young's modulus, yield
  strength, layer adhesion factor, etc. are domain overlays a consumer
  attaches based on `FilamentCategory`. Keeping them out of this schema
  means the library doesn't have to pick a source of mechanical truth.
- **Strict by default.** `extra="forbid"` on every model — unknown fields
  fail at load time rather than silently being ignored.
- **Traceability is not optional.** Every top-level object carries a
  `source: SourceMetadata` block with the slicer name, version, vendor
  directory, original ID, and the `inherits` chain that produced the
  effective values.

## Three top-level objects

| Object | Purpose |
|---|---|
| `CanonicalPrinter` | Hardware envelope. What the machine can do. |
| `CanonicalFilament` | Material behaviour. What this filament / resin wants. |
| `CanonicalProcess` | Slicing choices. How the slicer decided to print. |

A `CanonicalRecipe` bundles one of each plus sits on top of
`ProfileBundle`, which is an indexed view of an entire slicer install.

## Enums (current set)

- `PrinterTechnology`: `fdm`, `resin_msla`, `sls`, `mjf`
- `Kinematics`: `cartesian`, `corexy`, `delta`, `idex`, `unknown`
- `RetractionType`: `direct_drive`, `bowden`, `unknown`
- `FilamentCategory`: `pla`, `petg`, `abs`, `asa`, `tpu`, `pa`, `pc`,
  `hips`, `pva`, `pvb`, `pet_cf`, `pla_cf`, `std_resin`,
  `water_washable_resin`, `tough_resin`, `castable_resin`,
  `pa12_powder`, `other`
- `InfillPattern`: `grid`, `line`, `triangles`, `cubic`, `gyroid`,
  `honeycomb`, `hilbert`, `concentric`, `adaptive_cubic`, `lightning`,
  `other`
- `SupportPattern`: `grid`, `snug`, `tree`, `organic`, `unknown`
- `SeamPosition`: `nearest`, `aligned`, `back`, `random`, `unknown`

## CanonicalPrinter

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `str` | ✓ | Namespaced, e.g. `"BBL/X1C_0.4"`. |
| `name` | `str` | ✓ | Human-readable name. |
| `vendor` | `str` | ✓ | Vendor directory name from the slicer. |
| `technology` | `PrinterTechnology` | ✓ | |
| `build_volume_mm` | `BuildVolumeMm` | ✓ | `{x, y, z}` in mm. |
| `firmware` | `str?` | | e.g. `"marlin"`, `"klipper"`. |
| `kinematics` | `Kinematics` | default `unknown` | |
| `enclosure` | `bool` | default `false` | |
| `nozzle_diameter_mm` | `float?` | FDM | |
| `retraction_type` | `RetractionType` | default `unknown` | Printer-level hint; a filament can still override. |
| `max_feedrate_mm_s` | `AxisSpeeds?` | | Per-axis max feedrate. |
| `max_accel_mm_s2` | `AxisSpeeds?` | | Per-axis max acceleration. |
| `pixel_size_mm` | `float?` | Resin | |
| `lcd_resolution_px` | `tuple[int, int]?` | Resin | |
| `z_step_mm` | `float?` | Resin | |
| `source` | `SourceMetadata` | ✓ | Traceability. |

## CanonicalFilament

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `str` | ✓ | |
| `name` | `str` | ✓ | |
| `vendor` | `str` | ✓ | |
| `category` | `FilamentCategory` | ✓ | Normalised family. |
| `raw_category` | `str?` | | Original vendor category (e.g. `"PLA Silk"`). |
| `nozzle_temp_c` | `NozzleTemps?` | FDM | |
| `bed_temp_c` | `BedTemps?` | FDM | |
| `flow_ratio` | `float?` | FDM | 0 < x ≤ 2. |
| `density_g_cm3` | `float?` | | |
| `shrinkage_pct` | `float?` | | |
| `bridge_flow` | `float?` | | |
| `max_volumetric_speed_mm3_s` | `float?` | | |
| `cooling` | `CoolingSettings?` | FDM | |
| `retraction` | `RetractionSettings?` | | Filament-specific overrides. |
| `drying_required` | `bool` | default `false` | |
| `enclosure_required` | `bool` | default `false` | |
| `bed_adhesion_rating` | `int?` | | 0–500 scale, higher = sticks harder. |
| `exposure_time_s` | `float?` | Resin | |
| `bottom_exposure_time_s` | `float?` | Resin | |
| `bottom_layer_count` | `int?` | Resin | |
| `source` | `SourceMetadata` | ✓ | |

## CanonicalProcess

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `str` | ✓ | |
| `name` | `str` | ✓ | |
| `compatible_printers` | `list[str]` | | Glob patterns or IDs. |
| `compatible_filaments` | `list[str]` | | |
| `layer_height_mm` | `float` | ✓ | |
| `first_layer_height_mm` | `float` | ✓ | |
| `wall_count` | `int?` | | |
| `top_shell_layers` | `int?` | | |
| `bottom_shell_layers` | `int?` | | |
| `top_shell_thickness_mm` | `float?` | | |
| `bottom_shell_thickness_mm` | `float?` | | |
| `infill_pct` | `int?` | | 0–100. |
| `infill_pattern` | `InfillPattern` | default `other` | |
| `raw_infill_pattern` | `str?` | | Original vendor name when mapped to `other`. |
| `speed_mm_s` | `ProcessSpeeds?` | | |
| `support` | `SupportSettings?` | | |
| `adhesion` | `AdhesionSettings?` | | Skirt / brim / raft. |
| `seam_position` | `SeamPosition` | default `unknown` | |
| `source` | `SourceMetadata` | ✓ | |

## SourceMetadata

| Field | Type | Required | Notes |
|---|---|---|---|
| `slicer` | `str` | ✓ | `"orca"`, `"prusa"`, `"bambu"`. |
| `slicer_version` | `str?` | | |
| `vendor` | `str?` | | Vendor subdir in the slicer. |
| `source_id` | `str?` | | Original ID inside the source slicer. |
| `inherits_chain` | `list[str]` | | Resolved parents, root last. |

## Versioning

The canonical schema follows semver. `0.x` means the shape may still
change between minor releases as edge-case fields surface during the
Prusa / Bambu translator work. Once the third translator ships cleanly,
`1.0` locks the shape.

Every breaking change ships with a migration note in the changelog.
