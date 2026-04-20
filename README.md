# slicer-profile-bridge

A **vendor-neutral 3D print profile schema** plus translators that convert
OrcaSlicer, PrusaSlicer, and BambuStudio profiles into that canonical form.

Use it as a library to read whatever profiles a user has installed in any
supported slicer, expose them through one stable shape, and avoid
reinventing per-vendor parsing in every downstream tool.

**Status**: alpha — schema under active design. Phase 1 (Orca) in progress.

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
| OrcaSlicer | Phase 1 — in progress | Primary target |
| PrusaSlicer | Phase 2 — planned | Handles both JSON and legacy INI |
| BambuStudio | Phase 3 — planned | Orca fork, near-identical schema |

Adding a new translator: implement `translate_printer`, `translate_filament`,
and `translate_process` functions that return canonical models. See
[`docs/contributing.md`](docs/contributing.md) (coming) for details.

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
