# Test fixtures — attribution

Profile JSON files under `tests/fixtures/orca/` are unmodified copies of
profiles distributed with [OrcaSlicer](https://github.com/SoftFever/OrcaSlicer),
licensed under **AGPL-3.0**.

Included fixtures (Bambu Lab X1 Carbon family):

| Path | Source |
|---|---|
| `orca/BBL/machine/Bambu Lab X1 Carbon 0.4 nozzle.json` | `OrcaSlicer/resources/profiles/BBL/machine/` |
| `orca/BBL/machine/fdm_bbl_3dp_001_common.json` | same |
| `orca/BBL/machine/fdm_machine_common.json` | same |
| `orca/BBL/filament/Bambu PLA Basic @BBL X1C.json` | `OrcaSlicer/resources/profiles/BBL/filament/` |
| `orca/BBL/filament/Bambu PLA Basic @base.json` | same |
| `orca/BBL/filament/fdm_filament_pla.json` | same |
| `orca/BBL/filament/fdm_filament_common.json` | same |
| `orca/BBL/process/0.20mm Standard @BBL X1C.json` | `OrcaSlicer/resources/profiles/BBL/process/` |
| `orca/BBL/process/fdm_process_single_0.20.json` | same |
| `orca/BBL/process/fdm_process_single_common.json` | same |
| `orca/BBL/process/fdm_process_common.json` | same |

The fixtures are present only to give the translator test suite real-world
inputs to parse; they are not redistributed as part of the library itself.

**License**: AGPL-3.0 for the profile files. See the OrcaSlicer repository
LICENSE for the full terms. The rest of this project is Apache-2.0 —
these fixtures are the sole AGPL-licensed content in the tree.
