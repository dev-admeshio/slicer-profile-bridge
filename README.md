# slicer-profile-bridge

A **vendor-neutral 3D print profile schema** plus translators that convert
OrcaSlicer, PrusaSlicer, and BambuStudio profiles into that canonical form.

Use it as a library to read whatever profiles a user has installed in any
supported slicer, expose them through one stable shape, and avoid
reinventing per-vendor parsing in every downstream tool.

**Status**: v0.3.0 — three translators shipped (Orca / Prusa / Bambu),
daily upstream sync workflow wired up, `raw_data` escape hatch for any
vendor field the canonical schema doesn't promote. v0.3 adds motion
tuning (PA / jerk / junction-deviation), custom start/end gcode,
filament diameter, and nozzle-type classification — the fields an
audit needs to verify a user's gcode against a simulated slice.
Schema stays `0.x` until a third external consumer stress-tests the shape.

## Why this exists

Pre-print tooling (audit, analysis, risk reporting) keeps repeating the same
plumbing: parse Slicer X's profile files, resolve the `inherits` chain, map
the vendor's field names to something internal, repeat for Slicer Y. This
library does it once.

If you are building anything that needs to *read* a real printer/material
profile — a mesh auditor, a farm scheduler, a cost estimator, a compliance
report generator — start here instead of writing a parser from scratch.

Admeshio uses it to power the Readiness (GRI-R) checks in its pre-print
audit. The library itself is standalone and has no dependency on Admeshio.

## Canonical schema

Three top-level objects:

| Object | Covers | Sources from |
|---|---|---|
| `Printer` | Hardware capability: build volume, nozzle or pixel, kinematics, firmware, enclosure | `printer/*.json` |
| `Filament` | Material behaviour: temps, flow, cooling, density, shrinkage | `filament/*.json` |
| `Process` | Slicing choices: layer height, walls, infill, speeds, supports, adhesion | `process/*.json` |

A `Recipe` composes one of each into a fully-resolved bundle that includes
the source slicer name and the original profile IDs for traceability.

Full field reference lives in [`docs/schema.md`](docs/schema.md).

## Installation

```bash
pip install slicer-profile-bridge
```

From source:

```bash
git clone https://github.com/dev-admeshio/slicer-profile-bridge.git
cd slicer-profile-bridge
pip install -e ".[dev]"
```

## Usage

### Load every profile from a slicer install

```python
from slicer_profile_bridge import load_orca

# Point at the OrcaSlicer resources/profiles directory.
bundle = load_orca("/path/to/OrcaSlicer/resources/profiles")

print(len(bundle.printers), "printers")
print(len(bundle.filaments), "filaments")
print(len(bundle.processes), "processes")
```

### Compose a recipe

```python
recipe = bundle.compose(
    printer_id="BBL/Bambu_Lab_X1_Carbon_0.4",
    filament_id="BBL/Bambu_PLA_Basic",
    process_id="BBL/0.20mm_Standard_X1C",
)

# recipe is a CanonicalRecipe — strongly typed, pydantic v2
print(recipe.printer.build_volume_mm)     # {"x": 256, "y": 256, "z": 256}
print(recipe.filament.nozzle_temp_c.normal)  # 220.0
print(recipe.process.layer_height_mm)     # 0.2
```

### Translate one file

```python
from slicer_profile_bridge import translate_file

profile = translate_file("orca", "/path/to/Ender3v2.json")
# profile is CanonicalPrinter | CanonicalFilament | CanonicalProcess
```

### CLI

```bash
spb translate --from orca --in ./profile.json --out ./profile.canonical.json
spb list --from orca --root /path/to/OrcaSlicer/resources/profiles
```

## Supported slicers

| Slicer | Status | Notes |
|---|---|---|
| OrcaSlicer | ✓ shipped (0.1.0) | Primary target — full field mapping. |
| PrusaSlicer | ✓ shipped (0.1.0) | Multi-section INI + multi-parent inheritance. |
| BambuStudio | ✓ shipped (0.1.0) | Orca fork — shares the translator, differs only in the source label. |
| Cura | planned | Welcome community translator PRs. |
| Lychee / ChiTuBox | planned | Resin-focused; needs dedicated fixture corpus first. |

Adding a new translator: see [`src/slicer_profile_bridge/translators/README.md`](src/slicer_profile_bridge/translators/README.md)
for the contract and conventions. Every translator ships three
per-profile functions and one loader, with fixtures in
`tests/fixtures/<slicer>/`.

## Daily sync workflow

`.github/workflows/sync-upstream.yml` runs at 06:00 UTC daily,
shallow-clones the three upstream slicer repos, translates every
profile into a single `canonical-profiles.json` (~36 MB), and publishes
to the `profiles-data` branch and as a GitHub release artifact.
Downstream consumers pin a tag or pull the latest commit on
`profiles-data` to get fresh vendor data without running the bridge
themselves.

## Who uses this

- [Admeshio](https://admeshio.com) — pre-print audit platform. Backend
  loads the canonical snapshot at boot to power its printer / filament
  / process dropdown, maps user selections to Admeshio's engine recipes
  via a substring table, falls back to generic FDM when no first-party
  recipe covers the chosen printer. Integration surface:
  `interfaces/web/backend/app/services/profile_catalog.py`.

Using this in your project? Open an issue or PR — helps us scope the
1.0 schema lock around real-world needs.

## Profile sources

At runtime the library reads from your local slicer install:

- OrcaSlicer: `<install>/resources/profiles/<Vendor>/`
- PrusaSlicer: `<install>/resources/profiles/<Vendor>/`
- BambuStudio: `<install>/resources/profiles/BBL/`

For tests and development, vendor profile fixtures (a small representative
subset) live in `tests/fixtures/` with original attribution preserved.

## License

Apache-2.0. The canonical schema and the translation code are permissive.
Vendor profile JSON / INI files read at runtime retain their original
license (typically AGPL-3.0 when bundled with the slicer distribution).
This library does not modify or redistribute them.

## Contributing

Issues and PRs welcome. Please add fixtures alongside new translator work
so behaviour is testable against real-world profiles, not just synthetic
ones. Run `pytest` and `ruff check` before submitting.
